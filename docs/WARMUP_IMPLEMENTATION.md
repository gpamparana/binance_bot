# Regime Detector Warmup Implementation

## Overview

The warmup feature pre-loads historical market data into the regime detector before live trading starts, eliminating the 26+ minute wait for the detector to become "warm" and ready to make trading decisions.

## How It Works

### 1. Strategy Startup Flow

When the strategy starts (`on_start()` method):

1. **Initialize Components**: Create regime detector, funding guard, order diff engine
2. **Subscribe to Live Data**: Set up real-time bar subscriptions
3. **Perform Warmup**: Automatically fetch and process historical data
4. **Start Trading**: Begin normal operations with pre-warmed detector

### 2. Warmup Process

The warmup happens automatically in the strategy's `_perform_warmup()` method:

```python
# In on_start():
self._perform_warmup()  # Fetches historical data and warms up detector
```

The process:
1. Checks if warmup is enabled (`config.enable_warmup`)
2. Skips warmup in backtests (they have their own data)
3. Fetches API credentials from environment
4. Downloads historical bars from Binance
5. Feeds bars to regime detector sequentially
6. Verifies detector is warm before continuing

### 3. Data Requirements

The number of bars needed depends on your regime detector settings:

```python
warmup_bars = max(regime_cfg.ema_slow + 20, 70)
```

For default settings (EMA slow = 26):
- Minimum bars needed: 46 (26 + 20)
- Actual fetched: 70 (safety margin)

### 4. Configuration

The warmup is configured through the strategy config:

```python
# In base_runner.py when creating strategy config:
config={
    "instrument_id": instrument_id,
    "hedge_grid_config_path": str(strategy_config_path),
    "oms_type": oms_type.value,
    "enable_warmup": True,  # Enable/disable warmup
    "testnet": venue_cfg.api.testnet,  # Use testnet API endpoint
}
```

## Implementation Details

### Strategy Changes

**File**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`

1. **Added `_perform_warmup()` method** (lines 249-327):
   - Checks configuration and environment
   - Creates BinanceDataWarmer instance
   - Fetches historical bars
   - Calls `warmup_regime_detector()`

2. **Updated `on_start()` method** (line 245):
   - Calls `_perform_warmup()` after component initialization

3. **Existing `warmup_regime_detector()` method** (lines 329-368):
   - Feeds historical bars to detector
   - Logs progress and final state

### Config Changes

**File**: `naut_hedgegrid/strategies/hedge_grid_v1/config.py`

Added two new config fields:
```python
enable_warmup: bool = True  # Enable regime detector warmup on start
testnet: bool = False  # Whether using testnet (for warmup API endpoint)
```

### Runner Changes

**File**: `naut_hedgegrid/runners/base_runner.py`

1. **Passes warmup config to strategy** (lines 215-216):
   - Sets `enable_warmup=True`
   - Passes `testnet` flag from venue config

2. **Removed old warmup attempt** (deleted lines 341-418):
   - Previously tried to warmup between build() and run()
   - Now handled directly in strategy's on_start()

### Data Warmer Module

**File**: `naut_hedgegrid/warmup/binance_warmer.py`

Provides the `BinanceDataWarmer` class that:
- Fetches historical klines from Binance API
- Supports both testnet and production endpoints
- Converts klines to DetectorBar format
- Handles rate limiting and pagination

## Usage

### Normal Operation (Automatic)

When you run live/paper trading, warmup happens automatically:

```bash
uv run python -m naut_hedgegrid paper-trade \
    --venue-config configs/venues/binance_testnet.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml
```

You'll see in the logs:
```
[2025-01-20 10:30:00] Starting warmup: fetching 70 historical bars for BTCUSDT (testnet=True)
[2025-01-20 10:30:01] ✓ Fetched 70 historical bars
[2025-01-20 10:30:01] Warming up regime detector with 70 historical bars
[2025-01-20 10:30:01] ✓ Regime detector warmup complete: current regime=SIDEWAYS, warm=True
```

### Disable Warmup

To disable warmup (not recommended), you can:

1. Set environment variable:
```bash
export DISABLE_WARMUP=true
```

2. Or modify the runner to pass `enable_warmup=False`

### Requirements

- **API Credentials**: Binance API key/secret must be set in environment
- **Network Access**: Must be able to reach Binance API endpoints
- **Sufficient History**: Binance must have enough historical data for the symbol

## Benefits

1. **Immediate Trading**: Start trading immediately instead of waiting 26+ minutes
2. **Accurate Regime Detection**: Detector has full context from the start
3. **Better Grid Placement**: Initial grids placed with correct regime classification
4. **Reduced Risk**: Avoid trading blind during warmup period

## Troubleshooting

### "No Binance API credentials found"

Set your API credentials:
```bash
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret
```

### "Warmup module not available"

Ensure the warmup module is installed:
```bash
uv sync --all-extras
```

### "No historical bars fetched"

Check:
1. Network connectivity to Binance
2. Symbol exists and has trading history
3. API credentials are valid
4. Not hitting rate limits

### Detector Still Not Warm

If detector isn't warm after fetching bars:
1. Increase the number of bars fetched
2. Check regime detector settings (EMA periods)
3. Verify bars are being processed correctly

## Testing

Test the warmup independently:
```bash
uv run python test_warmup_integration.py
```

Expected output:
```
Testing regime detector warmup...
Initial state - Regime: SIDEWAYS, Warm: False
Fetching historical bars from Binance...
Fetched 50 bars
Warming up detector...
  Progress: 10/50 - Regime: SIDEWAYS, Warm: False
  Progress: 20/50 - Regime: SIDEWAYS, Warm: False
  Progress: 30/50 - Regime: UP, Warm: True
  Progress: 40/50 - Regime: UP, Warm: True
  Progress: 50/50 - Regime: SIDEWAYS, Warm: True

Final state - Regime: SIDEWAYS, Warm: True
✓ Warmup successful!
  EMA Fast: 110234.56
  EMA Slow: 110198.23
  ADX: 28.45
  ATR: 156.78
```

## Summary

The warmup implementation successfully:
- ✅ Fetches historical data on strategy startup
- ✅ Pre-warms the regime detector before trading
- ✅ Eliminates the 26+ minute wait period
- ✅ Works with both testnet and production
- ✅ Handles errors gracefully (continues without warmup if it fails)
- ✅ Integrates seamlessly with existing strategy code

The strategy is now ready to trade immediately upon startup!