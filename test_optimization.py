#!/usr/bin/env python
"""Quick test optimization with 3 trials to verify fixes work."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from naut_hedgegrid.optimization import StrategyOptimizer
from naut_hedgegrid.optimization.constraints import ConstraintThresholds

def main():
    """Run quick test optimization."""
    
    # Configuration paths
    backtest_config = Path("configs/backtest/btcusdt_mark_trades_funding.yaml")
    strategy_config = Path("configs/strategies/hedge_grid_v1.yaml")
    
    # Relaxed constraints for testing
    constraints = ConstraintThresholds(
        min_sharpe_ratio=0.0,       # Accept any Sharpe for testing
        max_drawdown_pct=100.0,     # Accept any drawdown for testing
        min_trades=1,               # Just need 1 trade
        min_win_rate_pct=0.0,       # Accept any win rate
        min_profit_factor=0.0,      # Accept any profit factor
        min_calmar_ratio=-999.0     # Accept negative Calmar
    )
    
    # Initialize optimizer with just 3 trials
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=3,  # Just 3 trials for quick test
        n_jobs=1,
        study_name="test_optimization",
        constraint_thresholds=constraints,
        verbose=True
    )
    
    print("=" * 80)
    print("TEST OPTIMIZATION - 3 TRIALS")
    print("=" * 80)
    print("Testing if trades occur with position check fix...")
    print()
    
    study = optimizer.optimize()
    
    print(f"\n✓ Optimization complete!")
    print(f"Best score: {study.best_value:.4f}")
    
    # Check if we got trades
    valid_trials = [t for t in study.trials if t.values is not None and t.values[0] > float("-inf")]
    if valid_trials:
        print(f"✓ SUCCESS: {len(valid_trials)} trials had trades!")
    else:
        print(f"✗ FAILURE: No trials had trades")
    
    # Export results
    optimizer.export_results(Path("test_optimization_results.csv"))
    print("✓ Results saved to: test_optimization_results.csv")

if __name__ == "__main__":
    main()
