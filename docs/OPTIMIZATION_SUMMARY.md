# HedgeGridV1 Parameter Optimization Framework - Implementation Summary

## Overview

A complete production-grade Bayesian optimization system has been implemented for finding optimal HedgeGridV1 strategy parameters. The framework uses Optuna for intelligent parameter search and provides comprehensive result tracking, validation, and analysis capabilities.

## Components Implemented

### 1. Core Modules (`naut_hedgegrid/optimization/`)

#### `param_space.py` - Parameter Search Space
- **17 tunable parameters** across 7 configuration categories
- Grid (5): step size, levels, base quantity, quantity scaling
- Exit (2): take profit and stop loss distances
- Regime (5): ADX, EMA, ATR periods and hysteresis
- Policy (2): counter-trend configuration
- Rebalance (1): grid recentering threshold
- Funding (1): maximum acceptable funding cost
- Position (1): maximum position percentage
- Support for log-scale distributions (exponential ranges like base_qty)
- Automatic constraint enforcement (EMA fast < slow, TP < 3x SL)
- Parameter validation before backtest execution

#### `objective.py` - Multi-Objective Scoring
- **Weighted combination** of 4 key metrics:
  - Sharpe ratio (30%): Risk-adjusted returns
  - Profit factor (25%): Win/loss ratio
  - Calmar ratio (25%): Return relative to drawdown
  - Drawdown penalty (-20%): Penalizes large drawdowns
- **Adaptive normalization**: Bounds updated every 20 trials using percentiles
- **Component score breakdown**: Individual metric scores for debugging
- Handles missing/invalid metrics gracefully (returns -inf)
- Configurable weights for custom optimization goals

#### `constraints.py` - Hard Constraints Validation
- **6 minimum performance requirements**:
  - Sharpe ratio >= 1.0
  - Max drawdown <= 20%
  - Total trades >= 50
  - Win rate >= 45%
  - Profit factor >= 1.1
  - Calmar ratio >= 0.5
- Strict and lenient modes (allow 0 or 1 violation)
- Continuous violation scoring for gradient-based methods
- Dynamic threshold updates during optimization
- Detailed violation messages for debugging

#### `results_db.py` - SQLite Results Database
- **Thread-safe** storage with connection pooling
- Three tables: trials, studies, best_params
- **Denormalized metrics** for fast queries (Sharpe, profit factor, etc.)
- Indices on study_name, score, timestamp
- Export to CSV with JSON parameter expansion
- Study statistics: validity rate, avg/best scores, trial count
- Cleanup function to manage database size
- Auto-incrementing trial IDs with conflict resolution

#### `parallel_runner.py` - Multi-Process Execution
- **ProcessPoolExecutor** for concurrent backtests
- Configurable worker count (default: CPU count - 1)
- **Rich progress bars** with live updates
- Retry logic: up to 3 attempts per failed backtest
- 10-minute timeout per backtest
- Proper resource cleanup and error handling
- Module-level function for multiprocessing pickle compatibility

#### `optimizer.py` - Main Orchestrator
- **Integrates all components** into cohesive workflow
- Optuna study with TPE sampler and median pruner
- Sequential trial execution (n_jobs=1)
- Automatic temp config generation for each trial
- **Live progress display** with Rich console
- Saves best parameters to `configs/strategies/{study_name}_best.yaml`
- Checkpoint support via Optuna storage parameter
- Comprehensive error handling and logging

### 2. Command-Line Interface (`optimization/cli.py`)

#### `optimize` Command
Run parameter optimization:
```bash
uv run python -m naut_hedgegrid.optimization.cli optimize \
  --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
  --strategy-config configs/strategies/hedge_grid_v1.yaml \
  --trials 200 \
  --study-name btcusdt_opt_v1 \
  --export-csv results.csv
```

#### `analyze` Command
Analyze optimization results:
```bash
uv run python -m naut_hedgegrid.optimization.cli analyze btcusdt_opt_v1 \
  --top-n 10 \
  --export-csv top_trials.csv
```

#### `cleanup` Command
Remove low-performing trials:
```bash
uv run python -m naut_hedgegrid.optimization.cli cleanup btcusdt_opt_v1 \
  --keep-top-n 100
```

### 3. Tests (`tests/optimization/`)

