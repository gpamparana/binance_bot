# HedgeGridV1 Parameter Optimization Framework

Production-grade Bayesian optimization system for finding optimal HedgeGridV1 strategy parameters using Optuna.

## Overview

This framework provides comprehensive parameter optimization through:

- **Bayesian Optimization**: Optuna with TPE sampler for intelligent parameter search
- **Multi-Objective Scoring**: Weighted combination of Sharpe ratio, profit factor, Calmar ratio, and drawdown penalty
- **Hard Constraints**: Validation of minimum performance requirements
- **Parallel Execution**: Multi-process backtest execution for efficiency
- **Result Persistence**: SQLite database for tracking trials and experiment history
- **Adaptive Normalization**: Dynamic metric bounds for fair comparison across trials

## Architecture

### Components

1. **ParameterSpace** (`param_space.py`): Defines search space for 17 tunable parameters
2. **MultiObjectiveFunction** (`objective.py`): Combines metrics into optimization score
3. **ConstraintsValidator** (`constraints.py`): Enforces minimum performance thresholds
4. **OptimizationResultsDB** (`results_db.py`): Persists trial results to SQLite
5. **ParallelBacktestRunner** (`parallel_runner.py`): Concurrent backtest execution
6. **StrategyOptimizer** (`optimizer.py`): Main orchestrator integrating all components

### Parameter Search Space

17 parameters across 7 categories:

**Grid Parameters (5)**
- `grid_step_bps`: [10, 200] - Grid spacing in basis points
- `grid_levels_long`: [3, 20] - Number of buy levels
- `grid_levels_short`: [3, 20] - Number of sell levels
- `base_qty`: [0.001, 0.1] (log scale) - Base order quantity
- `qty_scale`: [1.0, 1.5] - Geometric quantity multiplier

**Exit Parameters (2)**
- `tp_steps`: [1, 10] - Take profit distance in grid steps
- `sl_steps`: [3, 20] - Stop loss distance in grid steps

**Regime Detection (5)**
- `adx_len`: [7, 30] - ADX period for trend strength
- `ema_fast`: [5, 25] - Fast EMA period
- `ema_slow`: [20, 60] - Slow EMA period
- `atr_len`: [7, 30] - ATR period for volatility
- `hysteresis_bps`: [5, 50] - Regime switching hysteresis

**Policy Parameters (2)**
- `counter_levels`: [2, 10] - Levels on counter-trend side
- `counter_qty_scale`: [0.3, 0.8] - Quantity reduction for counter-trend

**Rebalance (1)**
- `recenter_trigger_bps`: [50, 500] - Grid recentering threshold

**Funding (1)**
- `funding_max_cost_bps`: [5, 50] - Maximum acceptable funding cost

**Position (1)**
- `max_position_pct`: [50, 95] - Maximum position as % of balance

### Multi-Objective Function

Combines four metrics with configurable weights:

- **Sharpe Ratio** (30%): Risk-adjusted returns
- **Profit Factor** (25%): Win/loss ratio
- **Calmar Ratio** (25%): Return relative to max drawdown
- **Drawdown Penalty** (-20%): Penalizes large drawdowns

Score calculation:
```python
score = (
    0.30 * normalize(sharpe_ratio) +
    0.25 * normalize(profit_factor) +
    0.25 * normalize(calmar_ratio) -
    0.20 * normalize(max_drawdown_pct)
)
```

Metrics are min-max normalized with adaptive bounds updated every 20 trials.

### Hard Constraints

All constraints must be met for valid trial (configurable):

- **Sharpe Ratio** >= 1.0
- **Max Drawdown** <= 20%
- **Total Trades** >= 50
- **Win Rate** >= 45%
- **Profit Factor** >= 1.1
- **Calmar Ratio** >= 0.5

## Usage

### Basic Example

```python
from pathlib import Path
from naut_hedgegrid.optimization import StrategyOptimizer

# Initialize optimizer
optimizer = StrategyOptimizer(
    backtest_config_path=Path("configs/backtest/btcusdt_mark_trades_funding.yaml"),
    base_strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    n_trials=100,
    n_jobs=1,  # Sequential (parallel backtests handled internally)
    study_name="btcusdt_optimization_v1"
)

# Run optimization
study = optimizer.optimize()

# Export results
optimizer.export_results(Path("optimization_results.csv"))
```

### Custom Configuration

```python
from naut_hedgegrid.optimization import (
    StrategyOptimizer,
    ObjectiveWeights,
    ConstraintThresholds,
    ParameterSpace,
    ParameterBounds
)

# Custom objective weights
weights = ObjectiveWeights(
    sharpe_ratio=0.40,
    profit_factor=0.30,
    calmar_ratio=0.20,
    drawdown_penalty=-0.10
)

# Custom constraints
constraints = ConstraintThresholds(
    min_sharpe_ratio=1.5,
    max_drawdown_pct=15.0,
    min_trades=100,
    min_win_rate_pct=50.0
)

# Custom parameter bounds
custom_bounds = {
    "GRID_STEP_BPS": ParameterBounds(min_value=20, max_value=100, step=10),
    "BASE_QTY": ParameterBounds(min_value=0.005, max_value=0.05, log_scale=True)
}
param_space = ParameterSpace(custom_bounds=custom_bounds)

# Initialize with custom settings
optimizer = StrategyOptimizer(
    backtest_config_path=Path("configs/backtest/btcusdt.yaml"),
    base_strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    n_trials=200,
    n_jobs=1,
    param_space=param_space,
    objective_weights=weights,
    constraint_thresholds=constraints,
    storage="sqlite:///optuna_studies.db"  # Persistent Optuna storage
)

study = optimizer.optimize()
```

