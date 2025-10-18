"""
Sample data generation script.

Generates 3-day sample dataset for BTCUSDT using Tardis.dev
to enable quick testing of the backtest runner.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from rich.console import Console

from naut_hedgegrid.data.pipelines.replay_to_parquet import run_pipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
console = Console()


async def generate_sample() -> None:
    """
    Generate 3-day sample dataset for BTCUSDT.

    Fetches data for January 1-3, 2024 from Tardis.dev including:
    - Trade ticks (aggTrade)
    - Mark prices (markPrice@1s)
    - Funding rates

    The data is written to ./data/catalog in Nautilus ParquetDataCatalog format.
    """
    console.rule("[bold cyan]Sample Data Generation[/bold cyan]")
    console.print("\n[bold]Configuration:[/bold]")
    console.print("  Symbol: BTCUSDT")
    console.print("  Exchange: Binance Futures")
    console.print("  Date range: 2024-01-01 to 2024-01-04 (3 days)")
    console.print("  Data types: trades, mark prices, funding rates")
    console.print("  Output: ./data/catalog\n")

    # Run pipeline with Tardis source
    await run_pipeline(
        source_type="tardis",
        symbol="BTCUSDT",
        start_date="2024-01-01",
        end_date="2024-01-04",  # Exclusive, so this gives us 3 full days
        output_path="./data/catalog",
        data_types=["trades", "mark", "funding"],
        source_config={
            "exchange": "binance-futures",
            # API key read from TARDIS_API_KEY environment variable
        },
        exchange="BINANCE",
    )

    # Print summary statistics
    console.print("\n[bold cyan]Verifying catalog...[/bold cyan]\n")

    try:
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        catalog = ParquetDataCatalog("./data/catalog")

        # Load and count data
        instrument_ids = ["BTCUSDT-PERP.BINANCE"]
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 4)

        # Check instruments
        instruments = catalog.instruments(instrument_ids=instrument_ids)
        console.print(f"[green]✓[/green] Instruments: {len(instruments)}")
        for inst in instruments:
            console.print(f"  - {inst.id}")

        # Check trade ticks
        try:
            trades = catalog.trade_ticks(
                instrument_ids=instrument_ids,
                start=start,
                end=end,
            )
            console.print(f"[green]✓[/green] Trade ticks: {len(trades):,}")

            if trades:
                # Print sample statistics
                first_trade = trades[0]
                last_trade = trades[-1]
                console.print(f"  First trade: {first_trade.ts_event}")
                console.print(f"  Last trade: {last_trade.ts_event}")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Trade ticks: {e}")

        # Check custom data files
        catalog_path = Path("./data/catalog")
        inst_dir = catalog_path / "BTCUSDT-PERP.BINANCE"

        mark_file = inst_dir / "mark_price.parquet"
        if mark_file.exists():
            import pandas as pd

            mark_df = pd.read_parquet(mark_file)
            console.print(f"[green]✓[/green] Mark prices: {len(mark_df):,}")
        else:
            console.print("[yellow]⚠[/yellow] Mark prices: file not found")

        funding_file = inst_dir / "funding_rate.parquet"
        if funding_file.exists():
            import pandas as pd

            funding_df = pd.read_parquet(funding_file)
            console.print(f"[green]✓[/green] Funding rates: {len(funding_df):,}")
        else:
            console.print("[yellow]⚠[/yellow] Funding rates: file not found")

    except Exception as e:
        console.print(f"[red]✗[/red] Verification failed: {e}")
        import traceback

        console.print(f"[red]{traceback.format_exc()}[/red]")

    console.print("\n[bold green]✓ Sample data generation complete![/bold green]")
    console.print(
        "\n[bold]Next steps:[/bold]\n"
        "  1. Run backtest with the generated data:\n"
        "     python -m naut_hedgegrid.runners.run_backtest \\\n"
        "       --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \\\n"
        "       --strategy-config configs/strategies/hedge_grid_v1.yaml\n"
    )


def main() -> None:
    """Entry point for script execution."""
    try:
        asyncio.run(generate_sample())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback

        console.print(f"[red]{traceback.format_exc()}[/red]")
        raise


if __name__ == "__main__":
    main()
