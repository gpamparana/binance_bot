"""Nautilus backtest runner with data loading and artifact management."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from naut_hedgegrid.config.backtest import BacktestConfig, BacktestConfigLoader
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfigLoader
from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1, HedgeGridV1Config


class BacktestRunner:
    """
    Orchestrates backtests using Nautilus BacktestEngine.

    This runner handles:
    - Loading backtest configuration and strategy configs
    - Setting up Nautilus data catalog
    - Loading instruments and market data
    - Configuring venues with starting balances
    - Running the backtest with proper engine setup
    - Extracting results and computing metrics
    - Saving artifacts (config, trades, metrics) to disk

    Attributes
    ----------
    config : BacktestConfig
        Complete backtest configuration
    strategy_configs : list[HedgeGridV1Config]
        Strategy configurations to run
    console : Console
        Rich console for formatted output
    run_id : str
        Unique identifier for this backtest run

    """

    def __init__(
        self,
        backtest_config: BacktestConfig,
        strategy_configs: list[HedgeGridV1Config],
        console: Console | None = None,
    ) -> None:
        """
        Initialize backtest runner.

        Parameters
        ----------
        backtest_config : BacktestConfig
            Complete backtest configuration
        strategy_configs : list[HedgeGridV1Config]
            Strategy configurations to run
        console : Console, optional
            Rich console for output (creates new if None)

        """
        self.config = backtest_config
        self.strategy_configs = strategy_configs
        self.console = console or Console()
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def setup_catalog(self) -> ParquetDataCatalog:
        """
        Create ParquetDataCatalog from configured path.

        Returns
        -------
        ParquetDataCatalog
            Initialized data catalog

        Raises
        ------
        FileNotFoundError
            If catalog path does not exist

        """
        catalog_path = Path(self.config.data.catalog_path)
        if not catalog_path.exists():
            msg = f"Catalog path not found: {catalog_path}"
            self.console.print(f"[red]Error: {msg}[/red]")
            raise FileNotFoundError(msg)

        self.console.print(f"[green]✓[/green] Loading catalog from: {catalog_path}")
        return ParquetDataCatalog(path=str(catalog_path))

    def load_instruments(self, catalog: ParquetDataCatalog) -> list:
        """
        Load instrument definitions from catalog.

        Parameters
        ----------
        catalog : ParquetDataCatalog
            Data catalog to load from

        Returns
        -------
        list
            List of Nautilus instrument objects

        Raises
        ------
        ValueError
            If instrument not found in catalog

        """
        instruments = []
        for inst_config in self.config.data.instruments:
            instrument_id = InstrumentId.from_str(inst_config.instrument_id)

            try:
                # Load instruments from catalog
                inst = catalog.instruments(instrument_ids=[instrument_id.value])
                if inst:
                    instruments.extend(inst)
                    self.console.print(f"[green]✓[/green] Loaded instrument: {instrument_id}")
                else:
                    msg = f"Instrument {instrument_id} not found in catalog"
                    self.console.print(f"[yellow]Warning: {msg}[/yellow]")
            except Exception as e:
                self.console.print(f"[yellow]Warning: Failed to load {instrument_id}: {e}[/yellow]")

        if not instruments:
            msg = "No instruments loaded from catalog"
            self.console.print(f"[red]Error: {msg}[/red]")
            raise ValueError(msg)

        return instruments

    def load_data(self, catalog: ParquetDataCatalog) -> dict[str, Any]:
        """
        Load all configured data types from catalog.

        Loads trade ticks, bars, quote ticks, and other data types
        based on backtest configuration.

        Parameters
        ----------
        catalog : ParquetDataCatalog
            Data catalog to load from

        Returns
        -------
        dict[str, Any]
            Dictionary with data type keys and loaded data lists

        """
        data: dict[str, Any] = {}
        start = self.config.time_range.start_time
        end = self.config.time_range.end_time

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
        ) as progress:
            task = progress.add_task("Loading data...", total=None)

            for inst_config in self.config.data.instruments:
                instrument_ids = [inst_config.instrument_id]

                for data_type_config in inst_config.data_types:
                    data_type = data_type_config.type

                    try:
                        if data_type == "TradeTick":
                            ticks = catalog.trade_ticks(
                                instrument_ids=instrument_ids,
                                start=start,
                                end=end,
                            )
                            data.setdefault("trade_ticks", []).extend(ticks)
                            self.console.print(
                                f"[green]✓[/green] Loaded {len(ticks):,} trade ticks"
                            )

                        elif data_type == "QuoteTick":
                            quotes = catalog.quote_ticks(
                                instrument_ids=instrument_ids,
                                start=start,
                                end=end,
                            )
                            data.setdefault("quote_ticks", []).extend(quotes)
                            self.console.print(
                                f"[green]✓[/green] Loaded {len(quotes):,} quote ticks"
                            )

                        elif data_type == "Bar":
                            bars = catalog.bars(
                                instrument_ids=instrument_ids,
                                start=start,
                                end=end,
                            )
                            data.setdefault("bars", []).extend(bars)
                            self.console.print(f"[green]✓[/green] Loaded {len(bars):,} bars")

                        elif data_type == "OrderBookDelta":
                            deltas = catalog.order_book_deltas(
                                instrument_ids=instrument_ids,
                                start=start,
                                end=end,
                            )
                            data.setdefault("order_book_deltas", []).extend(deltas)
                            self.console.print(
                                f"[green]✓[/green] Loaded {len(deltas):,} order book deltas"
                            )

                        elif data_type in ("FundingRate", "MarkPrice"):
                            # Try loading as generic data
                            try:
                                generic_data = catalog.generic_data(
                                    cls=data_type,
                                    instrument_ids=instrument_ids,
                                    start=start,
                                    end=end,
                                )
                                key = data_type.lower() + "s"
                                data.setdefault(key, []).extend(generic_data)
                                self.console.print(
                                    f"[green]✓[/green] Loaded {len(generic_data):,} {data_type}s"
                                )
                            except Exception as e:
                                self.console.print(
                                    f"[yellow]⚠[/yellow] {data_type} not available: {e}"
                                )

                    except Exception as e:
                        self.console.print(
                            f"[yellow]Warning: Failed to load {data_type}: {e}[/yellow]"
                        )

            progress.update(task, completed=True)

        return data

    def setup_engine(self) -> BacktestEngine:
        """
        Initialize Nautilus BacktestEngine with venue configuration.

        Returns
        -------
        BacktestEngine
            Configured backtest engine

        Raises
        ------
        ValueError
            If venue configuration is invalid

        """
        # Create engine config with logging settings
        logging_config = LoggingConfig(
            bypass_logging=False,
            log_level=self.config.output.log_level,
        )
        engine_config = BacktestEngineConfig(
            logging=logging_config,
        )
        engine = BacktestEngine(config=engine_config)

        # Add venues
        for venue_config in self.config.venues:
            try:
                # Load venue config to get details
                venue_cfg = VenueConfigLoader.load(venue_config.config_path)
                venue = Venue(venue_cfg.venue.name)

                # Map account type from venue config
                account_type_map = {
                    "CASH": AccountType.CASH,
                    "MARGIN": AccountType.MARGIN,
                    "PERPETUAL_LINEAR": AccountType.MARGIN,
                    "PERPETUAL_INVERSE": AccountType.MARGIN,
                }
                account_type = account_type_map.get(
                    venue_cfg.venue.account_type, AccountType.MARGIN
                )

                # Determine OMS type based on hedge mode
                oms_type = OmsType.HEDGING if venue_cfg.trading.hedge_mode else OmsType.NETTING

                # Convert starting balances
                starting_balances = []
                for balance in venue_config.starting_balances:
                    currency = Currency.from_str(balance.currency)
                    starting_balances.append(Money(balance.total, currency))

                # Add venue to engine
                engine.add_venue(
                    venue=venue,
                    oms_type=oms_type,
                    account_type=account_type,
                    starting_balances=starting_balances,
                    base_currency=None,  # Will use first balance currency
                )
                self.console.print(
                    f"[green]✓[/green] Added venue: {venue.value} "
                    f"(oms={oms_type.name}, type={account_type.name})"
                )

            except Exception as e:
                msg = f"Failed to add venue from {venue_config.config_path}: {e}"
                self.console.print(f"[red]Error: {msg}[/red]")
                raise ValueError(msg) from e

        return engine

    def add_data_to_engine(self, engine: BacktestEngine, data: dict) -> None:
        """
        Add loaded data to the backtest engine.

        Parameters
        ----------
        engine : BacktestEngine
            Backtest engine to add data to
        data : dict
            Dictionary of loaded data by type

        """
        total_items = 0

        if "bars" in data and data["bars"]:
            engine.add_data(data["bars"])
            total_items += len(data["bars"])

        if "trade_ticks" in data and data["trade_ticks"]:
            engine.add_data(data["trade_ticks"])
            total_items += len(data["trade_ticks"])

        if "quote_ticks" in data and data["quote_ticks"]:
            engine.add_data(data["quote_ticks"])
            total_items += len(data["quote_ticks"])

        if "order_book_deltas" in data and data["order_book_deltas"]:
            engine.add_data(data["order_book_deltas"])
            total_items += len(data["order_book_deltas"])

        if "fundingratesing" in data and data["fundingratesing"]:
            engine.add_data(data["fundingratesing"])
            total_items += len(data["fundingratesing"])

        if "markprices" in data and data["markprices"]:
            engine.add_data(data["markprices"])
            total_items += len(data["markprices"])

        self.console.print(f"[green]✓[/green] Added {total_items:,} data items to engine")

    def add_strategies(self, engine: BacktestEngine) -> None:
        """
        Add strategies to the engine.

        Parameters
        ----------
        engine : BacktestEngine
            Backtest engine to add strategies to

        """
        for strategy_config in self.strategy_configs:
            strategy = HedgeGridV1(config=strategy_config)
            engine.add_strategy(strategy)
            self.console.print(f"[green]✓[/green] Added strategy: {strategy.id}")

    def run(self, catalog: ParquetDataCatalog) -> tuple[BacktestEngine, dict]:
        """
        Execute the complete backtest.

        Parameters
        ----------
        catalog : ParquetDataCatalog
            Data catalog for loading data

        Returns
        -------
        tuple[BacktestEngine, dict]
            Backtest engine and loaded data dictionary

        """
        # Load instruments
        instruments = self.load_instruments(catalog)

        # Load data
        data = self.load_data(catalog)

        # Setup engine
        engine = self.setup_engine()

        # Add instruments
        for instrument in instruments:
            engine.add_instrument(instrument)

        # Add data
        self.add_data_to_engine(engine, data)

        # Add strategies
        self.add_strategies(engine)

        # Run backtest
        self.console.print("\n[bold cyan]Running backtest...[/bold cyan]")
        start_time = datetime.now()

        try:
            engine.run()
        except Exception as e:
            self.console.print(f"[red]Error during backtest: {e}[/red]")
            raise

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        self.console.print(f"[green]✓[/green] Backtest completed in {duration:.2f}s\n")

        return engine, data

    def extract_results(self, engine: BacktestEngine) -> dict:
        """
        Extract results from the completed backtest.

        Parameters
        ----------
        engine : BacktestEngine
            Completed backtest engine

        Returns
        -------
        dict
            Results dictionary with account state, orders, trades, etc.

        """
        # Get portfolio
        portfolio = engine.portfolio

        # Extract venue (use first configured venue)
        venue_name = None
        if self.config.venues:
            venue_cfg_path = Path(self.config.venues[0].config_path)
            venue_cfg = VenueConfigLoader.load(venue_cfg_path)
            venue_name = venue_cfg.venue.name

        if not venue_name:
            self.console.print("[yellow]Warning: No venue found for results extraction[/yellow]")
            return {
                "run_id": self.run_id,
                "config": self.config.model_dump(mode="python"),
                "account": {},
                "orders": [],
                "positions": [],
                "trades": [],
            }

        venue = Venue(venue_name)
        account = portfolio.account(venue)

        # Get currency from starting balances (use first balance currency)
        currency = None
        if self.config.venues and self.config.venues[0].starting_balances:
            currency = Currency.from_str(self.config.venues[0].starting_balances[0].currency)

        # Build results dict
        results = {
            "run_id": self.run_id,
            "config": self.config.model_dump(mode="python"),
            "account": {
                "balance_total": float(account.balance_total(currency).as_double())
                if account and currency
                else 0.0,
                "balance_free": float(account.balance_free(currency).as_double())
                if account and currency
                else 0.0,
                "balance_locked": float(account.balance_locked(currency).as_double())
                if account and currency
                else 0.0,
            },
            "orders": [],
            "positions": [],
            "trades": [],
        }

        # Extract fills from orders
        try:
            for order in engine.cache.orders():
                if order.is_filled or order.filled_qty.as_double() > 0:
                    order_dict = {
                        "client_order_id": str(order.client_order_id),
                        "venue_order_id": str(order.venue_order_id) if order.venue_order_id else None,
                        "side": str(order.side),
                        "quantity": float(order.quantity.as_double()),
                        "filled_qty": float(order.filled_qty.as_double()),
                        "avg_px": float(order.avg_px) if order.avg_px else None,
                        "status": str(order.status),
                    }
                    results["orders"].append(order_dict)
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to extract orders: {e}[/yellow]")

        # Extract positions
        try:
            for position in engine.cache.positions():
                position_dict = {
                    "position_id": str(position.id),
                    "instrument_id": str(position.instrument_id),
                    "side": str(position.side),
                    "quantity": float(position.quantity.as_double()),
                    "entry_price": float(position.avg_px_open),
                    "realized_pnl": float(position.realized_pnl.as_double()),
                    "unrealized_pnl": float(position.unrealized_pnl().as_double()),
                }
                results["positions"].append(position_dict)
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to extract positions: {e}[/yellow]")

        return results

    def calculate_metrics(self, results: dict) -> dict:
        """
        Calculate performance metrics from backtest results.

        Parameters
        ----------
        results : dict
            Backtest results dictionary

        Returns
        -------
        dict
            Performance metrics

        """
        metrics = {
            "total_orders": len(results.get("orders", [])),
            "total_positions": len(results.get("positions", [])),
            "final_balance": results.get("account", {}).get("balance_total", 0.0),
        }

        # Calculate total PnL from positions
        total_realized_pnl = sum(
            pos.get("realized_pnl", 0.0) for pos in results.get("positions", [])
        )
        total_unrealized_pnl = sum(
            pos.get("unrealized_pnl", 0.0) for pos in results.get("positions", [])
        )

        metrics["total_realized_pnl"] = total_realized_pnl
        metrics["total_unrealized_pnl"] = total_unrealized_pnl
        metrics["total_pnl"] = total_realized_pnl + total_unrealized_pnl

        # Calculate fill rate
        filled_orders = sum(1 for o in results.get("orders", []) if o.get("filled_qty", 0) > 0)
        metrics["fill_rate"] = (
            filled_orders / metrics["total_orders"] if metrics["total_orders"] > 0 else 0.0
        )

        return metrics

    def save_artifacts(self, results: dict, metrics: dict) -> Path:
        """
        Save backtest artifacts to disk.

        Creates a directory structure with configuration, summary,
        trades CSV, and metrics CSV.

        Parameters
        ----------
        results : dict
            Backtest results dictionary
        metrics : dict
            Performance metrics

        Returns
        -------
        Path
            Output directory path

        """
        output_dir = Path(self.config.output.report_dir) / self.run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save config
        config_path = output_dir / "config.json"
        try:
            with open(config_path, "w") as f:
                json.dump(results["config"], f, indent=2, default=str)
            self.console.print(f"[green]✓[/green] Saved config: {config_path}")
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to save config: {e}[/yellow]")

        # Save summary
        summary_path = output_dir / "summary.json"
        summary = {
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(),
            "backtest_name": self.config.backtest.name,
            "description": self.config.backtest.description,
            "time_range": {
                "start": self.config.time_range.start_time.isoformat(),
                "end": self.config.time_range.end_time.isoformat(),
            },
            "metrics": metrics,
            "account": results["account"],
        }
        try:
            with open(summary_path, "w") as f:
                json.dump(summary, f, indent=2, default=str)
            self.console.print(f"[green]✓[/green] Saved summary: {summary_path}")
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to save summary: {e}[/yellow]")

        # Save orders CSV
        if results.get("orders") and self.config.output.save_trades:
            orders_df = pd.DataFrame(results["orders"])
            orders_path = output_dir / "orders.csv"
            try:
                orders_df.to_csv(orders_path, index=False)
                self.console.print(f"[green]✓[/green] Saved orders: {orders_path}")
            except Exception as e:
                self.console.print(f"[yellow]Warning: Failed to save orders: {e}[/yellow]")

        # Save positions CSV
        if results.get("positions") and self.config.output.save_positions:
            positions_df = pd.DataFrame(results["positions"])
            positions_path = output_dir / "positions.csv"
            try:
                positions_df.to_csv(positions_path, index=False)
                self.console.print(f"[green]✓[/green] Saved positions: {positions_path}")
            except Exception as e:
                self.console.print(f"[yellow]Warning: Failed to save positions: {e}[/yellow]")

        # Save metrics CSV
        metrics_df = pd.DataFrame([metrics])
        metrics_path = output_dir / "metrics.csv"
        try:
            metrics_df.to_csv(metrics_path, index=False)
            self.console.print(f"[green]✓[/green] Saved metrics: {metrics_path}")
        except Exception as e:
            self.console.print(f"[yellow]Warning: Failed to save metrics: {e}[/yellow]")

        self.console.print(f"\n[bold green]✓ Artifacts saved to: {output_dir}[/bold green]")
        return output_dir

    def print_summary(self, metrics: dict) -> None:
        """
        Print console summary with Rich tables.

        Parameters
        ----------
        metrics : dict
            Performance metrics to display

        """
        table = Table(title="Performance Summary", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="magenta")

        for key, value in metrics.items():
            metric_name = key.replace("_", " ").title()
            if isinstance(value, float):
                if "pnl" in key.lower() or "balance" in key.lower():
                    formatted_value = f"${value:,.2f}"
                elif "rate" in key.lower():
                    formatted_value = f"{value:.2%}"
                else:
                    formatted_value = f"{value:.4f}"
            else:
                formatted_value = f"{value:,}"

            table.add_row(metric_name, formatted_value)

        self.console.print(table)


# CLI Interface
app = typer.Typer(
    name="run_backtest",
    help="Run backtest with Nautilus BacktestEngine",
    add_completion=False,
)
console = Console()


@app.command()
def main(
    backtest_config: str = typer.Option(
        "configs/backtest/btcusdt_mark_trades_funding.yaml",
        "--backtest-config",
        "-b",
        help="Path to backtest config YAML",
    ),
    strategy_config: str = typer.Option(
        "configs/strategies/hedge_grid_v1.yaml",
        "--strategy-config",
        "-s",
        help="Path to strategy config YAML (HedgeGridV1Config)",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        "-r",
        help="Custom run ID (auto-generated if not provided)",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Output directory for artifacts (overrides config)",
    ),
) -> None:
    """
    Run backtest with Nautilus BacktestEngine.

    This command:
    1. Loads backtest and strategy configurations
    2. Sets up data catalog and loads market data
    3. Configures Nautilus engine with venues and strategies
    4. Runs the backtest simulation
    5. Extracts results and calculates metrics
    6. Saves artifacts to disk

    Example:
        uv run python -m naut_hedgegrid.runners.run_backtest \\
            --backtest-config configs/backtest/my_backtest.yaml \\
            --strategy-config configs/strategies/hedge_grid_v1.yaml

    """
    console.rule("[bold cyan]Nautilus Backtest Runner[/bold cyan]")

    try:
        # Load backtest config
        console.print("\n[bold]Loading configurations...[/bold]")
        bt_config_path = Path(backtest_config)
        if not bt_config_path.exists():
            console.print(f"[red]Error: Backtest config not found: {bt_config_path}[/red]")
            raise typer.Exit(code=1)

        bt_config = BacktestConfigLoader.load(bt_config_path)
        console.print(f"[green]✓[/green] Backtest config: {bt_config_path}")

        # Override output directory if provided
        if output_dir:
            bt_config.output.report_dir = output_dir

        # Load strategy configs from backtest config
        strat_configs = []
        for strat_config_entry in bt_config.strategies:
            if not strat_config_entry.enabled:
                console.print(
                    f"[yellow]⚠[/yellow] Strategy {strat_config_entry.config_path} disabled, skipping"
                )
                continue

            strat_config_path = Path(strat_config_entry.config_path)
            if not strat_config_path.exists():
                console.print(f"[yellow]Warning: Strategy config not found: {strat_config_path}[/yellow]")
                continue

            # Load HedgeGridConfig
            hedge_grid_cfg = HedgeGridConfigLoader.load(strat_config_path)

            # Get instrument from config
            instrument_id = hedge_grid_cfg.strategy.instrument_id

            # Create HedgeGridV1Config
            # Note: bar_type is constructed inside the strategy, not passed in config
            strat_cfg = HedgeGridV1Config(
                instrument_id=instrument_id,
                hedge_grid_config_path=str(strat_config_path),
                oms_type=OmsType.HEDGING,
            )
            strat_configs.append(strat_cfg)
            console.print(f"[green]✓[/green] Strategy config: {strat_config_path}")

        if not strat_configs:
            console.print("[red]Error: No enabled strategies found[/red]")
            raise typer.Exit(code=1)

        # Create runner
        runner = BacktestRunner(
            backtest_config=bt_config,
            strategy_configs=strat_configs,
            console=console,
        )

        # Override run_id if provided
        if run_id:
            runner.run_id = run_id

        console.print(f"[cyan]Run ID: {runner.run_id}[/cyan]\n")

        # Setup catalog
        catalog = runner.setup_catalog()

        # Run backtest
        engine, data = runner.run(catalog)

        # Extract results
        console.print("[bold]Extracting results...[/bold]")
        results = runner.extract_results(engine)

        # Calculate metrics
        console.print("[bold]Calculating metrics...[/bold]")
        metrics = runner.calculate_metrics(results)

        # Save artifacts
        console.print("\n[bold]Saving artifacts...[/bold]")
        output_path = runner.save_artifacts(results, metrics)

        # Print summary
        console.print("")
        runner.print_summary(metrics)

        console.rule("[bold green]Backtest Complete[/bold green]")
        console.print(f"\n[bold]Results saved to:[/bold] {output_path}\n")

    except FileNotFoundError as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except ValueError as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        import traceback

        console.print(f"[red]{traceback.format_exc()}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