Comprehensive test suite:
- `test_param_space.py`: Parameter sampling, ranges, constraint enforcement
- `test_objective.py`: Multi-objective scoring, normalization, component scores
- `test_constraints.py`: Validation logic, violation detection, threshold updates
- `test_results_db.py`: Database operations, thread-safety, CSV export

### 4. Documentation

- **README.md** (10 pages): Complete user guide with examples, best practices, troubleshooting
- **OPTIMIZATION_SUMMARY.md**: This implementation overview
- **Example script** (`examples/optimize_strategy.py`): Full working example

## Architecture

```
User
  |
  v
StrategyOptimizer (orchestrator)
  |
  +-- ParameterSpace --> Optuna Trial
  |
  +-- BacktestRunner --> NautilusTrader Engine
  |
  +-- ReportGenerator --> PerformanceMetrics (32 metrics)
  |
  +-- ConstraintsValidator --> is_valid() bool
  |
  +-- MultiObjectiveFunction --> score float
  |
  +-- OptimizationResultsDB --> SQLite
  |
  v
Best Parameters YAML + CSV Export
```

## Integration Points

### With Existing System
- **BacktestRunner**: Uses existing backtest infrastructure
- **ReportGenerator**: Leverages 32 existing performance metrics
- **HedgeGridConfig**: Generates valid Pydantic configs
- **Config loaders**: Compatible with YAML config system

### Data Flow
1. Load base backtest + strategy configs
2. Sample parameters from Optuna trial
3. Generate temporary strategy YAML
4. Run backtest via BacktestRunner
5. Calculate metrics via ReportGenerator
6. Validate constraints
7. Calculate multi-objective score
8. Save to database
9. Update Optuna study
10. Repeat until n_trials complete

## Key Features

### 1. Bayesian Optimization
- **TPE sampler**: Tree-structured Parzen Estimator for intelligent search
- **Median pruner**: Early termination of unpromising trials
- **10 startup trials**: Random exploration before Bayesian phase
- **Persistent storage**: Resume interrupted optimizations

### 2. Performance
- **Sequential trials**: Avoids database contention and memory issues
- **Fast backtests**: NautilusTrader engine optimized for speed
- **Adaptive normalization**: Faircomparison across trials
- **Indexed queries**: Fast retrieval of best trials

### 3. Robustness
- **Parameter validation**: Ensures valid configs before backtesting
- **Constraint validation**: Filters unprofitable parameter sets
- **Error handling**: Graceful degradation on backtest failures
- **Retry logic**: Up to 3 attempts for transient failures
- **Resource cleanup**: Proper temp file and connection management

### 4. Usability
- **Rich console output**: Live progress, colored status, tables
- **Comprehensive logging**: All trials saved to database
- **CSV export**: Easy analysis in Excel/Python/R
- **Best parameter export**: Drop-in replacement YAML config
- **Study statistics**: Validity rate, avg/best scores, duration

## Usage Examples

### Basic Optimization
```python
from pathlib import Path
from naut_hedgegrid.optimization import StrategyOptimizer

optimizer = StrategyOptimizer(
    backtest_config_path=Path("configs/backtest/btcusdt.yaml"),
    base_strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    n_trials=100,
    study_name="btcusdt_opt"
)

study = optimizer.optimize()
print(f"Best score: {study.best_value:.4f}")
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

# Custom weights (prioritize Sharpe over profit)
weights = ObjectiveWeights(
    sharpe_ratio=0.50,
    profit_factor=0.20,
    calmar_ratio=0.20,
    drawdown_penalty=-0.10
)

# Stricter constraints
constraints = ConstraintThresholds(
    min_sharpe_ratio=1.5,
    max_drawdown_pct=15.0,
    min_trades=100
)

# Narrower parameter bounds
custom_bounds = {
    "GRID_STEP_BPS": ParameterBounds(min_value=20, max_value=100, step=10)
}

optimizer = StrategyOptimizer(
    backtest_config_path=Path("configs/backtest/btcusdt.yaml"),
    base_strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    n_trials=200,
    param_space=ParameterSpace(custom_bounds=custom_bounds),
    objective_weights=weights,
    constraint_thresholds=constraints,
    storage="sqlite:///optuna_studies.db"
)

study = optimizer.optimize()
```

