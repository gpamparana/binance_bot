#!/usr/bin/env python
"""Run overnight optimization for HedgeGridV1 strategy."""

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
    """Run parameter optimization with 200 trials."""

    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/hedge_grid_v1.yaml")

    # Very relaxed constraints for initial optimization
    # This allows us to see which parameter sets produce ANY trades
    # After we have some good results, we can tighten these
    constraints = ConstraintThresholds(
        min_sharpe_ratio=0.0,  # Accept any sharpe (even negative)
        max_drawdown_pct=100.0,  # Accept any drawdown
        min_trades=1,  # Just need at least 1 trade
        min_win_rate_pct=0.0,  # Accept any win rate
        min_profit_factor=0.0,  # Accept any profit factor
        min_calmar_ratio=0.0,  # Accept any calmar
    )

    # Initialize optimizer with more trials
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=200,  # Full 200 trials for overnight run
        n_jobs=4,  # Sequential execution (safer for overnight)
        study_name="hedge_grid_optimization_overnight",
        objective_weights=ObjectiveWeights(),  # Use defaults
        constraint_thresholds=constraints,
        verbose=True,
    )

    # Run optimization
    print("=" * 80)
    print("Starting overnight optimization with 200 trials...")
    print("This will take several hours to complete.")
    print("Results will be saved to: configs/strategies/hedge_grid_optimization_overnight_best.yaml")
    print("=" * 80)
    print()

    study = optimizer.optimize()

    print("\nOptimization complete!")
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

    # Show summary statistics - use optimizer's actual validation counts
    print("\nOptimization Summary:")
    # Trials that passed constraint validation (not just didn't crash)
    valid_trials = [t for t in study.trials if t.user_attrs.get("is_valid", False)]
    # Trials that completed (have a score, even if invalid)
    completed_trials = [
        t for t in study.trials if t.values is not None and len(t.values) > 0 and t.values[0] > float("-inf")
    ]

    print(f"  Total trials: {len(study.trials)}")
    print(f"  Completed trials: {len(completed_trials)}")
    print(f"  Valid trials (passed constraints): {len(valid_trials)}")
    print(f"  Validity rate: {len(valid_trials) / len(study.trials) * 100:.1f}%" if study.trials else "N/A")

    if completed_trials:
        scores = [t.values[0] for t in completed_trials]
        print(f"  Best score: {max(scores):.4f}")
        if valid_trials:
            valid_scores = [t.values[0] for t in valid_trials]
            print(f"  Best valid score: {max(valid_scores):.4f}")
            print(f"  Avg valid score: {sum(valid_scores) / len(valid_scores):.4f}")
        else:
            print("  No trials passed constraint validation")


if __name__ == "__main__":
    main()
