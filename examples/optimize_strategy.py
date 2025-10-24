"""Example: Optimize HedgeGridV1 strategy parameters.

This script demonstrates how to use the optimization framework to find
optimal parameters for the HedgeGridV1 strategy through Bayesian optimization.
"""

from pathlib import Path

from naut_hedgegrid.optimization import (
    ConstraintThresholds,
    MultiObjectiveFunction,
    ObjectiveWeights,
    ParameterSpace,
    StrategyOptimizer,
)


def main():
    """Run parameter optimization with custom settings."""

    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/hedge_grid_v1.yaml")

    # Custom objective weights
    # Prioritize risk-adjusted returns over raw profit
    weights = ObjectiveWeights(
        sharpe_ratio=0.40,  # Emphasize risk-adjusted returns
        profit_factor=0.20,
        calmar_ratio=0.30,  # Emphasize drawdown resilience
        drawdown_penalty=-0.10  # Less penalty on drawdown
    )

    # Custom constraints
    # Stricter requirements for production deployment
    constraints = ConstraintThresholds(
        min_sharpe_ratio=1.5,  # Higher than default
        max_drawdown_pct=15.0,  # Stricter than default
        min_trades=100,  # More trades for statistical significance
        min_win_rate_pct=48.0,
        min_profit_factor=1.2,
        min_calmar_ratio=0.8
    )

    # Initialize optimizer
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=200,  # More trials for thorough search
        n_jobs=1,  # Sequential execution
        study_name="btcusdt_production_v1",
        db_path=Path("optimization_results.db"),
        objective_weights=weights,
        constraint_thresholds=constraints,
        storage="sqlite:///optuna_studies.db",  # Persistent Optuna storage
        verbose=True
    )

    # Run optimization
    print("\nStarting parameter optimization...")
    print(f"Target: Find optimal parameters for BTCUSDT futures")
    print(f"Trials: {optimizer.n_trials}")
    print(f"Objective: {weights.sharpe_ratio:.0%} Sharpe + {weights.profit_factor:.0%} Profit + {weights.calmar_ratio:.0%} Calmar - {abs(weights.drawdown_penalty):.0%} DD")
    print(f"Constraints: Sharpe>={constraints.min_sharpe_ratio}, DD<={constraints.max_drawdown_pct}%, Trades>={constraints.min_trades}")
    print()

    study = optimizer.optimize()

    # Display results
    print("\n" + "="*60)
    print("OPTIMIZATION COMPLETE")
    print("="*60)

    print(f"\nBest Trial: {study.best_trial.number}")
    print(f"Best Score: {study.best_value:.4f}")

    print("\nBest Parameters:")
    for param, value in study.best_trial.params.items():
        print(f"  {param}: {value}")

    # Export results
    results_csv = Path("optimization_results_btcusdt_v1.csv")
    optimizer.export_results(results_csv)
    print(f"\nResults exported to: {results_csv}")

    # Show optimized config path
    optimized_config = Path(f"configs/strategies/{optimizer.study_name}_best.yaml")
    print(f"Optimized config saved to: {optimized_config}")

    print("\n" + "="*60)
    print("Next Steps:")
    print("="*60)
    print("1. Review optimized parameters in YAML config")
    print("2. Validate on out-of-sample data:")
    print(f"   uv run python -m naut_hedgegrid backtest \\")
    print(f"     --strategy-config {optimized_config}")
    print("3. Test in paper trading before live deployment")
    print()


if __name__ == "__main__":
    main()