### Analyzing Results
```python
from pathlib import Path
from naut_hedgegrid.optimization import OptimizationResultsDB

db = OptimizationResultsDB(Path("optimization_results.db"))

# Get top 10 trials
best = db.get_best_trials("btcusdt_opt", n=10)
for trial in best:
    print(f"Trial {trial['id']}: Score={trial['score']:.4f}")
    print(f"  Sharpe: {trial['metrics']['sharpe_ratio']:.2f}")
    print(f"  DD: {trial['metrics']['max_drawdown_pct']:.1f}%")

# Study statistics
stats = db.get_study_stats("btcusdt_opt")
print(f"Validity rate: {stats['validity_rate']:.1%}")
print(f"Best score: {stats['best_score']:.4f}")

# Export to CSV
db.export_to_csv("btcusdt_opt", Path("results.csv"))
```

## Performance Considerations

### Recommended Settings
- **100-200 trials**: Good balance of exploration and runtime
- **Sequential execution** (n_jobs=1): Avoids resource contention
- **Shorter backtest periods**: Use 1-3 months for initial exploration
- **Wider search space initially**: Narrow based on results

### Expected Runtime
- Single backtest: 30-120 seconds (depends on data volume)
- 100 trials: 1-3 hours
- 200 trials: 2-6 hours
- 500 trials: 5-15 hours

### Memory Usage
- Per backtest: 100-500 MB (Nautilus engine + data)
- Database: ~100 KB per trial
- 100 trials: ~10 MB database

## Next Steps

### Immediate Use
1. Run example optimization on BTCUSDT
2. Validate best parameters on out-of-sample data
3. Test in paper trading before live deployment

### Future Enhancements
- Multi-instrument optimization
- Walk-forward optimization with rolling windows
- Ensemble parameter sets for robustness
- Real-time monitoring dashboard
- Automated regime-specific parameter switching
- Cloud computing integration for massive parallelization

## Files Created

**Core modules** (6 files, ~1,900 lines):
- `/naut_hedgegrid/optimization/__init__.py`
- `/naut_hedgegrid/optimization/param_space.py` (252 lines)
- `/naut_hedgegrid/optimization/objective.py` (268 lines)
- `/naut_hedgegrid/optimization/constraints.py` (262 lines)
- `/naut_hedgegrid/optimization/results_db.py` (452 lines)
- `/naut_hedgegrid/optimization/parallel_runner.py` (388 lines)
- `/naut_hedgegrid/optimization/optimizer.py` (466 lines)
- `/naut_hedgegrid/optimization/cli.py` (272 lines)

**Tests** (4 files, ~800 lines):
- `/tests/optimization/__init__.py`
- `/tests/optimization/test_param_space.py` (200 lines)
- `/tests/optimization/test_objective.py` (253 lines)
- `/tests/optimization/test_constraints.py` (217 lines)
- `/tests/optimization/test_results_db.py` (178 lines)

**Documentation** (3 files):
- `/naut_hedgegrid/optimization/README.md` (400+ lines)
- `/examples/optimize_strategy.py` (97 lines)
- `/OPTIMIZATION_SUMMARY.md` (this file)

**Total**: ~2,700 lines of production-quality code with comprehensive testing and documentation.

## Quality Assurance

- ✅ All modules import successfully
- ✅ Ruff formatting and linting applied
- ✅ Type hints on all public APIs
- ✅ Pydantic v2 models for validation
- ✅ Error handling throughout
- ✅ Thread-safe database operations
- ✅ Resource cleanup (temp files, connections)
- ✅ Comprehensive docstrings
- ✅ Example usage scripts
- ✅ CLI interface with typer + rich

## Testing

Run tests:
```bash
# All optimization tests
uv run pytest tests/optimization/ -v

# Specific test modules
uv run pytest tests/optimization/test_param_space.py -v
uv run pytest tests/optimization/test_objective.py -v
uv run pytest tests/optimization/test_constraints.py -v
uv run pytest tests/optimization/test_results_db.py -v
```

## Conclusion

The optimization framework is **production-ready** and provides:
- Intelligent parameter search via Bayesian optimization
- Comprehensive performance validation
- Persistent result tracking
- Easy integration with existing backtest infrastructure
- Professional CLI and documentation

The system is designed for **real-world trading strategy optimization** with proper error handling, resource management, and result analysis capabilities.
