#!/usr/bin/env python3
"""Example script showing paper trading with data warmup.

This script demonstrates how the warmup system works with the paper trading runner.
It shows:
1. How historical data is fetched before trading starts
2. How the regime detector is warmed up
3. How the strategy starts with a fully warmed detector

Usage:
    python examples/paper_trade_with_warmup.py

Requirements:
    - BINANCE_API_KEY and BINANCE_API_SECRET environment variables
    - Valid strategy and venue configuration files
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel

from naut_hedgegrid.runners.paper_runner import PaperRunner


def main():
    """Run paper trading with warmup demonstration."""
    console = Console()

    # Display header
    console.rule("[bold cyan]Paper Trading with Data Warmup[/bold cyan]")
    console.print()

    # Check environment
    if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
        warning_panel = Panel(
            "[yellow]⚠ BINANCE_API_KEY and BINANCE_API_SECRET not set[/yellow]\n\n"
            "Paper trading requires API keys to fetch instrument metadata.\n"
            "The warmup system also needs API access to fetch historical data.\n\n"
            "Please set your Binance API credentials:\n"
            "  export BINANCE_API_KEY=your_key\n"
            "  export BINANCE_API_SECRET=your_secret",
            title="Environment Check",
            border_style="yellow",
        )
        console.print(warning_panel)
        console.print()
        sys.exit(1)

    # Configuration paths
    strategy_config = "configs/strategies/hedge_grid_v1.yaml"
    venue_config = "configs/venues/binance_testnet.yaml"

    # Show configuration
    info_panel = Panel(
        f"[cyan]Strategy Config:[/cyan] {strategy_config}\n"
        f"[cyan]Venue Config:[/cyan] {venue_config}\n\n"
        "[bold]What will happen:[/bold]\n"
        "1. Trading node will be built and initialized\n"
        "2. Strategy's on_start() will be called\n"
        "3. Historical bars will be fetched from Binance\n"
        "4. Regime detector will be warmed up with historical data\n"
        "5. Live data streaming will begin\n"
        "6. Strategy will trade with a fully warmed detector\n\n"
        "[yellow]Press CTRL+C to stop[/yellow]",
        title="Configuration",
        border_style="cyan",
    )
    console.print(info_panel)
    console.print()

    # Create and run paper trader
    runner = PaperRunner()

    try:
        # Run paper trading with warmup
        # The warmup happens automatically in the run() method
        runner.run(
            strategy_config=strategy_config,
            venue_config=venue_config,
            require_api_keys=False,  # Paper trading doesn't require keys for execution
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutdown requested by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback

        console.print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
