#!/usr/bin/env python
"""Debug backtest to understand why strategy isn't trading."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import yaml
from nautilus_trader.model.enums import OmsType

from naut_hedgegrid.config.backtest import BacktestConfigLoader
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.runners.run_backtest import BacktestRunner
from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1Config


def main():
    """Run a single backtest with DEBUG logging to see what's happening."""

    # Use parameters from trial 13 (first one in CSV)
    trial_params = {
        "grid": {
            "grid_step_bps": 80.0,
            "grid_levels_long": 10,
            "grid_levels_short": 8,
            "base_qty": 0.002293128129547974,
            "qty_scale": 1.0,
        },
        "exit": {
            "tp_steps": 2,
            "sl_steps": 4,
        },
        "regime": {
            "adx_len": 27,
            "ema_fast": 17,
            "ema_slow": 48,
            "atr_len": 7,
            "hysteresis_bps": 50.0,
        },
        "policy": {
            "strategy": "throttled-counter",
            "counter_levels": 9,
            "counter_qty_scale": 0.4,
        },
        "rebalance": {
            "recenter_trigger_bps": 125.0,
        },
        "funding": {
            "funding_window_minutes": 480,
            "funding_max_cost_bps": 10.0,
        },
        "position": {
            "max_position_pct": 0.65,  # Already as decimal
        },
    }

    print("=" * 80)
    print("DEBUG BACKTEST - Understanding Why Strategy Doesn't Trade")
    print("=" * 80)
    print("\nUsing parameters from Trial 13 (from CSV)")
    print(f"Grid step: {trial_params['grid']['grid_step_bps']} bps")
    print(f"Levels: {trial_params['grid']['grid_levels_long']} long, {trial_params['grid']['grid_levels_short']} short")
    print(f"Base qty: {trial_params['grid']['base_qty']}")
    print("\n")

    # Load base configs
    backtest_config_path = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    base_strategy_config_path = Path("configs/strategies/final_working_test_best.yaml")

    # Load and modify backtest config to use DEBUG logging
    backtest_config = BacktestConfigLoader.load(backtest_config_path)
    backtest_config.output.log_level = "INFO"  # Use INFO to see strategy actions

    # Create modified strategy config
    with open(base_strategy_config_path) as f:
        strategy_yaml = yaml.safe_load(f)

    # Merge trial parameters
    for section, params in trial_params.items():
        if section in strategy_yaml:
            strategy_yaml[section].update(params)
        else:
            strategy_yaml[section] = params

    # Save temporary config
    temp_config = Path("debug_strategy_config.yaml")
    with open(temp_config, "w") as f:
        yaml.dump(strategy_yaml, f)

    # Load the modified config
    hedge_grid_cfg = HedgeGridConfigLoader.load(temp_config)

    # Create Nautilus strategy config
    strategy_config = HedgeGridV1Config(
        instrument_id=hedge_grid_cfg.strategy.instrument_id,
        hedge_grid_config_path=str(temp_config),
        oms_type=OmsType.HEDGING,
    )

    # Run backtest
    print("Running backtest with INFO logging...")
    print("Watch for:")
    print("  - 'Strategy started' message")
    print("  - 'Regime detected' messages")
    print("  - 'Placing orders' messages")
    print("  - Any errors or warnings")
    print("\n" + "=" * 80 + "\n")

    runner = BacktestRunner(backtest_config=backtest_config, strategy_configs=[strategy_config])

    catalog = runner.setup_catalog()
    engine, data = runner.run(catalog)

    # Cleanup
    temp_config.unlink(missing_ok=True)

    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)

    if engine:
        portfolio = engine.portfolio

        # Check positions
        positions_closed = engine.cache.positions_closed()
        positions_open = engine.cache.positions_open()
        orders = engine.cache.orders()

        print(f"\nPositions closed: {len(positions_closed)}")
        print(f"Positions open: {len(positions_open)}")
        print(f"Total orders: {len(orders)}")

        if len(orders) > 0:
            print("\nFirst 5 orders:")
            for i, order in enumerate(list(orders)[:5]):
                print(f"  {i + 1}. {order}")
        else:
            print("\n⚠️  NO ORDERS WERE PLACED!")
            print("\nPossible reasons:")
            print("  1. Strategy didn't start (check for 'Strategy started' message)")
            print("  2. Regime detector not ready (needs warmup bars)")
            print("  3. No bars received (data loading issue)")
            print("  4. Grid parameters preventing order placement")
            print("  5. Position limits preventing trading")

        # Check account
        try:
            account = engine.cache.account(backtest_config.venues[0].venue)
            if account:
                print(f"\nAccount balance: {account.balance_total()}")
        except:
            pass

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
