# Parameter Optimization Guide

Complete guide for optimizing HedgeGridV1 strategy parameters using Bayesian optimization.

## Overview

The parameter optimization system uses **Optuna** (Bayesian optimization framework) to intelligently search for optimal strategy parameters. It combines multiple objectives (Sharpe ratio, profit, Calmar ratio, drawdown) and applies hard constraints to ensure only profitable parameter sets are considered.

## Quick Start

### Step 1: Download Historical Data from Binance

Before running optimization, you need to download historical market data. The system includes a built-in data pipeline for fetching data directly from Binance.

#### Download 6 Months of Data (Recommended)

```bash
# Download data for BTCUSDT from April 2024 to October 2024 (6 months)
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol BTCUSDT \
    --start 2024-04-01 \
    --end 2024-10-01 \
    --output ./data/catalog \
    --data-types trades,mark,funding
```

**What this downloads:**
- **Trades**: Individual trade executions (used for realistic fill simulation)
- **Mark Prices**: 1-minute OHLCV bars from mark price (used for strategy bars)
- **Funding Rates**: 8-hour funding rate data (critical for perpetual futures)

**Expected Duration**: ~30-60 minutes for 6 months of data (depends on network speed and Binance API)

#### Download Different Time Periods

```bash
# 3 months (faster, good for initial testing)
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol BTCUSDT \
    --start 2024-07-01 \
    --end 2024-10-01 \
    --output ./data/catalog \
    --data-types trades,mark,funding

# 1 year (most robust, longer download time)
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol BTCUSDT \
    --start 2024-10-01 \
    --end 2025-10-01 \
    --output ./data/catalog \
    --data-types mark,funding
```

#### Download Other Instruments

```bash
# Ethereum perpetual
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol ETHUSDT \
    --start 2024-04-01 \
    --end 2024-10-01 \
    --output ./data/catalog \
    --data-types trades,mark,funding

# Solana perpetual
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol SOLUSDT \
    --start 2024-04-01 \
    --end 2024-10-01 \
    --output ./data/catalog \
    --data-types trades,mark,funding
```

#### Verify Downloaded Data

After download completes, verify the data structure:

```bash
# Check catalog structure
ls -lh data/catalog/

# Should see directories for each instrument:
# BTCUSDT-PERP.BINANCE/
# ├── bar/              # Mark price bars (1-minute OHLCV)
# ├── trade_tick/       # Individual trades
# └── funding_rate.parquet  # Funding rate history

# Count number of records
uv run python -c "
import pandas as pd
from pathlib import Path

catalog = Path('data/catalog')
for instrument_dir in catalog.glob('*-PERP.BINANCE'):
    print(f'\n{instrument_dir.name}:')

    # Count bars
    bar_files = list((instrument_dir / 'bar').glob('*.parquet'))
    if bar_files:
        bars_df = pd.concat([pd.read_parquet(f) for f in bar_files])
        print(f'  Bars: {len(bars_df):,} records')

    # Count trades
    trade_files = list((instrument_dir / 'trade_tick').glob('*.parquet'))
    if trade_files:
        trades_df = pd.concat([pd.read_parquet(f) for f in trade_files])
        print(f'  Trades: {len(trades_df):,} records')

    # Count funding rates
    funding_file = instrument_dir / 'funding_rate.parquet'
    if funding_file.exists():
        funding_df = pd.read_parquet(funding_file)
        print(f'  Funding: {len(funding_df):,} records')
"
```

**Expected Output**:
```
BTCUSDT-PERP.BINANCE:
  Bars: 259,200 records (6 months × 1440 minutes/day)
  Trades: ~5,000,000 records (depends on market activity)
  Funding: 540 records (6 months × 3 funding events/day)
```

#### Troubleshooting Data Download

**Issue: "Rate limit exceeded" (HTTP 429)**
```bash
# Increase delay between requests (default is 0.5s)
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol BTCUSDT \
    --start 2024-04-01 \
    --end 2024-10-01 \
    --output ./data/catalog \
    --data-types trades,mark,funding \
    --config data_source_config.json

# Create config file: data_source_config.json
{
  "rate_limit_delay": 1.0,  # 1 second delay between requests
  "request_limit": 500      # Max requests per batch
}
```

**Issue: "Connection timeout" or "Network error"**
- Download shorter periods (1 month at a time)
- Check internet connection stability
- Retry failed downloads (pipeline is idempotent)

**Issue: "Missing data for specific dates"**
- Binance may have gaps during maintenance
- Download surrounding dates and interpolate if needed
- Check Binance status page for historical outages

### Step 2: Verify Data Quality

