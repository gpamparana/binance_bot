"""Base runner infrastructure for paper and live trading.

This module provides the base infrastructure for trading runners, extracting
common functionality between paper and live trading modes.

Key Features:
    - Event-based shutdown mechanism (no blocking sleep())
    - Signal handling (SIGINT, SIGTERM)
    - Common configuration loading
    - Resource cleanup
    - Rich console output
"""

import os
import signal
import sys
import threading
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from types import FrameType

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceAccountType,
    BinanceDataClientConfig,
    BinanceExecClientConfig,
)
from nautilus_trader.config import InstrumentProviderConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from rich.console import Console
from rich.panel import Panel

from nautilus_trader.config import ImportableStrategyConfig

from naut_hedgegrid.config.strategy import HedgeGridConfig, HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfig, VenueConfigLoader


class BaseRunner(ABC):
    """Base runner with common functionality for paper and live trading.

    This base class extracts common functionality between paper and live trading,
    including:
    - Configuration loading and validation
    - Signal handling with event-based shutdown
    - TradingNode lifecycle management
    - Error handling and cleanup

    Subclasses must implement:
    - create_exec_client_config(): Define execution client configuration
    - get_runner_name(): Return display name for the runner
    - get_trader_id(): Return trader ID for TradingNode
    - show_startup_warning(): Display mode-specific warnings
    - get_status_panel(): Create status display panel
    """

    def __init__(self) -> None:
        """Initialize base runner."""
        self.console = Console()
        self.shutdown_event = threading.Event()
        self.node: TradingNode | None = None
        self.ops_manager = None

    @abstractmethod
    def create_exec_client_config(
        self,
        venue_cfg: VenueConfig,
        api_key: str | None,
        api_secret: str | None,
    ) -> BinanceExecClientConfig | None:
        """Create execution client configuration.

        Parameters
        ----------
        venue_cfg : VenueConfig
            Venue configuration
        api_key : str | None
            API key (may be None for paper trading)
        api_secret : str | None
            API secret (may be None for paper trading)

        Returns
        -------
        BinanceExecClientConfig | None
            Execution client config, or None for paper trading
        """
        raise NotImplementedError

    @abstractmethod
    def get_runner_name(self) -> str:
        """Get display name for the runner.

        Returns
        -------
        str
            Runner name for console display
        """
        raise NotImplementedError

    @abstractmethod
    def get_trader_id(self) -> str:
        """Get trader ID for TradingNode.

        Returns
        -------
        str
            Trader ID (e.g., "PAPER-001", "LIVE-001")
        """
        raise NotImplementedError

    @abstractmethod
    def show_startup_warning(self, venue_cfg: VenueConfig) -> None:
        """Display mode-specific startup warnings.

        Parameters
        ----------
        venue_cfg : VenueConfig
            Venue configuration for display
        """
        raise NotImplementedError

    @abstractmethod
    def get_status_panel(
        self,
        hedge_grid_cfg: HedgeGridConfig,
        instrument_id: str,
        oms_type: OmsType,
        venue_cfg: VenueConfig,
    ) -> Panel:
        """Create status display panel.

        Parameters
        ----------
        hedge_grid_cfg : HedgeGridConfig
            Hedge grid configuration
        instrument_id : str
            Trading instrument ID
        oms_type : OmsType
            Order management system type
        venue_cfg : VenueConfig
            Venue configuration

        Returns
        -------
        Panel
            Rich panel for status display
        """
        raise NotImplementedError

    def setup_signal_handlers(self) -> None:
        """Setup graceful shutdown handlers for SIGINT and SIGTERM."""

        def signal_handler(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
            """Handle shutdown signals gracefully."""
            self.console.print("\n[yellow]Shutdown signal received[/yellow]")
            self.shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def load_strategy_config(
        self,
        strategy_config_path: Path,
        hedge_grid_cfg: HedgeGridConfig,
        venue_cfg: VenueConfig,
    ) -> ImportableStrategyConfig:
        """Load strategy configuration for TradingNode.

        Creates an ImportableStrategyConfig that tells Nautilus how to load
        the HedgeGridV1 strategy with the necessary parameters.

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
        ImportableStrategyConfig
            Strategy config ready for TradingNode
        """
        # Extract instrument ID
        instrument_id = hedge_grid_cfg.strategy.instrument_id

        # Determine OMS type from venue config
        oms_type = OmsType.HEDGING if venue_cfg.trading.hedge_mode else OmsType.NETTING

        # Create ImportableStrategyConfig
        # This is the correct Nautilus 1.220.0 pattern - use ImportableStrategyConfig directly
        # Do NOT subclass it! Just provide the paths and config dict.
        # Note: bar_type is NOT included - strategy constructs it programmatically to avoid parsing bug
        return ImportableStrategyConfig(
            strategy_path="naut_hedgegrid.strategies.hedge_grid_v1.strategy:HedgeGridV1",
            config_path="naut_hedgegrid.strategies.hedge_grid_v1.config:HedgeGridV1Config",
            config={
                "instrument_id": instrument_id,
                "hedge_grid_config_path": str(strategy_config_path),
                "oms_type": oms_type.value,
            },
        )

    def create_bar_type(self, instrument_id_str: str) -> BarType:
        """Create BarType object for 1-minute bars.

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
        self,
        instrument_id: str,
        venue_cfg: VenueConfig,
        api_key: str | None,
        api_secret: str | None,
    ) -> BinanceDataClientConfig:
        """Create Binance data client configuration.

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
            account_type=BinanceAccountType.USDT_FUTURES,
            testnet=venue_cfg.api.testnet,
            base_url_http=str(venue_cfg.api.base_url) if venue_cfg.api.base_url else None,
            instrument_provider=InstrumentProviderConfig(
                load_all=False,  # Don't load all instruments
                filters={"symbols": [symbol]},  # Only load this symbol
            ),
        )

    def create_node_config(
        self,
        strategy_config: ImportableStrategyConfig,
        data_client_config: BinanceDataClientConfig,
        exec_client_config: BinanceExecClientConfig | None = None,
    ) -> TradingNodeConfig:
        """Create TradingNodeConfig for paper or live trading.

        Parameters
        ----------
        strategy_config : ImportableStrategyConfig
            Strategy configuration
        data_client_config : BinanceDataClientConfig
            Data client configuration
        exec_client_config : BinanceExecClientConfig | None
            Execution client configuration (None for paper trading)

        Returns
        -------
        TradingNodeConfig
            Configured TradingNode
        """
        trader_id = self.get_trader_id()

        # Add exec client if provided
        exec_clients = {BINANCE: exec_client_config} if exec_client_config else {}

        return TradingNodeConfig(
            trader_id=trader_id,
            data_clients={BINANCE: data_client_config},
            exec_clients=exec_clients,
            strategies=[strategy_config],
        )

    def validate_environment(self, require_api_keys: bool = False) -> tuple[str | None, str | None]:
        """Validate environment variables and API credentials.

        Parameters
        ----------
        require_api_keys : bool
            If True, API keys are required and missing keys will raise an error

        Returns
        -------
        tuple[str | None, str | None]
            API key and secret (may be None if not required)
        """
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")

        if require_api_keys:
            if not api_key or not api_secret:
                self.console.print(
                    "[red]Error: BINANCE_API_KEY and BINANCE_API_SECRET "
                    "environment variables required[/red]"
                )
                self.console.print("[yellow]Set with:[/yellow]")
                self.console.print("[yellow]  export BINANCE_API_KEY=your_key[/yellow]")
                self.console.print("[yellow]  export BINANCE_API_SECRET=your_secret[/yellow]")
                sys.exit(1)

            self.console.print("[green]✓[/green] BINANCE_API_KEY found")
            self.console.print("[green]✓[/green] BINANCE_API_SECRET found")
        elif not api_key or not api_secret:
            self.console.print(
                "[yellow]⚠ Warning: BINANCE_API_KEY and BINANCE_API_SECRET not set[/yellow]"
            )
            self.console.print(
                "[yellow]  Public market data only "
                "(no account data or private endpoints)[/yellow]"
            )

        return api_key, api_secret

    def load_configs(
        self,
        strategy_config: str,
        venue_config: str,
    ) -> tuple[Path, HedgeGridConfig, Path, VenueConfig]:
        """Load and validate strategy and venue configurations.

        Parameters
        ----------
        strategy_config : str
            Path to strategy config YAML
        venue_config : str
            Path to venue config YAML

        Returns
        -------
        tuple[Path, HedgeGridConfig, Path, VenueConfig]
            Paths and loaded configurations
        """
        self.console.print("[bold]Loading configurations...[/bold]")

        # Load strategy config
        strat_config_path = Path(strategy_config)
        if not strat_config_path.exists():
            self.console.print(f"[red]Error: Strategy config not found: {strat_config_path}[/red]")
            sys.exit(1)

        hedge_grid_cfg = HedgeGridConfigLoader.load(strat_config_path)
        self.console.print(f"[green]✓[/green] Strategy config: {strat_config_path.name}")

        # Load venue config
        venue_config_path = Path(venue_config)
        if not venue_config_path.exists():
            self.console.print(f"[red]Error: Venue config not found: {venue_config_path}[/red]")
            sys.exit(1)

        venue_cfg = VenueConfigLoader.load(venue_config_path)
        self.console.print(f"[green]✓[/green] Venue config: {venue_config_path.name}")

        # Validate venue
        if venue_cfg.venue.name != "BINANCE":
            self.console.print(
                f"[red]Error: Only BINANCE venue supported, got {venue_cfg.venue.name}[/red]"
            )
            sys.exit(1)

        return strat_config_path, hedge_grid_cfg, venue_config_path, venue_cfg

    def run(  # noqa: PLR0915
        self,
        strategy_config: str,
        venue_config: str,
        require_api_keys: bool = False,
        enable_ops: bool = False,
        prometheus_port: int = 9090,
        api_port: int = 8080,
        api_key: str | None = None,
    ) -> None:
        """Main runner logic.

        This method orchestrates the entire trading lifecycle:
        1. Validate environment and load configurations
        2. Create TradingNode with appropriate client configs
        3. Optionally start operational infrastructure (Prometheus + FastAPI)
        4. Start trading with event-based main loop
        5. Handle graceful shutdown with proper cleanup

        Parameters
        ----------
        strategy_config : str
            Path to strategy config YAML
        venue_config : str
            Path to venue config YAML
        require_api_keys : bool
            If True, API keys are required (for live trading)
        enable_ops : bool
            If True, start Prometheus and FastAPI services
        prometheus_port : int
            Port for Prometheus metrics endpoint (default 9090)
        api_port : int
            Port for FastAPI control endpoints (default 8080)
        api_key : str | None
            Optional API key for FastAPI authentication
        """
        self.console.rule(f"[bold cyan]{self.get_runner_name()}[/bold cyan]")
        self.console.print()

        try:
            # Validate environment
            if require_api_keys:
                self.console.print("[bold]Validating environment variables...[/bold]")
            api_key, api_secret = self.validate_environment(require_api_keys)
            self.console.print()

            # Load configurations
            strat_config_path, hedge_grid_cfg, venue_config_path, venue_cfg = self.load_configs(
                strategy_config, venue_config
            )

            # Get instrument ID from strategy config
            instrument_id = hedge_grid_cfg.strategy.instrument_id
            self.console.print(f"[cyan]Instrument: {instrument_id}[/cyan]")

            # Determine OMS type from venue config
            oms_type = OmsType.HEDGING if venue_cfg.trading.hedge_mode else OmsType.NETTING
            self.console.print(f"[cyan]OMS Type: {oms_type.name}[/cyan]")
            self.console.print()

            # Show mode-specific warnings
            self.show_startup_warning(venue_cfg)

            # Configure TradingNode
            self.console.print("[bold]Configuring trading node...[/bold]")

            # Create strategy config for Nautilus
            strat_cfg = self.load_strategy_config(
                strategy_config_path=strat_config_path,
                hedge_grid_cfg=hedge_grid_cfg,
                venue_cfg=venue_cfg,
            )

            # Create data client config with instrument subscription
            data_client_config = self.create_data_client_config(
                instrument_id=instrument_id,
                venue_cfg=venue_cfg,
                api_key=api_key,
                api_secret=api_secret,
            )

            # Create execution client config (subclass-specific)
            exec_client_config = self.create_exec_client_config(
                venue_cfg=venue_cfg,
                api_key=api_key,
                api_secret=api_secret,
            )

            # Create node config
            node_config = self.create_node_config(
                strategy_config=strat_cfg,
                data_client_config=data_client_config,
                exec_client_config=exec_client_config,
            )

            # Display configuration summary
            symbol = instrument_id.split("-")[0]
            self.console.print("[green]✓[/green] Data client configured: BINANCE (USDT_FUTURES)")
            self.console.print(f"[green]✓[/green] Instrument subscription: {symbol}")

            if exec_client_config:
                self.console.print(
                    "[green]✓[/green] Execution client configured: BINANCE (USDT_FUTURES)"
                )
                hedge_status = "enabled" if venue_cfg.trading.hedge_mode else "disabled"
                self.console.print(
                    f"[green]✓[/green] use_reduce_only: False (hedge mode {hedge_status})"
                )
            else:
                self.console.print("[green]✓[/green] Execution mode: PAPER (simulated fills)")
            self.console.print()

            # Create node
            self.node = TradingNode(config=node_config)

            # Setup signal handlers
            self.setup_signal_handlers()

            # Build and start node
            self.console.print("[bold]Starting trading node...[/bold]")

            try:
                self.node.build()
                self.console.print("[green]✓[/green] Node built successfully")

                self.node.run()
                self.console.print("[green]✓[/green] Node started, waiting for bars...")
                self.console.print()

                # Start operational infrastructure if enabled
                if enable_ops:
                    self.console.print("[bold]Starting operational infrastructure...[/bold]")
                    try:
                        # Import here to avoid circular dependency
                        from naut_hedgegrid.ops import OperationsManager

                        # Get strategy instance from node
                        strategy = self.node.trader.strategy_states()[0]

                        # Initialize ops manager
                        self.ops_manager = OperationsManager(
                            strategy=strategy,
                            instrument_id=instrument_id,
                            prometheus_port=prometheus_port,
                            api_port=api_port,
                            api_key=api_key,
                        )

                        # Start services
                        self.ops_manager.start()
                        self.console.print(
                            f"[green]✓[/green] Prometheus metrics: http://localhost:{prometheus_port}/metrics"
                        )
                        self.console.print(
                            f"[green]✓[/green] FastAPI endpoints: http://localhost:{api_port}/docs"
                        )
                        self.console.print()

                    except Exception as e:  # noqa: BLE001
                        self.console.print(
                            f"[yellow]Warning: Failed to start ops infrastructure: {e}[/yellow]"
                        )
                        self.ops_manager = None

                # Display running status
                status_panel = self.get_status_panel(
                    hedge_grid_cfg=hedge_grid_cfg,
                    instrument_id=instrument_id,
                    oms_type=oms_type,
                    venue_cfg=venue_cfg,
                )
                self.console.print(status_panel)
                self.console.print()

                # Event-based wait (no blocking sleep!)
                # This blocks efficiently until shutdown_event is set
                self.shutdown_event.wait()

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Keyboard interrupt detected[/yellow]")

            finally:
                self.console.print("\n[bold]Shutting down...[/bold]")

                try:
                    # Stop operational infrastructure first
                    if self.ops_manager is not None:
                        self.console.print("[cyan]Stopping operational infrastructure...[/cyan]")
                        self.ops_manager.stop()
                        self.console.print("[green]✓[/green] Ops infrastructure stopped")

                    # Stop node (calls strategy.on_stop() which cancels orders)
                    if exec_client_config:
                        self.console.print("[cyan]Canceling open orders...[/cyan]")
                    self.node.stop()
                    self.console.print("[green]✓[/green] Node stopped")

                except Exception as e:  # noqa: BLE001
                    self.console.print(f"[yellow]Warning during shutdown: {e}[/yellow]")

                finally:
                    # Dispose resources
                    self.node.dispose()
                    self.console.print("[green]✓[/green] Shutdown complete")
                    self.console.print()

        except FileNotFoundError as e:
            self.console.print(f"\n[red]Error: {e}[/red]")
            sys.exit(1)
        except Exception as e:  # noqa: BLE001
            self.console.print(f"\n[red]Unexpected error: {e}[/red]")
            self.console.print(f"[red]{traceback.format_exc()}[/red]")
            sys.exit(1)


