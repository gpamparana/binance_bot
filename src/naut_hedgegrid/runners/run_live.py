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
) -> None:
    """Run live trading with REAL execution on Binance Futures.

    This command:
    1. Validates API keys from environment variables
    2. Loads strategy and venue configurations
    3. Connects to Binance data feed AND execution endpoint
    4. Runs strategy with REAL order placement
    5. Handles graceful shutdown on CTRL-C

    WARNING: This mode places REAL ORDERS with REAL MONEY.
    Ensure your strategy is thoroughly tested in paper trading first.

    Example:
        export BINANCE_API_KEY=your_key
        export BINANCE_API_SECRET=your_secret
        uv run python -m naut_hedgegrid.runners.run_live \\
            --strategy-config configs/strategies/hedge_grid_v1.yaml \\
            --venue-config configs/venues/binance_futures.yaml
    """
    runner = LiveRunner()
    runner.run(
        strategy_config=strategy_config,
        venue_config=venue_config,
        require_api_keys=True,
    )


if __name__ == "__main__":
    app()
