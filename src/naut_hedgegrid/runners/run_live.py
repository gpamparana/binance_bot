"""Nautilus live trading runner with real execution on Binance Futures."""

import typer

from naut_hedgegrid.runners.base_runner import LiveRunner

# CLI Interface
app = typer.Typer(
    name="run_live",
    help="Run live trading with Nautilus TradingNode (REAL EXECUTION)",
    add_completion=False,
)


@app.command()
def main(
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
    api_key: str = typer.Option(
        None,
        "--api-key",
        help="API key for FastAPI authentication (optional)",
    ),
) -> None:
    """Run live trading with REAL execution on Binance Futures.

    This command:
    1. Validates API keys from environment variables
    2. Loads strategy and venue configurations
    3. Connects to Binance data feed AND execution endpoint
    4. Runs strategy with REAL order placement
    5. Optionally starts operational infrastructure (Prometheus + FastAPI)
    6. Handles graceful shutdown on CTRL-C

    WARNING: This mode places REAL ORDERS with REAL MONEY.
    Ensure your strategy is thoroughly tested in paper trading first.

    Example:
        export BINANCE_API_KEY=your_key
        export BINANCE_API_SECRET=your_secret
        uv run python -m naut_hedgegrid.runners.run_live \\
            --strategy-config configs/strategies/hedge_grid_v1.yaml \\
            --venue-config configs/venues/binance_futures.yaml \\
            --enable-ops \\
            --prometheus-port 9090 \\
            --api-port 8080
    """
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


if __name__ == "__main__":
    app()