```bash
# Run data verification script
uv run python examples/verify_data_pipeline.py --catalog ./data/catalog

# Check for gaps in time series
uv run python -c "
import pandas as pd
from pathlib import Path

catalog = Path('data/catalog/BTCUSDT-PERP.BINANCE')
bars_file = list((catalog / 'bar').glob('*.parquet'))[0]
df = pd.read_parquet(bars_file)

# Check for gaps (>1 minute between bars)
df['ts_event'] = pd.to_datetime(df['ts_event'])
gaps = df['ts_event'].diff() > pd.Timedelta('2 minutes')
print(f'Found {gaps.sum()} gaps in bar data')

if gaps.sum() > 0:
    print('\nGap locations:')
    print(df[gaps][['ts_event']].head(10))
"
```

### Step 3: Install Dependencies

```bash
# Ensure all dependencies are installed
uv sync --all-extras

# Verify optimization module is available
uv run python -c "from naut_hedgegrid.optimization import StrategyOptimizer; print('✓ Optimization module ready')"
```

### Step 4: Run Optimization

Now you're ready to optimize!

```bash
# Run optimization with default settings
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 100 \
    --study-name btcusdt_opt_v1
```

uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 10 \
    --study-name quick_test

uv run python -m naut_hedgegrid.optimization.cli optimize \
      --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
      --strategy-config configs/strategies/hedge_grid_v1.yaml \
      --trials 200 \
      --study-name btcusdt_overnight_$(date +%Y%m%d) \
      2>&1 | tee logs/optimization_overnight_$(date +%Y%m%d_%H%M%S).log
      
### Expected Output

```
╔══════════════════════════════════════════════════════════╗
║         Strategy Parameter Optimization                   ║
╚══════════════════════════════════════════════════════════╝

Study: btcusdt_opt_v1
Sampler: TPE (Bayesian Optimization)
Trials: 100
Workers: 4

[━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━] 100/100 100% 0:03:24

✓ Optimization complete!

Best Trial (#67):
├── Score: 2.47
├── Sharpe Ratio: 1.82
├── Total Profit: $1,245.67
├── Calmar Ratio: 2.34
├── Max Drawdown: 8.5%
├── Win Rate: 58.3%
└── Trades: 234

Best parameters saved to:
configs/strategies/btcusdt_opt_v1_best.yaml

Results database:
artifacts/optimization/btcusdt_opt_v1/study.db
```

## System Architecture

### Components

1. **ParameterSpace** (`param_space.py`)
   - Defines searchable parameter ranges
   - 17 tunable parameters across 7 categories
   - Optuna distribution types (continuous, integer, categorical)

2. **MultiObjectiveFunction** (`objective.py`)
   - Combines 4 metrics: Sharpe (30%), Profit (25%), Calmar (25%), Drawdown (-20%)
   - Adaptive normalization for fair weighting
   - Component score breakdown

3. **ConstraintsValidator** (`constraints.py`)
   - Hard filters for unprofitable parameters
   - 6 constraints: Sharpe ≥ 1.0, DD ≤ 20%, Trades ≥ 50, etc.
   - Strict and lenient validation modes

4. **OptimizationResultsDB** (`results_db.py`)
   - Thread-safe SQLite storage
   - Query best trials, export CSV, analyze importance
   - Persistent across runs

5. **ParallelBacktestRunner** (`parallel_runner.py`)
   - Multi-process execution
   - Rich progress bars, retry logic
   - Configurable worker count

6. **StrategyOptimizer** (`optimizer.py`)
   - Main orchestrator
   - Optuna study with TPE sampler
   - Auto-generates configs, runs backtests, saves results

## Tunable Parameters

### Grid Parameters (5)
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| grid_step_bps | 10-200 | 50 | Grid spacing in bps (0.1%-2%) |
| grid_levels_long | 3-20 | 10 | Buy levels below mid price |
| grid_levels_short | 3-20 | 10 | Sell levels above mid price |
| base_qty | 0.001-0.1 | 0.01 | Base order quantity (log scale) |
| qty_scale | 1.0-1.5 | 1.1 | Quantity multiplier per level |

### Exit Parameters (2)
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| tp_steps | 1-10 | 2 | Take profit after N grid steps |
| sl_steps | 3-20 | 8 | Stop loss after N grid steps |

### Regime Detection (5)
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| adx_len | 7-30 | 14 | ADX period for trend strength |
| ema_fast | 5-25 | 12 | Fast EMA period |
| ema_slow | 20-60 | 26 | Slow EMA period |
| atr_len | 7-30 | 14 | ATR period for volatility |
| hysteresis_bps | 5-50 | 10 | Regime change threshold |

### Policy Parameters (2)
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| counter_levels | 2-10 | 5 | Counter-trend levels |
| counter_qty_scale | 0.3-0.8 | 0.5 | Counter-trend qty scale |

