# Quick Start Guide - Fixed Optimization System

**Status:** All fixes applied ✓
**Test backtest:** Running (check `test_backtest_fixed.log`)
**Ready for:** Production optimization

---

## What Was Fixed

### 1. Duplicate ClientOrderId Bug ✓
**File:** `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`

Changed TP/SL order ID generation to use fill event timestamps instead of current clock time, ensuring uniqueness even when multiple fills occur at the same level.

### 2. Optimization Parameter Bounds ✓
**File:** `naut_hedgegrid/optimization/param_space.py`

Updated bounds to be realistic for live trading:
- Grid step: 10-50 bps (was 10-200 bps)
- Base qty: 0.005-0.020 BTC (was 0.001-0.004 BTC)
- Min levels: 5 (was 3)

### 3. Optimization Constraints ✓
**File:** `run_optimization_fixed.py`

Better constraint thresholds to filter unviable strategies:
- min_sharpe_ratio: 0.5
- max_drawdown_pct: 30.0
- min_trades: 10 (was 1)
- min_win_rate_pct: 40.0
- min_profit_factor: 1.1

---

## How to Run

### Step 1: Verify Test Backtest Completed

```bash
# Check if backtest finished
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/binance_bot
tail test_backtest_fixed.log

# Look for "Results saved to:" and check for errors
grep -i "error\|results saved" test_backtest_fixed.log | tail -20

# If completed, check the results
ls -la reports/  # Find the latest report directory
cat reports/YYYYMMDD_HHMMSS/summary.json
```

**Expected results:**
- ✓ total_trades > 0 (not zero like before)
- ✓ No duplicate ClientOrderId errors
- ✓ total_pnl should be reasonable (not -40% loss)

### Step 2: Run Fixed Optimization

```bash
# Run optimization with fixed parameters
uv run python run_optimization_fixed.py

# This will:
# - Run 50 trials (~1-2 hours)
# - Save results to optimization_results_fixed.csv
# - Save best config to configs/strategies/hedge_grid_fixed_optimization_best.yaml
```

### Step 3: Monitor Progress

```bash
# Watch optimization progress (in another terminal)
watch -n 30 'sqlite3 optimization_results.db "SELECT COUNT(*) as trials, SUM(CASE WHEN is_valid=1 THEN 1 ELSE 0 END) as valid, MAX(score) as best_score FROM trials WHERE study_name='\''hedge_grid_fixed_optimization'\''"'

# Or check manually
sqlite3 optimization_results.db "SELECT id, total_trades, sharpe_ratio, score FROM trials WHERE study_name='hedge_grid_fixed_optimization' ORDER BY id DESC LIMIT 10"
```

### Step 4: Analyze Results

```bash
# After optimization completes, view summary
cat optimization_results_fixed.csv

# Test the best parameters
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_fixed_optimization_best.yaml
```

---

## Expected Outcomes

### Before Fixes (Old Runs)
- ❌ 10/10 trials invalid (0 trades each)
- ❌ Score: -Inf for all
- ❌ Duplicate ClientOrderId errors

### After Fixes (Expected)
- ✓ 10-30 valid trials out of 50 (20-60% validity rate)
- ✓ Valid trials: 10+ trades each
- ✓ Best score > 0
- ✓ No duplicate ClientOrderId errors
- ✓ Sharpe ratio >= 0.5 for best trial
- ✓ Win rate >= 40% for best trial

---

## Troubleshooting

### If test backtest shows 0 trades:
```bash
# Check if data loaded correctly
grep "Loaded.*bars\|No data" test_backtest_fixed.log

# Check regime detector warmup
grep "regime.*warm" test_backtest_fixed.log | head -10

# Verify grid orders created
grep "Built.*ladder" test_backtest_fixed.log | head -20
```

### If optimization trials still show 0 trades:
```bash
# Check parameter sampling
sqlite3 optimization_results.db "SELECT json_extract(parameters, '$.grid.base_qty') as base_qty, json_extract(parameters, '$.grid.grid_step_bps') as step FROM trials WHERE study_name='hedge_grid_fixed_optimization' LIMIT 10"

# Adjust bounds further if needed (edit param_space.py)
# Maybe increase base_qty minimum to 0.010 BTC
# Maybe decrease grid_step_bps maximum to 30 bps
```

### If seeing duplicate ClientOrderId errors:
```bash
# This should NOT happen after the fix
# But if it does, check:
grep "duplicate ClientOrderId" test_backtest_fixed.log

# Verify the fix was applied
grep "fill_event_ts" naut_hedgegrid/strategies/hedge_grid_v1/strategy.py
# Should see: def _create_tp_order(..., fill_event_ts: int)
```

---

## Files Created/Modified

### Modified
1. `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py` (lines 807-824, 1288-1400)
2. `naut_hedgegrid/optimization/param_space.py` (lines 43-51)

### Created
1. `run_optimization_fixed.py` - Updated optimization script
2. `OPTIMIZATION_FIXES.md` - Detailed documentation
3. `QUICK_START_GUIDE.md` - This file
4. `test_backtest_fixed.log` - Test backtest output (running)

---

## Next Steps After Optimization

1. **Validate best parameters:**
   ```bash
   uv run python -m naut_hedgegrid backtest \
       --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
       --strategy-config configs/strategies/hedge_grid_fixed_optimization_best.yaml
   ```

2. **Test on out-of-sample data:**
   - Edit backtest config: change date range to 2024-07-01 to 2024-10-01
   - Run backtest with best parameters
   - Verify performance holds up

3. **Paper trading:**
   ```bash
   # Set environment variables
   export BINANCE_API_KEY="your_key"
   export BINANCE_API_SECRET="your_secret"

   # Run paper trading (no real money)
   uv run python -m naut_hedgegrid paper \
       --strategy-config configs/strategies/hedge_grid_fixed_optimization_best.yaml \
       --venue-config configs/venues/binance_futures.yaml
   ```

4. **Live trading (when ready):**
   ```bash
   # Start live with operational controls
   uv run python -m naut_hedgegrid live \
       --strategy-config configs/strategies/hedge_grid_fixed_optimization_best.yaml \
       --venue-config configs/venues/binance_futures.yaml \
       --enable-ops \
       --prometheus-port 9090 \
       --api-port 8080
   ```

---

## Questions?

- Check `OPTIMIZATION_FIXES.md` for detailed problem analysis
- Check `OPTIMIZATION_SUMMARY.md` for system architecture
- Check project `CLAUDE.md` for general development guidelines

**Good luck with your optimization! 🚀**
