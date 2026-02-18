"""Unified CLI for NautilusTrader HedgeGrid trading system.

This module provides a consolidated command-line interface for all trading
operations, including backtesting, paper trading, live trading, and operational
controls.

Commands:
    backtest - Run historical backtests
    paper    - Run paper trading with simulated execution
    live     - Run live trading with real execution
    flatten  - Flatten positions on running strategy
    status   - Query running strategy status
    metrics  - Query Prometheus metrics

Usage:
    uv run python -m naut_hedgegrid <command> [options]

Examples:
    # Run backtest
    uv run python -m naut_hedgegrid backtest \\
        --backtest-config configs/backtest/my_bt.yaml \\
        --strategy-config configs/strategies/hedge_grid_v1.yaml

    # Start paper trading
    uv run python -m naut_hedgegrid paper \\
        --strategy-config configs/strategies/hedge_grid_v1.yaml \\
        --venue-config configs/venues/binance_futures.yaml \\
        --enable-ops

    # Flatten LONG positions
    uv run python -m naut_hedgegrid flatten --side LONG

    # Query running strategy status
    uv run python -m naut_hedgegrid status

    # Query Prometheus metrics
    uv run python -m naut_hedgegrid metrics
"""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Initialize Typer app
app = typer.Typer(
    name="naut-hedgegrid",
    help="NautilusTrader HedgeGrid Trading System",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


# ============================================================================
# TRADING COMMANDS
# ============================================================================


@app.command()
def backtest(
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
    """Run backtest with Nautilus BacktestEngine.

    This command loads historical data from a Parquet catalog, configures the
    Nautilus backtest engine with specified strategy and venue parameters, runs
    the simulation, and saves results to disk.

    The backtest configuration defines:
    - Data sources (catalog path, instruments, date range)
    - Venue parameters (starting balances, fees, execution simulation)
    - Strategy configurations to run
    - Output settings (report directory, metrics to calculate)

    Results include:
    - Trade history with fill details
    - Position snapshots over time
    - Performance metrics (Sharpe, Sortino, max drawdown, etc.)
    - Account balance evolution

    Examples:
        # Run with default configs
        uv run python -m naut_hedgegrid backtest

        # Run with custom configs
        uv run python -m naut_hedgegrid backtest \\
            -b configs/backtest/my_backtest.yaml \\
            -s configs/strategies/hedge_grid_v1.yaml

        # Override output directory
        uv run python -m naut_hedgegrid backtest \\
            --output-dir ./my_results

        # Custom run ID for tracking
        uv run python -m naut_hedgegrid backtest \\
            --run-id my_experiment_v1
    """
    # Import here to avoid circular dependencies
    from nautilus_trader.model.enums import OmsType

    from naut_hedgegrid.config.backtest import BacktestConfigLoader
    from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
    from naut_hedgegrid.runners.run_backtest import BacktestRunner
    from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1Config

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
                console.print(f"[yellow]⚠[/yellow] Strategy {strat_config_entry.config_path} disabled, skipping")
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
        engine, _data = runner.run(catalog)

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


@app.command()
def paper(
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
    enable_ops: bool = typer.Option(
        False,
        "--enable-ops",
        help="Enable Prometheus metrics and FastAPI control endpoints",
    ),
    prometheus_port: int = typer.Option(
        9090,
        "--prometheus-port",
        help="Port for Prometheus metrics endpoint",
    ),
    api_port: int = typer.Option(
        8080,
        "--api-port",
        help="Port for FastAPI control endpoints",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key for FastAPI authentication (optional)",
    ),
) -> None:
    """Run paper trading with simulated execution.

    This command connects to real market data (WebSocket + REST) from Binance
    but simulates all order execution locally. No real orders are placed,
    making it ideal for testing strategies before live deployment.

    Paper trading provides:
    - Real-time market data feed
    - Simulated order fills based on market conditions
    - Full strategy execution without financial risk
    - Optional Prometheus metrics and FastAPI control endpoints

    When --enable-ops is set, the following services start:
    - Prometheus metrics at http://localhost:9090/metrics
    - FastAPI control endpoints at http://localhost:8080/docs

    Examples:
        # Basic paper trading
        uv run python -m naut_hedgegrid paper

        # With operational infrastructure
        uv run python -m naut_hedgegrid paper --enable-ops

        # Custom ports
        uv run python -m naut_hedgegrid paper \\
            --enable-ops \\
            --prometheus-port 9091 \\
            --api-port 8081

        # With API authentication
        uv run python -m naut_hedgegrid paper \\
            --enable-ops \\
            --api-key my_secret_key
    """
    from naut_hedgegrid.runners.base_runner import PaperRunner

    runner = PaperRunner()
    runner.run(
        strategy_config=strategy_config,
        venue_config=venue_config,
        require_api_keys=True,
        enable_ops=enable_ops,
        prometheus_port=prometheus_port,
        api_port=api_port,
        api_key=api_key,
    )


@app.command()
def live(
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
    enable_ops: bool = typer.Option(
        False,
        "--enable-ops",
        help="Enable Prometheus metrics and FastAPI control endpoints",
    ),
    prometheus_port: int = typer.Option(
        9090,
        "--prometheus-port",
        help="Port for Prometheus metrics endpoint",
    ),
    api_port: int = typer.Option(
        8080,
        "--api-port",
        help="Port for FastAPI control endpoints",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key for FastAPI authentication (optional)",
    ),
) -> None:
    """Run live trading with REAL execution on Binance Futures.

    ⚠️  WARNING: This mode places REAL ORDERS with REAL MONEY ⚠️

    This command connects to Binance Futures with both market data and
    execution capabilities. All orders are placed on the exchange using
    real funds.

    Requirements:
    - BINANCE_API_KEY environment variable
    - BINANCE_API_SECRET environment variable
    - Thoroughly tested strategy (use paper trading first!)
    - Adequate account balance
    - Risk management controls configured

    When --enable-ops is set, the following services start:
    - Prometheus metrics at http://localhost:9090/metrics
    - FastAPI control endpoints at http://localhost:8080/docs

    The operational endpoints allow you to:
    - Flatten positions in emergency situations
    - Query strategy status in real-time
    - Adjust strategy parameters dynamically
    - Monitor risk metrics

    Examples:
        # Set API credentials first
        export BINANCE_API_KEY=your_key
        export BINANCE_API_SECRET=your_secret

        # Start live trading
        uv run python -m naut_hedgegrid live --enable-ops

        # With custom ports
        uv run python -m naut_hedgegrid live \\
            --enable-ops \\
            --prometheus-port 9091 \\
            --api-port 8081

        # With API authentication (recommended for production)
        uv run python -m naut_hedgegrid live \\
            --enable-ops \\
            --api-key my_secret_key
    """
    from naut_hedgegrid.runners.base_runner import LiveRunner

    # Show warning panel
    warning_panel = Panel(
        "[bold red]⚠️  LIVE TRADING MODE - REAL MONEY AT RISK  ⚠️[/bold red]\n\n"
        "This command will place REAL ORDERS on Binance Futures.\n"
        "All trades will execute with REAL MONEY.\n\n"
        "[yellow]Ensure you have:[/yellow]\n"
        "  ✓ Thoroughly tested your strategy in paper trading\n"
        "  ✓ Set appropriate position limits\n"
        "  ✓ Configured stop losses and risk controls\n"
        "  ✓ Adequate account balance\n"
        "  ✓ BINANCE_API_KEY and BINANCE_API_SECRET set\n\n"
        "[bold]Press CTRL-C at any time to stop trading.[/bold]",
        title="DANGER",
        border_style="red",
    )
    console.print(warning_panel)
    console.print()

    # Prompt for confirmation
    confirm = typer.confirm(
        "Do you want to proceed with LIVE TRADING?",
        default=False,
    )

    if not confirm:
        console.print("[yellow]Live trading cancelled by user.[/yellow]")
        raise typer.Exit(code=0)

    console.print()

    runner = LiveRunner()
    runner.run(
        strategy_config=strategy_config,
        venue_config=venue_config,
        require_api_keys=True,
        enable_ops=enable_ops,
        prometheus_port=prometheus_port,
        api_port=api_port,
        api_key=api_key,
    )


# ============================================================================
# OPERATIONAL CONTROL COMMANDS
# ============================================================================


@app.command()
def flatten(
    side: str = typer.Option(
        "BOTH",
        "--side",
        "-s",
        help="Side to flatten (LONG, SHORT, or BOTH)",
    ),
    host: str = typer.Option(
        "localhost",
        "--host",
        "-h",
        help="FastAPI server host",
    ),
    port: int = typer.Option(
        8080,
        "--port",
        "-p",
        help="FastAPI server port",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key for authentication (if required)",
    ),
) -> None:
    """Flatten positions on a running strategy.

    This command sends a flatten request to a running paper or live trading
    instance via its FastAPI control endpoint. The running instance must have
    been started with --enable-ops for this to work.

    The flatten operation:
    1. Cancels all open orders for the specified side
    2. Closes all open positions for the specified side
    3. Returns immediately after initiating the flatten

    Options:
    - LONG: Flatten only long positions
    - SHORT: Flatten only short positions
    - BOTH: Flatten all positions (default)

    Use this command in emergency situations when you need to quickly exit
    all positions and cancel pending orders.

    Examples:
        # Flatten all positions
        uv run python -m naut_hedgegrid flatten

        # Flatten only LONG positions
        uv run python -m naut_hedgegrid flatten --side LONG

        # Flatten on remote host with authentication
        uv run python -m naut_hedgegrid flatten \\
            --host 192.168.1.100 \\
            --port 8080 \\
            --api-key my_secret_key
    """
    try:
        import requests
    except ImportError:
        console.print("[red]Error: requests library not installed[/red]")
        console.print("[yellow]Install with: uv add requests[/yellow]")
        raise typer.Exit(code=1)

    # Validate side parameter
    side_upper = side.upper()
    if side_upper not in ("LONG", "SHORT", "BOTH"):
        console.print(f"[red]Error: Invalid side '{side}'. Must be LONG, SHORT, or BOTH[/red]")
        raise typer.Exit(code=1)

    # Build URL
    url = f"http://{host}:{port}/api/v1/flatten/{side_upper.lower()}"

    # Prepare headers
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    console.print(f"[cyan]Sending flatten request to {url}...[/cyan]")

    try:
        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse response
        result = response.json()

        # Display result
        console.print(f"\n[green]✓ Flatten {side_upper} completed successfully[/green]\n")

        table = Table(title="Flatten Result")
        table.add_column("Action", style="cyan")
        table.add_column("Count", style="magenta")

        table.add_row("Cancelled Orders", str(result.get("cancelled_orders", 0)))
        table.add_row("Closing Positions", str(result.get("closing_positions", 0)))

        console.print(table)

    except requests.exceptions.ConnectionError:
        console.print(f"\n[red]Error: Could not connect to {host}:{port}[/red]")
        console.print("\n[yellow]Make sure paper/live trading is running with --enable-ops[/yellow]")
        console.print("[yellow]Example: uv run python -m naut_hedgegrid paper --enable-ops[/yellow]")
        raise typer.Exit(code=1)
    except requests.exceptions.HTTPError as e:
        console.print(f"\n[red]HTTP Error: {e}[/red]")
        if e.response.status_code == 401:
            console.print("[yellow]Hint: API key may be required or incorrect[/yellow]")
        elif e.response.status_code == 404:
            console.print("[yellow]Hint: API endpoint not found - ensure ops are enabled[/yellow]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def status(
    host: str = typer.Option(
        "localhost",
        "--host",
        "-h",
        help="FastAPI server host",
    ),
    port: int = typer.Option(
        8080,
        "--port",
        "-p",
        help="FastAPI server port",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key for authentication (if required)",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table or json)",
    ),
) -> None:
    """Query running strategy status.

    This command retrieves operational metrics from a running paper or live
    trading instance via its FastAPI control endpoint. The running instance
    must have been started with --enable-ops for this to work.

    The status includes:
    - Position inventories (LONG, SHORT, NET)
    - Active grid rungs per side
    - Open order count
    - Risk metrics (margin ratio, maker ratio)
    - Funding rate information
    - PnL breakdown (realized, unrealized, total)
    - System health (uptime, last bar timestamp)

    Examples:
        # Query local instance
        uv run python -m naut_hedgegrid status

        # Query remote instance with authentication
        uv run python -m naut_hedgegrid status \\
            --host 192.168.1.100 \\
            --port 8080 \\
            --api-key my_secret_key

        # JSON output for scripting
        uv run python -m naut_hedgegrid status --format json
    """
    try:
        import requests
    except ImportError:
        console.print("[red]Error: requests library not installed[/red]")
        console.print("[yellow]Install with: uv add requests[/yellow]")
        raise typer.Exit(code=1)

    # Build URL
    url = f"http://{host}:{port}/api/v1/status"

    # Prepare headers
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    console.print(f"[cyan]Querying status from {url}...[/cyan]\n")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse response
        metrics = response.json()

        if format.lower() == "json":
            # JSON output
            console.print(json.dumps(metrics, indent=2))
        else:
            # Table output
            table = Table(title="Strategy Status", show_header=True, header_style="bold cyan")
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", style="magenta")

            # Group metrics by category

            # Add rows with formatting
            for key, value in metrics.items():
                metric_name = key.replace("_", " ").title()

                # Format value based on type
                if isinstance(value, float):
                    if "usdt" in key.lower():
                        formatted_value = f"${value:,.2f}"
                    elif "ratio" in key.lower() or "rate" in key.lower():
                        formatted_value = f"{value:.4f}"
                    else:
                        formatted_value = f"{value:.2f}"
                else:
                    formatted_value = str(value)

                table.add_row(metric_name, formatted_value)

            console.print(table)

    except requests.exceptions.ConnectionError:
        console.print(f"[red]Error: Could not connect to {host}:{port}[/red]")
        console.print("\n[yellow]Make sure paper/live trading is running with --enable-ops[/yellow]")
        console.print("[yellow]Example: uv run python -m naut_hedgegrid paper --enable-ops[/yellow]")
        raise typer.Exit(code=1)
    except requests.exceptions.HTTPError as e:
        console.print(f"[red]HTTP Error: {e}[/red]")
        if e.response.status_code == 401:
            console.print("[yellow]Hint: API key may be required or incorrect[/yellow]")
        elif e.response.status_code == 404:
            console.print("[yellow]Hint: API endpoint not found - ensure ops are enabled[/yellow]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def metrics(
    host: str = typer.Option(
        "localhost",
        "--host",
        "-h",
        help="Prometheus server host",
    ),
    port: int = typer.Option(
        9090,
        "--prometheus-port",
        "-p",
        help="Prometheus server port",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table or raw)",
    ),
) -> None:
    """Query Prometheus metrics endpoint.

    This command retrieves Prometheus metrics from a running paper or live
    trading instance. The running instance must have been started with
    --enable-ops for the metrics server to be available.

    Prometheus metrics include all operational metrics in a standardized
    format suitable for time-series monitoring and alerting.

    Metrics are available at http://localhost:9090/metrics by default.

    Examples:
        # Query local Prometheus endpoint
        uv run python -m naut_hedgegrid metrics

        # Query remote instance
        uv run python -m naut_hedgegrid metrics \\
            --host 192.168.1.100 \\
            --prometheus-port 9090

        # Raw Prometheus format
        uv run python -m naut_hedgegrid metrics --format raw
    """
    try:
        import requests
    except ImportError:
        console.print("[red]Error: requests library not installed[/red]")
        console.print("[yellow]Install with: uv add requests[/yellow]")
        raise typer.Exit(code=1)

    # Build URL
    url = f"http://{host}:{port}/metrics"

    console.print(f"[cyan]Querying metrics from {url}...[/cyan]\n")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        if format.lower() == "raw":
            # Raw Prometheus output
            console.print(response.text)
        else:
            # Parse and display as table
            lines = response.text.strip().split("\n")
            metrics_data = []

            for line in lines:
                # Skip comments and empty lines
                if line.startswith("#") or not line.strip():
                    continue

                # Parse metric line
                if "{" in line:
                    # Has labels
                    metric_name = line.split("{")[0]
                    rest = line.split(" ")[-1]
                    value = rest
                else:
                    # No labels
                    parts = line.split()
                    if len(parts) >= 2:
                        metric_name = parts[0]
                        value = parts[1]
                    else:
                        continue

                metrics_data.append((metric_name, value))

            # Display as table
            table = Table(title="Prometheus Metrics", show_header=True, header_style="bold cyan")
            table.add_column("Metric", style="cyan", no_wrap=False)
            table.add_column("Value", style="magenta")

            for metric_name, value in metrics_data:
                table.add_row(metric_name, value)

            console.print(table)
            console.print(f"\n[green]Total metrics: {len(metrics_data)}[/green]")

    except requests.exceptions.ConnectionError:
        console.print(f"[red]Error: Could not connect to {host}:{port}[/red]")
        console.print("\n[yellow]Make sure paper/live trading is running with --enable-ops[/yellow]")
        console.print("[yellow]Example: uv run python -m naut_hedgegrid paper --enable-ops[/yellow]")
        raise typer.Exit(code=1)
    except requests.exceptions.HTTPError as e:
        console.print(f"[red]HTTP Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


if __name__ == "__main__":
    app()
