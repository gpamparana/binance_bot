#!/usr/bin/env python
"""Run optimization for HedgeGridV1 strategy."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from naut_hedgegrid.optimization import (
    StrategyOptimizer,
)
from naut_hedgegrid.optimization.constraints import ConstraintThresholds
from naut_hedgegrid.optimization.objective import ObjectiveWeights


def main():
    """Run parameter optimization."""

    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/final_working_test_best.yaml")

    # Relax constraints for testing since we have limited data
    constraints = ConstraintThresholds(
        min_sharpe_ratio=0.5,  # Relaxed for testing
        max_drawdown_pct=30.0,  # Relaxed for testing
        min_trades=5,  # Very relaxed - just need some trades
        min_win_rate_pct=30.0,  # Relaxed for testing
        min_profit_factor=0.8,  # Relaxed for testing
        min_calmar_ratio=0.1,  # Relaxed for testing
    )

    # Initialize optimizer
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=2,  # Start with 2 trials for testing
        n_jobs=1,  # Sequential execution
        study_name="hedge_grid_optimization",
        objective_weights=ObjectiveWeights(),  # Use defaults
        constraint_thresholds=constraints,
        verbose=True,
    )

    # Run optimization
    print("Starting optimization with 2 trials...")
    study = optimizer.optimize()

    print("\nOptimization complete!")
    print(f"Best score: {study.best_value:.4f}")
    print(f"Best trial: {study.best_trial.number}")

    # Show best parameters
    print("\nBest parameters:")
    for key, value in study.best_trial.params.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
