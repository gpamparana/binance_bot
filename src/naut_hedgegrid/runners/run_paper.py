"""Nautilus paper trading runner with simulated execution."""

import os
import signal
import sys
import time
import traceback
from pathlib import Path
from types import FrameType

import typer
from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceAccountType,
    BinanceDataClientConfig,
)
from nautilus_trader.config import InstrumentProviderConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from rich.console import Console
from rich.panel import Panel

from naut_hedgegrid.config.strategy import HedgeGridConfig, HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfig, VenueConfigLoader
from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1Config

# CLI Interface
app = typer.Typer(
    name="run_paper",
    help="Run paper trading with Nautilus TradingNode (simulated execution)",
    add_completion=False,
)
console = Console()

# Global node reference for signal handler
_node: TradingNode | None = None


def signal_handler(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
    """
    Handle shutdown signals gracefully.

    Parameters
    ----------
    signum : int
        Signal number
    frame : FrameType | None
        Current stack frame

    """
    console.print("\n[yellow]Shutdown signal received[/yellow]")
    sys.exit(0)


def load_strategy_config(
    strategy_config_path: Path,
    hedge_grid_cfg: HedgeGridConfig,
    venue_cfg: VenueConfig,
) -> HedgeGridV1Config:
    """
    Load strategy configuration for TradingNode.

    Creates a HedgeGridV1Config that wraps the hedge grid configuration
    and provides the necessary parameters for Nautilus TradingNode integration.

    Parameters
    ----------
    strategy_config_path : Path
        Path to HedgeGridConfig YAML file
    hedge_grid_cfg : HedgeGridConfig
        Loaded hedge grid configuration
    venue_cfg : VenueConfig
        Venue configuration

    Returns
    -------
    HedgeGridV1Config
        Strategy config ready for TradingNode

    """
    # Extract instrument ID
    instrument_id = hedge_grid_cfg.strategy.instrument_id

    # Create bar type string (Nautilus format: BTCUSDT-PERP.BINANCE-1-MINUTE-LAST)
    bar_type_str = f"{instrument_id}-1-MINUTE-LAST"

    # Determine OMS type from venue config
    oms_type = OmsType.HEDGING if venue_cfg.trading.hedge_mode else OmsType.NETTING

    # Create HedgeGridV1Config
    return HedgeGridV1Config(
        instrument_id=instrument_id,
        bar_type=bar_type_str,
        hedge_grid_config_path=str(strategy_config_path),
        oms_type=oms_type,
    )


def create_bar_type(instrument_id_str: str) -> BarType:
    """
    Create BarType object for 1-minute bars.

    Constructs BarType programmatically to avoid string parsing issues.

    Parameters
    ----------
    instrument_id_str : str
        Instrument ID string (e.g., "BTCUSDT-PERP.BINANCE")

    Returns
    -------
    BarType
        Configured BarType for 1-minute LAST bars

    """
    instrument_id = InstrumentId.from_str(instrument_id_str)

    bar_spec = BarSpecification(
        step=1,
        aggregation=BarAggregation.MINUTE,
        price_type=PriceType.LAST,
    )

    return BarType(
        instrument_id=instrument_id,
        bar_spec=bar_spec,
        aggregation_source=AggregationSource.EXTERNAL,
    )


def create_data_client_config(
    instrument_id: str,
    venue_cfg: VenueConfig,
    api_key: str | None,
    api_secret: str | None,
) -> BinanceDataClientConfig:
    """
    Create Binance data client configuration.

    Configures the data client to subscribe to specific instruments only,
    avoiding the overhead of loading all exchange instruments.

    Parameters
    ----------
    instrument_id : str
        Instrument ID to subscribe to
    venue_cfg : VenueConfig
        Venue configuration
    api_key : str | None
        API key (optional for public data)
    api_secret : str | None
        API secret (optional for public data)

    Returns
    -------
    BinanceDataClientConfig
        Configured data client

    """
    # Extract symbol from instrument_id (e.g., "BTCUSDT-PERP.BINANCE" -> "BTCUSDT")
    symbol = instrument_id.split("-")[0]

    return BinanceDataClientConfig(
        api_key=api_key,
        api_secret=api_secret,
        account_type=BinanceAccountType.USDT_FUTURE,
        testnet=venue_cfg.api.testnet,
        base_url_http=str(venue_cfg.api.base_url) if venue_cfg.api.base_url else None,
        instrument_provider=InstrumentProviderConfig(
            load_all=False,  # Don't load all instruments
            filters={"symbols": [symbol]},  # Only load this symbol
        ),
    )


def create_node_config(
    strategy_config: HedgeGridV1Config,
    data_client_config: BinanceDataClientConfig,
    is_live: bool = False,
) -> TradingNodeConfig:
    """
    Create TradingNodeConfig for paper or live trading.

    Parameters
    ----------
    strategy_config : HedgeGridV1Config
        Strategy configuration
    data_client_config : BinanceDataClientConfig
        Data client configuration
    is_live : bool
        True for live trading (requires exec client), False for paper

    Returns
    -------
    TradingNodeConfig
        Configured TradingNode

    """
    trader_id = "LIVE-001" if is_live else "PAPER-001"

    # Paper trading has no exec client (simulated fills)
    exec_clients = {}

    return TradingNodeConfig(
        trader_id=trader_id,
        data_clients={BINANCE: data_client_config},
        exec_clients=exec_clients,
        strategies=[strategy_config],
        log_level="INFO",
    )


@app.command()
def main(  # noqa: PLR0915
    strategy_config: str = typer.Option(
        "configs/strategies/hedge_grid_v1.yaml",
        "--strategy-config",
        "-s",
        help="Path to strategy config YAML",
    ),
    venue_config: str = typer.Option(
        "configs/venues/binance_futures.yaml",
        "--venue-config",
        "-v",
        help="Path to venue config YAML",
    ),
) -> None:
    """
    Run paper trading with simulated execution.

    This command:
    1. Loads strategy and venue configurations
    2. Connects to Binance data feed (WebSocket + REST)
    3. Runs strategy with simulated fills (no real orders)
    4. Handles graceful shutdown on CTRL-C

    Paper trading provides realistic market data with zero execution risk,
    ideal for testing strategies before live deployment.

    Example:
        uv run python -m naut_hedgegrid.runners.run_paper \\
            --strategy-config configs/strategies/hedge_grid_v1.yaml \\
            --venue-config configs/venues/binance_futures.yaml

    """
    global _node  # noqa: PLW0603

    console.rule("[bold cyan]Paper Trading Runner[/bold cyan]")
    console.print()

    try:
        # Load configurations
        console.print("[bold]Loading configurations...[/bold]")

        # Load strategy config
        strat_config_path = Path(strategy_config)
        if not strat_config_path.exists():
            console.print(f"[red]Error: Strategy config not found: {strat_config_path}[/red]")
            raise typer.Exit(code=1)

        hedge_grid_cfg = HedgeGridConfigLoader.load(strat_config_path)
        console.print(f"[green]✓[/green] Strategy config: {strat_config_path.name}")

        # Load venue config
        venue_config_path = Path(venue_config)
        if not venue_config_path.exists():
            console.print(f"[red]Error: Venue config not found: {venue_config_path}[/red]")
            raise typer.Exit(code=1)

        venue_cfg = VenueConfigLoader.load(venue_config_path)
        console.print(f"[green]✓[/green] Venue config: {venue_config_path.name}")

        # Validate venue
        if venue_cfg.venue.name != "BINANCE":
            console.print(
                f"[red]Error: Only BINANCE venue supported, got {venue_cfg.venue.name}[/red]"
            )
            raise typer.Exit(code=1)

        # Get API keys from environment (optional for paper trading data feed)
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")

        if not api_key or not api_secret:
            console.print(
                "[yellow]⚠ Warning: BINANCE_API_KEY and BINANCE_API_SECRET not set[/yellow]"
            )
            console.print(
                "[yellow]  Public market data only (no account data or private endpoints)[/yellow]"
            )

        # Get instrument ID from strategy config
        instrument_id = hedge_grid_cfg.strategy.instrument_id
        console.print(f"[cyan]Instrument: {instrument_id}[/cyan]")

        # Determine OMS type from venue config
        oms_type = OmsType.HEDGING if venue_cfg.trading.hedge_mode else OmsType.NETTING
        console.print(f"[cyan]OMS Type: {oms_type.name}[/cyan]")
        console.print()

        # Configure TradingNode
        console.print("[bold]Configuring paper trading node...[/bold]")

        # Create strategy config for Nautilus
        strat_cfg = load_strategy_config(
            strategy_config_path=strat_config_path,
            hedge_grid_cfg=hedge_grid_cfg,
            venue_cfg=venue_cfg,
        )

        # Create data client config with instrument subscription
        data_client_config = create_data_client_config(
            instrument_id=instrument_id,
            venue_cfg=venue_cfg,
            api_key=api_key,
            api_secret=api_secret,
        )

        # Create node config (no exec client = paper trading)
        node_config = create_node_config(
            strategy_config=strat_cfg,
            data_client_config=data_client_config,
            is_live=False,
        )

        # Extract symbol for display
        symbol = instrument_id.split("-")[0]
        console.print("[green]✓[/green] Data client configured: BINANCE (USDT_FUTURE)")
        console.print(f"[green]✓[/green] Instrument subscription: {symbol}")
        console.print("[green]✓[/green] Execution mode: PAPER (simulated fills)")
        console.print()

        # Create node
        _node = TradingNode(config=node_config)

        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Build and start node
        console.print("[bold]Starting trading node...[/bold]")

        try:
            _node.build()
            console.print("[green]✓[/green] Node built successfully")

            _node.start()
            console.print("[green]✓[/green] Node started, waiting for bars...")
            console.print()

            # Display running status
            status_panel = Panel(
                "[green]Paper Trading Active[/green]\n\n"
                f"Strategy: {hedge_grid_cfg.strategy.name}\n"
                f"Instrument: {instrument_id}\n"
                f"OMS Type: {oms_type.name}\n"
                f"Bar Type: 1-MINUTE-LAST\n\n"
                "[cyan]Press CTRL-C to shutdown[/cyan]",
                title="Status",
                border_style="cyan",
            )
            console.print(status_panel)
            console.print()

            # Keep alive loop
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            console.print("\n[yellow]Keyboard interrupt detected[/yellow]")

        finally:
            console.print("\n[bold]Shutting down...[/bold]")

            try:
                # Stop node (calls strategy.on_stop() which cancels orders)
                _node.stop()
                console.print("[green]✓[/green] Node stopped")

            except Exception as e:  # noqa: BLE001
                console.print(f"[yellow]Warning during shutdown: {e}[/yellow]")

            finally:
                # Dispose resources
                _node.dispose()
                console.print("[green]✓[/green] Shutdown complete")
                console.print()

    except typer.Exit:
        raise
    except FileNotFoundError as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1) from None
    except Exception as e:  # noqa: BLE001
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        console.print(f"[red]{traceback.format_exc()}[/red]")
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
