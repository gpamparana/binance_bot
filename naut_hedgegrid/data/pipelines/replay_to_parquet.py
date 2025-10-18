"""
Main data pipeline orchestrator.

Coordinates data fetching, normalization, conversion to Nautilus types,
and writing to ParquetDataCatalog with proper partitioning.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import typer
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from naut_hedgegrid.data.pipelines.normalizer import (
    normalize_funding_rates,
    normalize_mark_prices,
    normalize_trades,
)
from naut_hedgegrid.data.schemas import convert_dataframe_to_nautilus
from naut_hedgegrid.data.sources.base import DataSource
from naut_hedgegrid.data.sources.binance_source import BinanceDataSource
from naut_hedgegrid.data.sources.csv_source import CSVDataSource
from naut_hedgegrid.data.sources.tardis_source import TardisDataSource
from naut_hedgegrid.data.sources.websocket_source import WebSocketDataSource

logger = logging.getLogger(__name__)
console = Console()


def create_source(source_type: str, config: dict[str, Any]) -> DataSource:
    """
    Factory function to create data source instances.

    Parameters
    ----------
    source_type : str
        Type of data source ("binance", "tardis", "csv", "websocket")
    config : dict
        Configuration for the data source

    Returns
    -------
    DataSource
        Instantiated data source

    Raises
    ------
    ValueError
        If source_type is unknown

    """
    sources = {
        "binance": BinanceDataSource,
        "tardis": TardisDataSource,
        "csv": CSVDataSource,
        "websocket": WebSocketDataSource,
    }

    if source_type not in sources:
        raise ValueError(
            f"Unknown source type: {source_type}. " f"Available: {list(sources.keys())}"
        )

    source_class = sources[source_type]

    # Create instance based on source type
    if source_type == "binance":
        return source_class(
            base_url=config.get("base_url", "https://fapi.binance.com"),
            rate_limit_delay=config.get("rate_limit_delay", 0.5),  # 2 req/sec default - conservative to avoid 429s
            request_limit=config.get("request_limit", 1000),
            testnet=config.get("testnet", False),
        )
    if source_type == "tardis":
        return source_class(
            api_key=config.get("api_key"),
            exchange=config.get("exchange", "binance-futures"),
            cache_dir=config.get("cache_dir"),
        )
    if source_type in ("csv", "websocket"):
        return source_class(
            config=config.get("files", {}),
            base_path=config.get("base_path", "."),
        )
    return source_class(**config)


async def fetch_data(
    source: DataSource,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    data_types: list[str],
) -> dict[str, pd.DataFrame]:
    """
    Fetch data from source for specified date range and types.

    Parameters
    ----------
    source : DataSource
        Data source to fetch from
    symbol : str
        Trading symbol
    start_date : datetime
        Start date (inclusive)
    end_date : datetime
        End date (exclusive)
    data_types : list[str]
        Data types to fetch ("trades", "mark", "funding")

    Returns
    -------
    dict[str, pd.DataFrame]
        Dictionary mapping data type to DataFrame

    """
    data = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching data...", total=len(data_types))

        for data_type in data_types:
            try:
                if data_type == "trades":
                    df = await source.fetch_trades(symbol, start_date, end_date)
                    data["trades"] = df
                    logger.info(f"Fetched {len(df):,} trades")

                elif data_type == "mark":
                    df = await source.fetch_mark_prices(symbol, start_date, end_date)
                    data["mark"] = df
                    logger.info(f"Fetched {len(df):,} mark prices")

                elif data_type == "funding":
                    df = await source.fetch_funding_rates(symbol, start_date, end_date)
                    data["funding"] = df
                    logger.info(f"Fetched {len(df):,} funding rates")

                else:
                    logger.warning(f"Unknown data type: {data_type}")

            except Exception as e:
                logger.error(f"Failed to fetch {data_type}: {e}")
                # Continue with other data types
                data[data_type] = pd.DataFrame()

            progress.advance(task)

    return data


def normalize_data(data: dict[str, pd.DataFrame], source_type: str) -> dict[str, pd.DataFrame]:
    """
    Normalize all data to standard schemas.

    Parameters
    ----------
    data : dict
        Raw data by type
    source_type : str
        Source type for logging

    Returns
    -------
    dict
        Normalized data by type

    """
    normalized = {}

    if "trades" in data and not data["trades"].empty:
        normalized["trades"] = normalize_trades(data["trades"], source_type)

    if "mark" in data and not data["mark"].empty:
        normalized["mark"] = normalize_mark_prices(data["mark"], source_type)

    if "funding" in data and not data["funding"].empty:
        normalized["funding"] = normalize_funding_rates(data["funding"], source_type)

    return normalized


def create_instrument(symbol: str, exchange: str = "BINANCE") -> CryptoPerpetual:
    """
    Create Nautilus instrument definition.

    Parameters
    ----------
    symbol : str
        Trading symbol (e.g., "BTCUSDT")
    exchange : str, default "BINANCE"
        Exchange name

    Returns
    -------
    CryptoPerpetual
        Nautilus perpetual futures instrument

    """
    # Parse symbol (assume USDT-margined)
    base_code = symbol.replace("USDT", "")
    quote_code = "USDT"

    # Create Currency objects
    base_currency = Currency.from_str(base_code)
    quote_currency = Currency.from_str(quote_code)

    instrument_id = InstrumentId.from_str(f"{symbol}-PERP.{exchange}")

    # Create instrument with typical Binance parameters
    instrument = CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=quote_currency,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("10000.0"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=Money(1_000_000, quote_currency),
        min_notional=Money(10, quote_currency),
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("0.01"),
        margin_maint=Decimal("0.005"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        ts_event=0,
        ts_init=0,
    )

    return instrument


def write_to_catalog(
    data: dict[str, pd.DataFrame],
    symbol: str,
    output_path: str,
    exchange: str = "BINANCE",
) -> None:
    """
    Write normalized data to ParquetDataCatalog.

    Parameters
    ----------
    data : dict
        Normalized data by type
    symbol : str
        Trading symbol
    output_path : str
        Output catalog path
    exchange : str, default "BINANCE"
        Exchange name

    """
    catalog = ParquetDataCatalog(path=output_path)
    instrument_id = InstrumentId.from_str(f"{symbol}-PERP.{exchange}")

    # Create and write instrument definition
    instrument = create_instrument(symbol, exchange)
    catalog.write_data([instrument])
    console.print(f"[green]✓[/green] Wrote instrument: {instrument_id}")

    # Write trade ticks
    if "trades" in data and not data["trades"].empty:
        trade_ticks = convert_dataframe_to_nautilus(data["trades"], "trade", instrument_id)
        catalog.write_data(trade_ticks)
        console.print(f"[green]✓[/green] Wrote {len(trade_ticks):,} trade ticks")

    # Write mark prices (as generic data - Nautilus handles custom data types)
    if "mark" in data and not data["mark"].empty:
        # For now, we'll store mark prices as a custom parquet file
        # since Nautilus doesn't have a built-in MarkPrice type
        mark_data = data["mark"].copy()
        mark_file = Path(output_path) / f"{instrument_id.value}" / "mark_price.parquet"
        mark_file.parent.mkdir(parents=True, exist_ok=True)
        mark_data.to_parquet(mark_file, index=False)
        console.print(f"[green]✓[/green] Wrote {len(mark_data):,} mark prices")

    # Write funding rates (as custom parquet file)
    if "funding" in data and not data["funding"].empty:
        funding_data = data["funding"].copy()
        funding_file = Path(output_path) / f"{instrument_id.value}" / "funding_rate.parquet"
        funding_file.parent.mkdir(parents=True, exist_ok=True)
        funding_data.to_parquet(funding_file, index=False)
        console.print(f"[green]✓[/green] Wrote {len(funding_data):,} funding rates")


async def run_pipeline(
    source_type: str,
    symbol: str,
    start_date: str,
    end_date: str,
    output_path: str,
    data_types: list[str] = ["trades", "mark", "funding"],
    source_config: dict | None = None,
    exchange: str = "BINANCE",
) -> None:
    """
    Execute complete data pipeline.

    This orchestrates the entire process:
    1. Initialize data source
    2. Fetch raw data for date range
    3. Normalize to standard schemas
    4. Convert to NautilusTrader types
    5. Write to ParquetDataCatalog
    6. Generate instrument definition

    Parameters
    ----------
    source_type : str
        Data source type ("binance", "tardis", "csv", "websocket")
    symbol : str
        Trading symbol (e.g., "BTCUSDT")
    start_date : str
        Start date in YYYY-MM-DD format
    end_date : str
        End date in YYYY-MM-DD format
    output_path : str
        Output catalog directory
    data_types : list[str], default ["trades", "mark", "funding"]
        Data types to fetch
    source_config : dict, optional
        Additional source configuration
    exchange : str, default "BINANCE"
        Exchange name

    """
    console.rule("[bold cyan]Data Pipeline Execution[/bold cyan]")

    # Parse dates
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    console.print("\n[bold]Configuration:[/bold]")
    console.print(f"  Source: {source_type}")
    console.print(f"  Symbol: {symbol}")
    console.print(f"  Date range: {start_date} to {end_date}")
    console.print(f"  Data types: {', '.join(data_types)}")
    console.print(f"  Output: {output_path}\n")

    # Create source
    config = source_config or {}
    source = create_source(source_type, config)
    console.print(f"[green]✓[/green] Initialized {source}")

    # Validate connection
    try:
        await source.validate_connection()
        console.print("[green]✓[/green] Connection validated\n")
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}\n")
        raise

    # Fetch data
    console.print("[bold]Fetching data...[/bold]")
    data = await fetch_data(source, symbol, start_dt, end_dt, data_types)

    # Check if we got any data
    if all(df.empty for df in data.values()):
        console.print("[yellow]⚠[/yellow] No data fetched, exiting")
        return

    # Normalize data
    console.print("\n[bold]Normalizing data...[/bold]")
    normalized = normalize_data(data, source_type)

    # Write to catalog
    console.print("\n[bold]Writing to catalog...[/bold]")
    write_to_catalog(normalized, symbol, output_path, exchange)

    # Print summary
    console.print("\n[bold green]✓ Pipeline completed successfully![/bold green]\n")

    # Print data summary
    from rich.table import Table

    table = Table(title="Data Summary", show_header=True, header_style="bold cyan")
    table.add_column("Data Type", style="cyan")
    table.add_column("Records", justify="right", style="magenta")

    for data_type, df in normalized.items():
        table.add_row(data_type.title(), f"{len(df):,}")

    console.print(table)

    # Close source
    await source.close()


# CLI Interface
app = typer.Typer(
    name="replay_to_parquet",
    help="Convert market data to Nautilus ParquetDataCatalog format",
    add_completion=False,
)


@app.command()
def main(
    source: str = typer.Option(
        "binance", "--source", "-s", help="Data source: binance, tardis, csv, websocket"
    ),
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Trading symbol"),
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)"),
    output: str = typer.Option("./data/catalog", "--output", "-o", help="Output catalog path"),
    data_types: str = typer.Option(
        "trades,mark,funding",
        "--data-types",
        "-d",
        help="Comma-separated data types to fetch",
    ),
    exchange: str = typer.Option("BINANCE", "--exchange", "-e", help="Exchange name"),
    config_file: str | None = typer.Option(
        None, "--config", "-c", help="Source configuration file (JSON/YAML)"
    ),
) -> None:
    """
    Run data pipeline to convert market data to Nautilus catalog.

    Example:
        python -m naut_hedgegrid.data.pipelines.replay_to_parquet \\
            --source tardis \\
            --symbol BTCUSDT \\
            --start 2024-01-01 \\
            --end 2024-01-03 \\
            --output ./data/catalog
    """
    # Parse data types
    data_type_list = [dt.strip() for dt in data_types.split(",")]

    # Load config if provided
    source_config = {}
    if config_file:
        import json

        with open(config_file) as f:
            if config_file.endswith(".json"):
                source_config = json.load(f)
            else:
                import yaml

                source_config = yaml.safe_load(f)

    # Run pipeline
    try:
        asyncio.run(
            run_pipeline(
                source_type=source,
                symbol=symbol,
                start_date=start,
                end_date=end,
                output_path=output,
                data_types=data_type_list,
                source_config=source_config,
                exchange=exchange,
            )
        )
    except Exception as e:
        console.print(f"\n[red]Pipeline failed: {e}[/red]")
        import traceback

        console.print(f"[red]{traceback.format_exc()}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
