# Understanding Optimization Results

## Summary Explanation

When you run optimization, you'll see two different types of "validity" reported:

### 1. During Optimization (Progress Messages)
```
✗ Trial 1: Invalid - Sharpe ratio -0.29 < 0.20, Profit factor 0.00 < 1.05
Trial 1/10 | Best: 0.0598 | Valid: 0/1
```

**"Valid" here means**: Trial passed all constraint checks (profitability thresholds)

### 2. In Final Summary
```
Study Statistics:
  Total Trials     10
  Valid Trials     0       ← Passed constraint checks
  Validity Rate    0.0%
  Best Score       0.0598  ← Best among completed trials

Optimization Summary:
  Total trials: 10
  Completed trials: 8       ← Finished without crashing
  Valid trials (passed constraints): 0  ← Actually profitable
  Validity rate: 0.0%
```

## What Each Metric Means

### Completed Trials
- **Definition**: Trials that ran successfully without errors
- **Your case**: 8/10 trials completed (2 may have crashed or been skipped)
- **What it tells you**: Your code is working, backtests are running

### Valid Trials (Passed Constraints)
- **Definition**: Trials that both completed AND met your profitability constraints
- **Your case**: 0/10 trials passed constraints
- **What it tells you**: None of the parameter combinations were profitable enough

### Your Constraints (from run_optimization_fixed.py)
```python
constraints = ConstraintThresholds(
    min_sharpe_ratio=0.2,       # Need at least 0.2 Sharpe
    max_drawdown_pct=40.0,      # Drawdown must be < 40%
    min_trades=5,               # Need at least 5 trades
    min_win_rate_pct=35.0,      # Win rate must be >= 35%
    min_profit_factor=1.05,     # Must make 5% more than losses
    min_calmar_ratio=0.1        # Positive Calmar ratio
)
```

## Why All Trials Failed Constraints

Looking at your logs:
```
Sharpe ratio -0.29 < 0.20     ← Negative returns (losing money)
Profit factor 0.00 < 1.05     ← No profitable trades
```

**This means**:
1. ✅ The optimization system is working correctly
2. ✅ Backtests are running and calculating metrics
3. ❌ The strategy is **consistently unprofitable** with these parameters

## What To Do Next

### Option 1: Relax Constraints (Discovery Mode)
Allow the optimizer to find ANY parameters that generate trades, even if unprofitable:

```python
constraints = ConstraintThresholds(
    min_sharpe_ratio=-1.0,      # Allow losses
    max_drawdown_pct=90.0,      # Very permissive
    min_trades=3,               # Just need some trades
    min_win_rate_pct=20.0,      # Very low bar
    min_profit_factor=0.5,      # Allow 2:1 loss ratio
    min_calmar_ratio=-1.0       # No profitability requirement
)
```

This will help you understand what parameter ranges generate fills.

### Option 2: Widen Parameter Search Space
The current parameters might be too conservative. Try:

```python
# In param_space.py
GRID_STEP_BPS = ParameterBounds(min_value=50, max_value=200, step=25)  # Wider grids
BASE_QTY = ParameterBounds(min_value=0.001, max_value=0.010, log_scale=True)  # Vary more
```

### Option 3: Test on Different Market Conditions
January 2024 might have been sideways/choppy. Try:
- A trending period (large BTC move)
- Longer time period (3-6 months instead of 1 month)
- Different market regime

### Option 4: Check If Strategy Logic Is Working
Run a single backtest with logging to verify:

```bash
# Run one backtest with default parameters
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml

# Check the logs for:
# - Are grid orders being placed?
# - Are any fills happening?
# - What's the regime detector showing?
```

## Interpretation Guide

| Result | What It Means | Action |
|--------|---------------|---------|
| 0 valid, 0 completed | Backtests crashing | Fix code bugs |
| 0 valid, X completed | Strategy unprofitable | Adjust parameters or strategy logic |
| X valid, Y completed (X < Y) | Some parameters work | Good! Analyze what makes them different |
| X valid, Y completed (X ≈ Y) | Most parameters profitable | Great! Refine further |

## Your Current Status

```
✅ Optimization system: WORKING
✅ Backtests: RUNNING (8/10 completed)
✅ Metrics: CALCULATING
❌ Profitability: NOT ACHIEVED
```

**Next step**: Run with relaxed constraints (Option 1) to understand parameter space better.

## Example: Good Results Would Look Like

```
Study Statistics:
  Total Trials     50
  Valid Trials     18      ← 36% of trials profitable
  Validity Rate    36.0%
  Best Score       2.4521
  Average Score    0.8234

Optimization Summary:
  Completed trials: 48
  Valid trials (passed constraints): 18
  Best valid score: 2.4521
```

This would mean you found 18 profitable parameter combinations out of 50 trials.