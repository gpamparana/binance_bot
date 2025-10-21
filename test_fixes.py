#!/usr/bin/env python
"""Test script to verify the grid reset fix and warmup implementation."""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console

from naut_hedgegrid.warmup.binance_warmer import BinanceDataWarmer
from naut_hedgegrid.strategy.detector import Bar as DetectorBar, RegimeDetector
from naut_hedgegrid.config.venue import VenueConfigLoader

console = Console()


async def test_warmup_functionality():
    """Test that the warmup system works correctly."""
    console.print("\n[bold cyan]Testing Warmup Functionality[/bold cyan]")
    console.print("=" * 60)

    # Load venue config to determine testnet/production
    try:
        venue_cfg = VenueConfigLoader.load("configs/venues/binance_futures_testnet.yaml")
        console.print(f"[green]✓[/green] Loaded venue config: testnet={venue_cfg.api.testnet}")
    except Exception as e:
        console.print(f"[red]✗ Failed to load venue config: {e}[/red]")
        return False

    # Create warmer
    warmer = BinanceDataWarmer(
        testnet=venue_cfg.api.testnet,
        api_key=venue_cfg.api.api_key,
        api_secret=venue_cfg.api.api_secret,
    )
    console.print(f"[green]✓[/green] Created BinanceDataWarmer (testnet={venue_cfg.api.testnet})")

    # Test fetching historical data
    try:
        bars = await warmer.get_historical_bars(
            symbol="BTCUSDT",
            interval="1m",
            limit=70,
        )
        console.print(f"[green]✓[/green] Fetched {len(bars)} historical bars")

        if bars:
            first_bar = bars[0]
            last_bar = bars[-1]
            console.print(f"  First bar: {first_bar.timestamp} - Close: {first_bar.close:.2f}")
            console.print(f"  Last bar:  {last_bar.timestamp} - Close: {last_bar.close:.2f}")
    except Exception as e:
        console.print(f"[red]✗ Failed to fetch historical data: {e}[/red]")
        return False

    # Test regime detector warmup
    console.print("\n[bold]Testing Regime Detector Warmup[/bold]")
    regime_detector = RegimeDetector(
        ema_fast=21,
        ema_slow=50,
        adx_len=14,
        atr_len=14,
        hysteresis_bps=10.0,
    )

    # Convert bars to detector format
    detector_bars = [
        DetectorBar(
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in bars
    ]

    # Feed bars to detector
    console.print(f"  Initial state: warm={regime_detector.is_warm}, regime={regime_detector.current()}")

    for i, bar in enumerate(detector_bars):
        regime_detector.update_from_bar(bar)

        # Show progress
        if (i + 1) % 20 == 0:
            console.print(f"  Progress: {i + 1}/{len(detector_bars)} bars processed")

    # Check final state
    console.print(f"  Final state: warm={regime_detector.is_warm}, regime={regime_detector.current()}")

    if regime_detector.is_warm:
        console.print(f"[green]✓[/green] Regime detector successfully warmed up")
        console.print(f"  EMA Fast: {regime_detector.ema_fast.value:.2f}")
        console.print(f"  EMA Slow: {regime_detector.ema_slow.value:.2f}")
        console.print(f"  ADX: {regime_detector.adx.value:.2f}")
        return True
    else:
        console.print(f"[red]✗ Regime detector not warm after {len(detector_bars)} bars[/red]")
        return False


def test_grid_center_fix():
    """Test that the grid center fix is in place."""
    console.print("\n[bold cyan]Testing Grid Center Fix[/bold cyan]")
    console.print("=" * 60)

    strategy_file = Path("naut_hedgegrid/strategies/hedge_grid_v1/strategy.py")

    # Read the strategy file
    with open(strategy_file, "r") as f:
        content = f.read()

    # Check if the fix is present
    if "mid=self._grid_center,  # Use stable grid center, not current price" in content:
        console.print("[green]✓[/green] Grid center fix is present in strategy.py:333")
        console.print("  Grid will now use stable center instead of current price")
        return True
    else:
        console.print("[red]✗ Grid center fix NOT found in strategy.py[/red]")
        console.print("  Expected: mid=self._grid_center")
        console.print("  This will cause grids to reset every bar")
        return False


def test_debug_logging():
    """Test that debug logging for grid deviation is in place."""
    console.print("\n[bold cyan]Testing Debug Logging[/bold cyan]")
    console.print("=" * 60)

    strategy_file = Path("naut_hedgegrid/strategies/hedge_grid_v1/strategy.py")

    # Read the strategy file
    with open(strategy_file, "r") as f:
        content = f.read()

    # Check if debug logging is present
    if "# Log grid center vs current price for debugging" in content:
        console.print("[green]✓[/green] Debug logging for grid center deviation is present")
        console.print("  Will log: Grid center, Current mid, Deviation in bps")
        return True
    else:
        console.print("[yellow]⚠[/yellow] Debug logging for grid center not found")
        console.print("  Consider adding it for better monitoring")
        return False


async def main():
    """Run all tests."""
    console.print("\n[bold cyan]====== Testing Grid Reset Fix and Warmup System ======[/bold cyan]\n")

    results = []

    # Test 1: Grid center fix
    results.append(("Grid Center Fix", test_grid_center_fix()))

    # Test 2: Debug logging
    results.append(("Debug Logging", test_debug_logging()))

    # Test 3: Warmup functionality
    warmup_result = await test_warmup_functionality()
    results.append(("Warmup System", warmup_result))

    # Summary
    console.print("\n[bold cyan]====== Test Summary ======[/bold cyan]")
    all_passed = True
    for test_name, passed in results:
        status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
        console.print(f"  {test_name:20} {status}")
        if not passed:
            all_passed = False

    console.print()
    if all_passed:
        console.print("[bold green]🎉 All tests passed! The fixes are ready for testnet.[/bold green]")
        console.print("\nNext steps:")
        console.print("1. Run paper trading to verify grid stability:")
        console.print("   [cyan]python -m naut_hedgegrid paper --venue-config configs/venues/binance_futures_testnet.yaml[/cyan]")
        console.print("\n2. Monitor logs for:")
        console.print("   - 'Grid recentering triggered' (should be rare)")
        console.print("   - 'Diff result: 0 adds, 0 cancels, 0 replaces' (should be common)")
        console.print("   - 'Regime detector warmup complete' (at startup)")
        console.print("\n3. Verify orders stay at stable prices between recenter events")
    else:
        console.print("[bold red]❌ Some tests failed. Please review and fix issues.[/bold red]")

    return all_passed


if __name__ == "__main__":
    # Check environment variables
    if not os.getenv("BINANCE_TESTNET_API_KEY"):
        console.print("[yellow]⚠ Warning: BINANCE_TESTNET_API_KEY not set[/yellow]")
        console.print("  Warmup test will fail without API credentials")
        console.print("  Set with: export BINANCE_TESTNET_API_KEY=your_key")
        console.print()

    # Run tests
    success = asyncio.run(main())
    sys.exit(0 if success else 1)