class PaperRunner(BaseRunner):
    """Paper trading runner with simulated execution.

    This runner connects to real market data but simulates all order execution.
    No real orders are placed, making it ideal for strategy testing without risk.
    """

    def create_exec_client_config(
        self,
        venue_cfg: VenueConfig,  # noqa: ARG002
        api_key: str | None,  # noqa: ARG002
        api_secret: str | None,  # noqa: ARG002
    ) -> BinanceExecClientConfig | None:
        """Create execution client config for paper trading.

        Paper trading uses simulated execution, so no exec client is needed.

        Parameters
        ----------
        venue_cfg : VenueConfig
            Venue configuration (unused for paper trading)
        api_key : str | None
            API key (unused for paper trading)
        api_secret : str | None
            API secret (unused for paper trading)

        Returns
        -------
        None
            No exec client for paper trading
        """
        return None

    def get_runner_name(self) -> str:
        """Get display name for paper trading runner.

        Returns
        -------
        str
            Display name
        """
        return "Paper Trading Runner"

    def get_trader_id(self) -> str:
        """Get trader ID for paper trading.

        Returns
        -------
        str
            Trader ID
        """
        return "PAPER-001"

    def show_startup_warning(self, venue_cfg: VenueConfig) -> None:
        """Display paper trading startup message.

        Parameters
        ----------
        venue_cfg : VenueConfig
            Venue configuration (unused)
        """
        # No warning needed for paper trading

    def get_status_panel(
        self,
        hedge_grid_cfg: HedgeGridConfig,
        instrument_id: str,
        oms_type: OmsType,
        venue_cfg: VenueConfig,  # noqa: ARG002
    ) -> Panel:
        """Create status display panel for paper trading.

        Parameters
        ----------
        hedge_grid_cfg : HedgeGridConfig
            Hedge grid configuration
        instrument_id : str
            Trading instrument ID
        oms_type : OmsType
            Order management system type
        venue_cfg : VenueConfig
            Venue configuration

        Returns
        -------
        Panel
            Status display panel
        """
        return Panel(
            "[green]Paper Trading Active[/green]\n\n"
            f"Strategy: {hedge_grid_cfg.strategy.name}\n"
            f"Instrument: {instrument_id}\n"
            f"OMS Type: {oms_type.name}\n"
            f"Bar Type: 1-MINUTE-LAST\n\n"
            "[cyan]Press CTRL-C to shutdown[/cyan]",
            title="Status",
            border_style="cyan",
        )


