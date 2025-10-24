#!/usr/bin/env python
"""Run overnight optimization for HedgeGridV1 strategy."""

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
    """Run parameter optimization with 200 trials."""

    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/final_working_test_best.yaml")

    # Very relaxed constraints for initial optimization
    # This allows us to see which parameter sets produce ANY trades
    # After we have some good results, we can tighten these
    constraints = ConstraintThresholds(
        min_sharpe_ratio=0.0,    # Accept any sharpe (even negative)
        max_drawdown_pct=100.0,  # Accept any drawdown
        min_trades=1,            # Just need at least 1 trade
        min_win_rate_pct=0.0,    # Accept any win rate
        min_profit_factor=0.0,   # Accept any profit factor
        min_calmar_ratio=0.0     # Accept any calmar
    )

    # Initialize optimizer with more trials
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=200,  # Full 200 trials for overnight run
        n_jobs=4,       # Sequential execution (safer for overnight)
        study_name="hedge_grid_optimization_overnight",
        objective_weights=ObjectiveWeights(),  # Use defaults
        constraint_thresholds=constraints,
        verbose=True
    )

    # Run optimization
    print("=" * 80)
    print("Starting overnight optimization with 200 trials...")
    print("This will take several hours to complete.")
    print("Results will be saved to: configs/strategies/hedge_grid_optimization_overnight_best.yaml")
    print("=" * 80)
    print()

    study = optimizer.optimize()

    print(f"\nOptimization complete!")
    print(f"Best score: {study.best_value:.4f}")
    print(f"Best trial: {study.best_trial.number}")

    # Show best parameters
    print("\nBest parameters:")
    for key, value in study.best_trial.params.items():
        print(f"  {key}: {value}")

    # Export results
    print("\nExporting results...")
    optimizer.export_results(Path("artifacts/optimization_results.csv"))
    print("Results saved to: artifacts/optimization_results.csv")


if __name__ == "__main__":
    main()