### Other Parameters (3)
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| recenter_trigger_bps | 50-500 | 200 | Grid recenter trigger |
| funding_max_cost_bps | 5-50 | 20 | Max funding cost threshold |
| max_position_pct | 50-95 | 80 | Max position % of balance |

## Multi-Objective Function

The optimizer combines 4 metrics into a single score:

```
score = 0.30 × sharpe_ratio +
        0.25 × profit_factor +
        0.25 × calmar_ratio -
        0.20 × drawdown_penalty
```

### Why Multi-Objective?

- **Single objectives** (e.g., only Sharpe) can lead to overfitting
- **Combining metrics** ensures robust strategies
- **Balanced approach**: profit + risk + consistency

### Metric Normalization

- All metrics are normalized to [0, 1] range
- Normalization updated every 20 trials
- Ensures fair weighting across different scales

## Hard Constraints

Trials violating any constraint are scored as `-inf` (rejected):

| Constraint | Threshold | Reason |
|------------|-----------|--------|
| Sharpe Ratio | ≥ 1.0 | Ensures risk-adjusted profitability |
| Max Drawdown | ≤ 20% | Capital preservation |
| Total Trades | ≥ 50 | Statistical significance |
| Win Rate | ≥ 45% | Positive edge |
| Profit Factor | ≥ 1.1 | Wins > Losses |
| Calmar Ratio | ≥ 0.5 | Return/drawdown balance |

## Configuration

Edit `configs/optimization/default_optimization.yaml`:

```yaml
# Study settings
study_name: "my_optimization"
n_trials: 100  # Increase for thorough search
n_jobs: 4  # Parallel workers (CPU count - 1)

# Objective weights (sum to 1.0)
objective:
  sharpe_ratio: 0.30
  total_profit: 0.25
  calmar_ratio: 0.25
  max_drawdown: -0.20  # Negative = penalty

# Constraints
constraints:
  min_sharpe_ratio: 1.0
  max_drawdown_pct: 20.0
  min_trades: 50
  min_win_rate_pct: 45.0
```

## Advanced Usage

### Custom Parameter Ranges

```python
from naut_hedgegrid.optimization import ParameterSpace

# Create custom parameter space
param_space = ParameterSpace()

# Override specific ranges
param_space.ranges['grid_step_bps'] = (20, 100)  # Narrower range
param_space.ranges['tp_steps'] = (2, 5)  # Conservative exits

# Use in optimizer
optimizer = StrategyOptimizer(
    param_space=param_space,
    # ... other args
)
```

### Custom Objective Weights

```python
from naut_hedgegrid.optimization import MultiObjectiveFunction

# Create custom objective
objective = MultiObjectiveFunction(
    weights={
        'sharpe_ratio': 0.40,  # More emphasis on risk-adjusted returns
        'total_profit': 0.20,
        'calmar_ratio': 0.20,
        'max_drawdown': -0.20,
    }
)

optimizer = StrategyOptimizer(
    objective_fn=objective,
    # ... other args
)
```

### Analyze Results

```bash
# View best trials
uv run python -m naut_hedgegrid.optimization.cli analyze \
    --study-name btcusdt_opt_v1 \
    --top-n 10

# Export to CSV for analysis
uv run python -m naut_hedgegrid.optimization.cli analyze \
    --study-name btcusdt_opt_v1 \
    --export-csv results.csv
```

### Clean Up Poor Trials

```bash
# Remove trials with score < 0
uv run python -m naut_hedgegrid.optimization.cli cleanup \
    --study-name btcusdt_opt_v1 \
    --min-score 0.0
```

## Performance Estimates

### Runtime (6-month backtest, 100 trials)

| Workers | Total Time | Time/Trial |
|---------|------------|------------|
| 1 | 12-15 hours | 7-9 min |
| 4 | 3-4 hours | Parallel |
| 8 | 2-2.5 hours | Diminishing returns |

### Convergence

- **Trials 1-10**: Random exploration
- **Trials 10-50**: Rapid improvement (Bayesian learning)
- **Trials 50-100**: Fine-tuning (diminishing returns)
- **Best results**: Typically found by trial 60-80

## Best Practices

### 1. Data Preparation

```bash
# Ensure complete data for optimization period
# 6 months recommended for robust results
# 1 year+ for production strategies

# Check data coverage
uv run python examples/verify_data_pipeline.py
```

### 2. Start Small

```bash
# Quick test with 10 trials
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --trials 10 \
    --study-name test_run

# Full optimization after validation
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --trials 100 \
    --study-name production_run
```

### 3. Validate Results