class LiveRunner(BaseRunner):
    """Live trading runner with real execution.

    This runner places REAL ORDERS on the exchange using real funds.
    Only use this mode after thorough testing in paper trading.
    """

    def create_exec_client_config(
        self,
        venue_cfg: VenueConfig,
        api_key: str | None,
        api_secret: str | None,
    ) -> BinanceExecClientConfig:
        """Create execution client config for live trading.

        Configures the execution client for real order placement with proper
        hedge mode settings.

        Parameters
        ----------
        venue_cfg : VenueConfig
            Venue configuration
        api_key : str | None
            API key (required for live trading)
        api_secret : str | None
            API secret (required for live trading)

        Returns
        -------
        BinanceExecClientConfig
            Configured execution client
        """
        return BinanceExecClientConfig(
            api_key=api_key,
            api_secret=api_secret,
            account_type=BinanceAccountType.USDT_FUTURES,
            testnet=venue_cfg.api.testnet,
            base_url_http=str(venue_cfg.api.base_url) if venue_cfg.api.base_url else None,
            use_reduce_only=False,  # CRITICAL: False for hedge mode
        )

    def get_runner_name(self) -> str:
        """Get display name for live trading runner.

        Returns
        -------
        str
            Display name
        """
        return "Live Trading Runner"

    def get_trader_id(self) -> str:
        """Get trader ID for live trading.

        Returns
        -------
        str
            Trader ID
        """
        return "LIVE-001"

    def show_startup_warning(self, venue_cfg: VenueConfig) -> None:  # noqa: ARG002
        """Display live trading warning.

        Parameters
        ----------
        venue_cfg : VenueConfig
            Venue configuration (unused)
        """
        warning_panel = Panel(
            "[bold red]WARNING: LIVE TRADING WITH REAL FUNDS[/bold red]\n\n"
            "This mode will place REAL ORDERS on Binance Futures.\n"
            "All trades will execute with REAL MONEY.\n\n"
            "[yellow]Ensure your strategy is thoroughly tested before proceeding.[/yellow]",
            title="DANGER",
            border_style="red",
        )
        self.console.print(warning_panel)
        self.console.print()

    def get_status_panel(
        self,
        hedge_grid_cfg: HedgeGridConfig,
        instrument_id: str,
        oms_type: OmsType,
        venue_cfg: VenueConfig,
    ) -> Panel:
        """Create status display panel for live trading.

        Parameters
        ----------
        hedge_grid_cfg : HedgeGridConfig
            Hedge grid configuration
        instrument_id : str
            Trading instrument ID
        oms_type : OmsType
            Order management system type
        venue_cfg : VenueConfig
            Venue configuration

        Returns
        -------
        Panel
            Status display panel
        """
        return Panel(
            "[bold red]LIVE TRADING ACTIVE[/bold red]\n\n"
            f"Strategy: {hedge_grid_cfg.strategy.name}\n"
            f"Instrument: {instrument_id}\n"
            f"OMS Type: {oms_type.name}\n"
            f"Hedge Mode: {'Enabled' if venue_cfg.trading.hedge_mode else 'Disabled'}\n"
            f"Leverage: {venue_cfg.trading.leverage}x\n"
            f"Testnet: {'Yes' if venue_cfg.api.testnet else 'No'}\n"
            f"Bar Type: 1-MINUTE-LAST\n\n"
            "[cyan]Press CTRL-C to shutdown[/cyan]",
            title="Status",
            border_style="red",
        )
