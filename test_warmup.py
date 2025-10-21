#!/usr/bin/env python3
"""Test script for the data warmup system.

This script tests the warmup functionality in isolation without
running the full trading node.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console

from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfigLoader
from naut_hedgegrid.strategy.detector import Bar as DetectorBar, RegimeDetector
from naut_hedgegrid.warmup import BinanceDataWarmer


def test_warmup_system():
    """Test the complete warmup system."""
    console = Console()
    console.rule("[bold cyan]Testing Data Warmup System[/bold cyan]")
    console.print()

    # Load configurations
    console.print("[bold]Loading configurations...[/bold]")

    # Use example configs
    strategy_config_path = Path("configs/strategies/hedge_grid_v1.yaml")
    venue_config_path = Path("configs/venues/binance_testnet.yaml")

    if not strategy_config_path.exists():
        console.print(f"[red]Strategy config not found: {strategy_config_path}[/red]")
        return False

    if not venue_config_path.exists():
        console.print(f"[red]Venue config not found: {venue_config_path}[/red]")
        return False

    hedge_grid_cfg = HedgeGridConfigLoader.load(strategy_config_path)
    console.print(f"[green]✓[/green] Loaded strategy config: {hedge_grid_cfg.strategy.name}")

    venue_cfg = VenueConfigLoader.load(venue_config_path)
    console.print(f"[green]✓[/green] Loaded venue config: testnet={venue_cfg.api.testnet}")
    console.print()

    # Test 1: Create and test BinanceDataWarmer
    console.print("[bold]Test 1: BinanceDataWarmer[/bold]")

    try:
        with BinanceDataWarmer(venue_cfg, console) as warmer:
            # Fetch some historical bars
            symbol = "BTCUSDT"
            num_bars = 10

            console.print(f"Fetching {num_bars} bars for {symbol}...")
            bars = warmer.fetch_detector_bars(
                symbol=symbol,
                num_bars=num_bars,
                interval="1m",
            )

            if bars and len(bars) == num_bars:
                console.print(f"[green]✓[/green] Successfully fetched {len(bars)} bars")

                # Show first and last bar
                first_bar = bars[0]
                last_bar = bars[-1]
                console.print(f"  First bar: O={first_bar.open:.2f} H={first_bar.high:.2f} "
                             f"L={first_bar.low:.2f} C={first_bar.close:.2f}")
                console.print(f"  Last bar:  O={last_bar.open:.2f} H={last_bar.high:.2f} "
                             f"L={last_bar.low:.2f} C={last_bar.close:.2f}")
            else:
                console.print(f"[red]✗[/red] Expected {num_bars} bars, got {len(bars) if bars else 0}")
                return False

    except Exception as e:
        console.print(f"[red]✗[/red] BinanceDataWarmer failed: {e}")
        return False

    console.print()

    # Test 2: Create and warmup RegimeDetector
    console.print("[bold]Test 2: RegimeDetector Warmup[/bold]")

    regime_cfg = hedge_grid_cfg.regime
    detector = RegimeDetector(
        ema_fast=regime_cfg.ema_fast,
        ema_slow=regime_cfg.ema_slow,
        adx_len=regime_cfg.adx_len,
        atr_len=regime_cfg.atr_len,
        hysteresis_bps=regime_cfg.hysteresis_bps,
    )

    console.print(f"Created detector: EMA({regime_cfg.ema_fast}/{regime_cfg.ema_slow}), "
                  f"ADX({regime_cfg.adx_len}), ATR({regime_cfg.atr_len})")

    # Check initial state
    initial_warm = detector.is_warm
    initial_regime = detector.current()
    console.print(f"Initial state: warm={initial_warm}, regime={initial_regime}")

    if initial_warm:
        console.print("[yellow]⚠ Detector already warm (unexpected)[/yellow]")

    # Fetch enough bars for warmup
    warmup_bars = max(regime_cfg.ema_slow + 20, 70)

    try:
        with BinanceDataWarmer(venue_cfg, console) as warmer:
            console.print(f"Fetching {warmup_bars} bars for warmup...")

            historical_bars = warmer.fetch_detector_bars(
                symbol="BTCUSDT",
                num_bars=warmup_bars,
                interval="1m",
            )

            if not historical_bars or len(historical_bars) < warmup_bars:
                console.print(f"[red]✗[/red] Failed to fetch enough bars: "
                             f"got {len(historical_bars) if historical_bars else 0}")
                return False

            console.print(f"[green]✓[/green] Fetched {len(historical_bars)} bars")

            # Feed bars to detector
            console.print("Warming up detector...")
            for i, bar in enumerate(historical_bars):
                detector.update_from_bar(bar)

                # Check warmup progress at key points
                if i + 1 in [20, 30, 40, 50, 60, 70]:
                    is_warm = detector.is_warm
                    regime = detector.current()
                    console.print(f"  Bar {i+1}: warm={is_warm}, regime={regime}")

            # Check final state
            final_warm = detector.is_warm
            final_regime = detector.current()

            console.print(f"Final state: warm={final_warm}, regime={final_regime}")

            if final_warm:
                console.print("[green]✓[/green] Detector successfully warmed up")

                # Show indicator values
                if detector.ema_fast.value and detector.ema_slow.value:
                    console.print(f"  EMA fast: {detector.ema_fast.value:.2f}")
                    console.print(f"  EMA slow: {detector.ema_slow.value:.2f}")
                if detector.adx.value:
                    console.print(f"  ADX: {detector.adx.value:.2f}")
                if detector.atr.value:
                    console.print(f"  ATR: {detector.atr.value:.2f}")
            else:
                console.print("[red]✗[/red] Detector still not warm after feeding all bars")
                return False

    except Exception as e:
        console.print(f"[red]✗[/red] Warmup failed: {e}")
        import traceback
        console.print(traceback.format_exc())
        return False

    console.print()

    # Test 3: Test error handling
    console.print("[bold]Test 3: Error Handling[/bold]")

    try:
        with BinanceDataWarmer(venue_cfg, console) as warmer:
            # Try to fetch invalid symbol
            console.print("Testing invalid symbol...")
            bars = warmer.fetch_detector_bars(
                symbol="INVALID",
                num_bars=5,
                interval="1m",
            )

            if bars:
                console.print("[yellow]⚠ Unexpected: got bars for invalid symbol[/yellow]")
            else:
                console.print("[green]✓[/green] Correctly handled invalid symbol")

    except Exception as e:
        console.print(f"[green]✓[/green] Correctly raised exception: {e}")

    console.print()
    console.print("[bold green]✅ All tests passed![/bold green]")
    return True


if __name__ == "__main__":
    # Check for required environment variables
    console = Console()

    if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
        console.print("[yellow]⚠ Warning: BINANCE_API_KEY and BINANCE_API_SECRET not set[/yellow]")
        console.print("[yellow]  The test may fail if the venue config requires API keys[/yellow]")
        console.print()

    success = test_warmup_system()
    sys.exit(0 if success else 1)