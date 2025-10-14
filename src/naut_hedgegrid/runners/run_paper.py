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
        "configs/venues/binance_futures.yaml",
        "--venue-config",
        "-v",
        help="Path to venue config YAML",
    ),
) -> None:
    """Run paper trading with simulated execution.

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
    runner = PaperRunner()
    runner.run(
        strategy_config=strategy_config,
        venue_config=venue_config,
        require_api_keys=False,
    )


if __name__ == "__main__":
    app()
