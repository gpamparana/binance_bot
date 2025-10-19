# Backtest Issues - Analysis and Fixes

## Issues Identified

### 1. **Inconsistent Backtest Endpoints**
- **Problem**: Backtests using identical configuration were stopping at different times (19:38, 19:50, 19:52) on September 1
- **Cause**: Race condition between NautilusTrader's async logging system and the backtest runner
- **Evidence**:
  - Run 1: Ended at 2025-09-01T19:38:00
  - Run 2: Ended at 2025-09-01T19:52:00
  - Run 3: Ended at 2025-09-01T19:50:00

### 2. **Log Truncation and Out-of-Order Messages**
- **Problem**: Console logs showed "Backtest completed" at line ~117,837 but logs continued to line ~134,000
- **Cause**: NautilusTrader's internal logging buffer was still flushing after `engine.run()` completed
- **Evidence**: Completion messages appeared in the middle of ongoing log output

### 3. **Incomplete Data Range Processing**
- **Problem**: Backtest only processing September 1 data despite config showing Sept 1-2
- **Cause**: The `end_time` parameter is **exclusive** (not inclusive)
- **Evidence**: Only 1,021 bars loaded (Sept 1: 07:00-23:59) instead of expected ~2,460 bars

## Fixes Applied

### Fix 1: Proper Engine Disposal (run_backtest.py:452)
```python
# Dispose the engine to trigger proper cleanup
engine.dispose()
```
This ensures the engine properly shuts down all internal components and flushes pending operations.

### Fix 2: Buffer Flushing (run_backtest.py:454-456)
```python
# Flush Python's stdout/stderr buffers
sys.stdout.flush()
sys.stderr.flush()
```
Forces immediate flush of Python's output buffers to prevent interleaved messages.

### Fix 3: Synchronization Delay (run_backtest.py:460)
```python
# Give any remaining async operations time to complete
time.sleep(1.0)  # Increased delay to ensure all logs are flushed
```
Provides time for NautilusTrader's async logging threads to complete their work.

## Configuration Note

The `end_time` in backtest configuration is **exclusive**. To include multiple days:

```yaml
# WRONG - Only includes Sept 1
time_range:
  start_time: "2025-09-01T00:00:00Z"
  end_time: "2025-09-02T00:00:00Z"  # Stops BEFORE Sept 2

# CORRECT - Includes Sept 1 and Sept 2
time_range:
  start_time: "2025-09-01T00:00:00Z"
  end_time: "2025-09-03T00:00:00Z"  # Includes all of Sept 1 AND Sept 2
```

## Expected Behavior After Fixes

1. **Consistent Endpoints**: All backtests with same config will process the same data range
2. **Clean Log Output**: Completion messages will appear after all backtest logs
3. **Proper Shutdown**: No truncated logs or race conditions
4. **Full Data Range**: When configured correctly, will process all days in the range

## Testing the Fix

Run multiple backtests and verify:
```bash
# Run backtest with logging
./scripts/run_backtest_with_logs.sh

# Check consistency
diff reports/RUN1/my_backtest.log reports/RUN2/my_backtest.log
```

The line counts and final timestamps should now be consistent across runs.

## Root Cause Summary

NautilusTrader uses an asynchronous logging architecture for performance. The backtest engine spawns multiple internal threads/coroutines that continue processing after the main `run()` method returns. Without proper synchronization, the runner would continue while logs were still being written, causing:
- Race conditions in log output
- Inconsistent stopping points
- Interleaved completion messages

The fixes ensure proper shutdown sequencing and synchronization between the engine and the runner.