### Accessing Results

```python
from naut_hedgegrid.optimization import OptimizationResultsDB

# Connect to results database
db = OptimizationResultsDB(Path("optimization_results.db"))

# Get best trials
best_trials = db.get_best_trials("btcusdt_optimization_v1", n=10)

for trial in best_trials:
    print(f"Trial {trial['id']}: Score={trial['score']:.4f}")
    print(f"  Parameters: {trial['parameters']}")
    print(f"  Metrics: {trial['metrics']}")

# Get best parameters
best_params = db.get_best_parameters("btcusdt_optimization_v1")
print(f"Best parameters: {best_params}")

# Get study statistics
stats = db.get_study_stats("btcusdt_optimization_v1")
print(f"Total trials: {stats['total_trials']}")
print(f"Valid trials: {stats['valid_trials']}")
print(f"Validity rate: {stats['validity_rate']:.1%}")
print(f"Best score: {stats['best_score']:.4f}")

# Export to CSV
db.export_to_csv("btcusdt_optimization_v1", Path("results.csv"))
```

## Workflow

1. **Initialize**: Load base backtest and strategy configurations
2. **Create Study**: Optuna study with TPE sampler and median pruner
3. **Optimize Loop**:
   - Sample parameters from search space
   - Validate parameter constraints
   - Run backtest with parameters
   - Calculate performance metrics
   - Validate hard constraints
   - Calculate multi-objective score
   - Save trial to database
   - Update Optuna study
4. **Save Results**: Export best parameters to YAML config file
5. **Analysis**: Query database for best trials and statistics

## Performance Considerations

### Sequential vs Parallel

The optimizer uses **sequential trial execution** (`n_jobs=1`) because:
- Each backtest is already optimized for speed
- NautilusTrader backtests are CPU-intensive
- Memory usage can be high for multiple concurrent backtests
- Database locking reduces parallel efficiency

For faster optimization:
1. Use shorter backtest periods during initial exploration
2. Increase `n_startup_trials` in TPE sampler for better early exploration
3. Use median pruner to terminate unpromising trials early
4. Run multiple optimization studies in parallel with different parameter ranges

### Database Performance

- Results database uses SQLite with thread-safe connections
- Denormalizes key metrics for fast queries
- Indices on study_name, score, and timestamp
- Cleanup old trials with `cleanup_old_trials()` to manage database size

## Integration with Existing System

### Configuration Files

Optimizer uses existing config loaders:
- `BacktestConfigLoader` for backtest setup
- `HedgeGridConfigLoader` for strategy parameters

Generated configs are compatible with:
- `BacktestRunner` for backtesting
- `PaperRunner` for paper trading
- `LiveRunner` for live trading

### Metrics Calculation

Uses existing `ReportGenerator` from `naut_hedgegrid.metrics.report`:
- 32 comprehensive metrics across 7 categories
- Consistent with manual backtest analysis
- Full compatibility with artifact exports

## Best Practices

### 1. Start with Wide Search Space
Use default parameter bounds for initial exploration, then narrow based on results.

### 2. Use Sufficient Trials
- Minimum 100 trials for meaningful results
- 200-500 trials for thorough optimization
- 1000+ trials for production deployment

### 3. Validate on Out-of-Sample Data
After optimization:
1. Backtest best parameters on different time period
2. Test on multiple instruments
3. Verify stability across market regimes

### 4. Monitor Constraint Violations
High violation rate (>50%) indicates:
- Constraints too strict - consider relaxing
- Search space misaligned with market conditions
- Base strategy design issues

### 5. Check Overfitting
- Compare in-sample vs out-of-sample performance
- Look for parameter sensitivity (small changes → large performance swings)
- Prefer simpler parameter sets when scores are similar

### 6. Use Optuna Features
```python
# Optuna visualization
import optuna.visualization as vis

vis.plot_optimization_history(study)
vis.plot_param_importances(study)
vis.plot_parallel_coordinate(study)
```

## Troubleshooting

### Issue: All trials fail validation
**Solution**: Check constraint thresholds - they may be too strict for your market/timeframe.

### Issue: Scores don't improve over time
**Solution**:
- Increase `n_startup_trials` for better initial exploration
- Check if search space includes viable parameter ranges
- Verify objective weights align with your goals

### Issue: Database corruption
**Solution**:
- Backup database regularly during long runs
- Use `storage` parameter for Optuna study persistence
- Enable WAL mode: `PRAGMA journal_mode=WAL`

### Issue: High memory usage
**Solution**:
- Reduce backtest period
- Clear Nautilus engine cache between trials
- Run fewer concurrent backtests

## Testing

Run optimization framework tests:

```bash
# All optimization tests
uv run pytest tests/optimization/ -v

# Specific test modules
uv run pytest tests/optimization/test_param_space.py -v
uv run pytest tests/optimization/test_objective.py -v
uv run pytest tests/optimization/test_constraints.py -v
uv run pytest tests/optimization/test_results_db.py -v
```

## Future Enhancements

- [ ] Multi-instrument optimization
- [ ] Walk-forward optimization with rolling windows
- [ ] Ensemble parameter sets for robustness
- [ ] Real-time performance monitoring dashboard
- [ ] Automated regime-specific parameter switching
- [ ] Integration with cloud computing for massive parallelization