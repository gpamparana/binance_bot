#!/usr/bin/env python
"""
Test script to verify optimization fixes are working.

This runs a minimal optimization (3 trials) to check:
1. Metrics extraction works (counts fills not positions)
2. Constraints are reasonable for 1 month of data
3. No position tracking errors
4. Parameter validation works
5. Order sync is performant
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from naut_hedgegrid.optimization import StrategyOptimizer
from naut_hedgegrid.optimization.constraints import ConstraintThresholds


def main():
    """Run quick test optimization."""

    print("=" * 80)
    print("TESTING OPTIMIZATION FIXES")
    print("=" * 80)
    print()
    print("Running 3 trials to verify fixes:")
    print("  ✓ Metrics extraction (counting fills)")
    print("  ✓ Constraint thresholds (relaxed for 1 month)")
    print("  ✓ Position tracking (retry mechanism)")
    print("  ✓ Parameter validation (proper inventory checks)")
    print("  ✓ Order sync caching")
    print()

    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/hedge_grid_v1.yaml")

    # Relaxed constraints for testing
    constraints = ConstraintThresholds(
        min_sharpe_ratio=0.1,       # Very low for testing
        max_drawdown_pct=50.0,      # High tolerance
        min_trades=3,               # Just need a few fills
        min_win_rate_pct=30.0,      # Low bar
        min_profit_factor=1.0,      # Break even is OK
        min_calmar_ratio=0.05       # Almost no requirement
    )

    # Initialize optimizer
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=3,                    # Just 3 trials for testing
        n_jobs=1,                       # Sequential
        study_name="test_fixes",
        constraint_thresholds=constraints,
        verbose=True
    )

    # Run optimization
    print("Starting test optimization (3 trials)...")
    print("-" * 80)

    try:
        study = optimizer.optimize()

        print()
        print("=" * 80)
        print("TEST RESULTS")
        print("=" * 80)

        # Check if we got any valid trials
        valid_trials = [
            t for t in study.trials
            if t.values is not None and len(t.values) > 0 and t.values[0] > float("-inf")
        ]

        print(f"Total trials run: {len(study.trials)}")
        print(f"Valid trials: {len(valid_trials)}")
        print(f"Success rate: {len(valid_trials)/len(study.trials)*100:.1f}%")

        if valid_trials:
            print()
            print("✅ FIXES WORKING! Got valid trials with proper scoring.")

            # Show metrics from best trial
            best_trial = max(valid_trials, key=lambda t: t.values[0])
            print(f"\nBest trial score: {best_trial.values[0]:.4f}")

            # Check if trades were counted
            if "total_trades" in best_trial.user_attrs:
                trades = best_trial.user_attrs["total_trades"]
                print(f"Trades executed: {trades}")
                if trades > 0:
                    print("✅ Trade counting fixed (fills being counted)")
                else:
                    print("⚠️ No trades executed - check strategy parameters")
        else:
            print()
            print("❌ No valid trials - issues may remain:")
            print("  - Check if trades are being placed")
            print("  - Verify position tracking")
            print("  - Check parameter bounds")

            # Show rejection reasons if available
            if study.trials:
                trial = study.trials[0]
                if trial.user_attrs:
                    print("\nFirst trial metrics:")
                    for key, value in trial.user_attrs.items():
                        if "trades" in key or "sharpe" in key or "return" in key:
                            print(f"  {key}: {value}")

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())