```bash
# After optimization, test on out-of-sample data
uv run python -m naut_hedgegrid backtest \
    --strategy-config configs/strategies/btcusdt_opt_v1_best.yaml \
    --backtest-config configs/backtest/btcusdt_out_of_sample.yaml
```

### 4. Avoid Overfitting

- Use constraints to prevent extreme parameters
- Test optimized params on different time periods
- Compare multiple optimization runs for stability
- Always validate in paper trading before live

## Troubleshooting

### Issue: Optimization is slow

**Solutions**:
- Increase `n_jobs` (more parallel workers)
- Reduce backtest period (3 months instead of 6)
- Reduce `n_trials` (50 instead of 100)
- Check if disk I/O is bottleneck (use SSD)

### Issue: All trials violate constraints

**Solutions**:
- Relax constraints (lower min_sharpe, raise max_drawdown)
- Widen parameter ranges
- Check if base strategy config is valid
- Verify data quality and coverage

### Issue: Optimization crashes

**Solutions**:
```bash
# Check logs
tail -100 artifacts/optimization/study_name/optimization.log

# Reduce workers to isolate issue
--n-jobs 1

# Validate backtest config manually
uv run python -m naut_hedgegrid backtest \
    --strategy-config configs/strategies/hedge_grid_v1.yaml
```

### Issue: Results not reproducible

**Solutions**:
- Set random seed in config:
  ```yaml
  sampler:
    seed: 42
  ```
- Use same data files and time ranges
- Check for non-deterministic components

## Safety and Validation

### Overfitting Prevention

1. **Out-of-sample testing**: Test on unseen data period
2. **Walk-forward validation**: Rolling time windows
3. **Parameter stability**: Run optimization multiple times
4. **Constraint enforcement**: Hard limits prevent extremes

### Robustness Checks

```bash
# 1. Test on different market regimes
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/bull_market.yaml

uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/bear_market.yaml

# 2. Compare results
uv run python -m naut_hedgegrid.optimization.cli analyze \
    --study-name bull_market_opt \
    --compare-with bear_market_opt

# 3. Test in paper trading
uv run python -m naut_hedgegrid paper \
    --strategy-config configs/strategies/optimized_params.yaml
```

## Example Workflow

### Complete Optimization Pipeline

```bash
# 1. Download historical data (6 months from Binance)
uv run python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source binance \
    --symbol BTCUSDT \
    --start 2024-04-01 \
    --end 2024-10-01 \
    --output ./data/catalog \
    --data-types trades,mark,funding

# Wait for download to complete (~30-60 minutes)
# Verify data was downloaded successfully
ls -lh data/catalog/BTCUSDT-PERP.BINANCE/

# 2. Run optimization
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_6months.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 100 \
    --study-name btcusdt_prod_v1

# 3. Analyze results
uv run python -m naut_hedgegrid.optimization.cli analyze \
    --study-name btcusdt_prod_v1 \
    --top-n 10 \
    --export-csv analysis.csv

# 4. Validate on out-of-sample period
uv run python -m naut_hedgegrid backtest \
    --strategy-config configs/strategies/btcusdt_prod_v1_best.yaml \
    --backtest-config configs/backtest/btcusdt_out_of_sample.yaml

# 5. Test in paper trading (24-48 hours)
uv run python -m naut_hedgegrid paper \
    --strategy-config configs/strategies/btcusdt_prod_v1_best.yaml

# 6. Deploy to live (if paper trading successful)
uv run python -m naut_hedgegrid live \
    --strategy-config configs/strategies/btcusdt_prod_v1_best.yaml
```

## Expected Results

### Baseline (Default Parameters)
- Sharpe Ratio: 0.8-1.2
- Total Return: 5-15% (6 months)
- Max Drawdown: 10-15%
- Win Rate: 50-55%

### After Optimization (Target)
- Sharpe Ratio: 1.5-2.0+ (improvement: 20-50%)
- Total Return: 10-25% (6 months)
- Max Drawdown: 8-12% (reduction: 20-40%)
- Win Rate: 55-62%

**Note**: Actual results depend on market conditions, data quality, and optimization period.

## References

- [Optuna Documentation](https://optuna.readthedocs.io/)
- [NautilusTrader Docs](https://nautilustrader.io/docs)
- [Project Documentation](../README.md)
- [Strategy Configuration](../CLAUDE.md)

## Support

For issues or questions:
1. Check logs: `artifacts/optimization/{study_name}/optimization.log`
2. Review test results: `uv run pytest tests/optimization/ -v`
3. Consult documentation: `docs/`
4. Raise issue on GitHub

---

**Document Version**: 1.0
**Last Updated**: 2025-10-22
**Author**: naut-hedgegrid optimization system
