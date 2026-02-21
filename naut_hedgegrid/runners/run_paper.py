"""Nautilus paper trading runner with simulated execution."""

import typer

from naut_hedgegrid.runners.base_runner import PaperRunner

# CLI Interface
app = typer.Typer(
    name="run_paper",
    help="Run paper trading with Nautilus TradingNode (simulated execution)",
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
        "configs/venues/binance_futures_testnet.yaml",
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
    """Run paper trading with simulated execution.

    This command:
    1. Loads strategy and venue configurations
    2. Connects to Binance data feed (WebSocket + REST)
    3. Runs strategy with simulated fills (no real orders)
    4. Optionally starts operational infrastructure (Prometheus + FastAPI)
    5. Handles graceful shutdown on CTRL-C

    Paper trading provides realistic market data with zero execution risk,
    ideal for testing strategies before live deployment.

    Example:
        uv run python -m naut_hedgegrid.runners.run_paper \\
            --strategy-config configs/strategies/hedge_grid_v1.yaml \\
            --venue-config configs/venues/binance_futures.yaml \\
            --enable-ops \\
            --prometheus-port 9090 \\
            --api-port 8080
    """
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


if __name__ == "__main__":
    app()
