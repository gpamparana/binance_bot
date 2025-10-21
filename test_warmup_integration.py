#!/usr/bin/env python
"""Test regime detector warmup functionality."""

import os
import sys
from datetime import UTC, datetime

# Set environment for testing
os.environ["BINANCE_API_KEY"] = os.environ.get("BINANCE_API_KEY", "")
os.environ["BINANCE_API_SECRET"] = os.environ.get("BINANCE_API_SECRET", "")

from naut_hedgegrid.config.venue import VenueConfig, VenueConfigLoader
from naut_hedgegrid.strategy.detector import RegimeDetector
from naut_hedgegrid.warmup import BinanceDataWarmer


def test_warmup():
    """Test the warmup functionality."""
    print("Testing regime detector warmup...")

    # Load venue config
    venue_config = VenueConfigLoader.load("configs/venues/binance_testnet.yaml")

    # Create regime detector
    detector = RegimeDetector(
        ema_fast=12,
        ema_slow=26,
        adx_len=14,
        atr_len=14,
        hysteresis_bps=50.0,
    )

    print(f"Initial state - Regime: {detector.current()}, Warm: {detector.is_warm}")

    # Fetch historical data
    with BinanceDataWarmer(venue_config) as warmer:
        print("Fetching historical bars from Binance...")
        historical_bars = warmer.fetch_detector_bars(
            symbol="BTCUSDT",
            num_bars=50,
            interval="1m",
        )

        if historical_bars:
            print(f"Fetched {len(historical_bars)} bars")

            # Warm up detector
            print("Warming up detector...")
            for i, bar in enumerate(historical_bars):
                detector.update_from_bar(bar)

                if (i + 1) % 10 == 0:
                    print(
                        f"  Progress: {i+1}/{len(historical_bars)} - "
                        f"Regime: {detector.current()}, Warm: {detector.is_warm}"
                    )

            print(f"\nFinal state - Regime: {detector.current()}, Warm: {detector.is_warm}")

            if detector.is_warm:
                print("✓ Warmup successful!")
                print(f"  EMA Fast: {detector.ema_fast.value:.2f}")
                print(f"  EMA Slow: {detector.ema_slow.value:.2f}")
                print(f"  ADX: {detector.adx.value:.2f}")
                print(f"  ATR: {detector.atr.value:.2f}")
            else:
                print("✗ Detector not warm after warmup")

        else:
            print("✗ Failed to fetch historical bars")


if __name__ == "__main__":
    test_warmup()