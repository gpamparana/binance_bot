#!/usr/bin/env python
"""Quick test to verify optimizer has clean output."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from naut_hedgegrid.optimization import (
    ConstraintsValidator,
    MultiObjectiveFunction,
    ParameterSpace,
    StrategyOptimizer,
)
from naut_hedgegrid.optimization.constraints import ConstraintThresholds
from naut_hedgegrid.optimization.objective import ObjectiveWeights


def main():
    """Run a quick 5-trial optimization to test output."""

    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/final_working_test_best.yaml")

    # Very relaxed constraints for quick test
    constraints = ConstraintThresholds(
        min_sharpe_ratio=0.0,
        max_drawdown_pct=50.0,
        min_trades=1,
        min_win_rate_pct=0.0,
        min_profit_factor=0.0,
        min_calmar_ratio=0.0
    )

    # Initialize optimizer with only 5 trials for quick test
    print("Initializing optimizer for quick 5-trial test...")
    print("This should show ONLY:")
    print("  - Progress bar")
    print("  - New best scores")
    print("  - Errors (if any)")
    print("\nIf you see lots of Nautilus logs, the fix didn't work.\n")

    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=5,  # Only 5 trials for quick test
        n_jobs=1,
        study_name="quick_output_test",
        objective_weights=ObjectiveWeights(),
        constraint_thresholds=constraints,
        verbose=True
    )

    # Run optimization
    study = optimizer.optimize()

    print(f"\n✓ Test complete!")
    print(f"If you saw clean output with progress bar, the fix worked!")
    print(f"Best score: {study.best_value:.4f}")


if __name__ == "__main__":
    main()
