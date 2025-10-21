# Data Warmup System Guide

## Overview

The data warmup system ensures that strategy components like the regime detector have sufficient historical data before live trading begins. This prevents the strategy from making uninformed decisions during the initial "cold start" period.

## Why Warmup is Important

Technical indicators and regime detectors require a certain number of historical data points to produce reliable signals:

- **EMA (Exponential Moving Average)**: Needs at least N periods to stabilize
- **ADX (Average Directional Index)**: Requires ~2x its period for accuracy
- **ATR (Average True Range)**: Needs its period + buffer for initialization

Without warmup, the HedgeGridV1 strategy's regime detector would:
1. Start in SIDEWAYS mode by default
2. Take 50+ bars to become "warm"
3. Make suboptimal trading decisions during this period

## Architecture

The warmup system consists of three main components:

### 1. BinanceDataWarmer (`naut_hedgegrid/warmup/binance_warmer.py`)

Responsible for fetching historical klines (bar) data from Binance API:

- Automatically detects testnet vs production based on venue config
- Handles pagination for large data requests
- Rate limits API calls to avoid throttling
- Converts Binance klines to NautilusTrader Bar objects
- Provides DetectorBar format for regime detector

### 2. Strategy Warmup Method (`HedgeGridV1.warmup_regime_detector()`)

Added to the HedgeGridV1 strategy to accept historical data:

- Feeds historical bars to the regime detector
- Logs warmup progress
- Reports final indicator values
- Non-blocking: continues even if warmup fails

### 3. Runner Integration (`BaseRunner._warmup_strategy()`)

Orchestrates the warmup process in paper/live trading:

- Executes after strategy initialization (`on_start()`)
- Fetches historical data based on configuration
- Calls strategy's warmup method
- Handles errors gracefully without failing startup

## How It Works

### Warmup Flow

```
1. Trading node built
   ↓
2. Node.run() called → Strategy.on_start() executed
   ↓
3. Regime detector and components initialized
   ↓
4. BaseRunner._warmup_strategy() called
   ↓
5. BinanceDataWarmer fetches historical bars
   ↓
6. Strategy.warmup_regime_detector() feeds data to detector
   ↓
7. Detector becomes "warm" with all indicators ready
   ↓
8. Live data streaming begins
   ↓
9. Strategy trades with fully warmed detector
```

### Number of Bars Required

The system automatically calculates the required number of historical bars:

```python
warmup_bars = max(slow_ema_period + 20, 70)
```

For default configuration:
- Slow EMA: 50 periods
- Buffer: 20 periods (for ADX/ATR)
- Minimum: 70 bars
- **Result: 70 historical 1-minute bars fetched**

## Configuration

### Venue Configuration

The warmup system uses the venue configuration to determine API endpoints:

```yaml
# configs/venues/binance_testnet.yaml
api:
  testnet: true  # Uses testnet.binancefuture.com
  api_key: ${BINANCE_API_KEY}
  api_secret: ${BINANCE_API_SECRET}
```

```yaml
# configs/venues/binance_prod.yaml
api:
  testnet: false  # Uses fapi.binance.com
  api_key: ${BINANCE_API_KEY}
  api_secret: ${BINANCE_API_SECRET}
```

### Strategy Configuration

The regime detector parameters determine warmup requirements:

```yaml
# configs/strategies/hedge_grid_v1.yaml
regime:
  ema_fast: 21    # Fast EMA period
  ema_slow: 50    # Slow EMA period (determines minimum warmup)
  adx_len: 14     # ADX period
  atr_len: 14     # ATR period
```

## API Requirements

### Testnet

- **Endpoint**: `https://testnet.binancefuture.com/fapi/v1/klines`
- **Authentication**: Not required for klines endpoint
- **Rate Limits**: More lenient than production

### Production

- **Endpoint**: `https://fapi.binance.com/fapi/v1/klines`
- **Authentication**: Not required for klines endpoint
- **Rate Limits**: Standard Binance limits apply

## Usage Examples

### Paper Trading with Warmup

```bash
# Set API credentials (required for instrument metadata)
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret

# Run paper trading - warmup happens automatically
python -m naut_hedgegrid paper \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_testnet.yaml
```

### Live Trading with Warmup

```bash
# Live trading also gets automatic warmup
python -m naut_hedgegrid live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_prod.yaml
```

### Testing Warmup System

```bash
# Run standalone warmup test
python test_warmup.py

# Run example with detailed output
python examples/paper_trade_with_warmup.py
```

## Console Output

During startup, you'll see warmup progress:

```
[bold]Warming up strategy components...[/bold]
[yellow]Using Binance Testnet for data warmup[/yellow]
[cyan]Fetching 70 historical bars for BTCUSDT...[/cyan]
[green]Fetched 70 detector bars[/green]
[green]✓ Fetched 70 historical bars[/green]
Warming up regime detector with 70 historical bars
✓ Regime detector warmup complete: current regime=UP, EMA fast=69850.23, EMA slow=69720.45, ADX=25.30
[green]✓ Strategy regime detector warmed up[/green]
```

## Error Handling

The warmup system is designed to be non-blocking:

### Network Errors
- If historical data fetch fails, strategy starts without warmup
- Warning is logged but startup continues
- Strategy will warm up naturally as live bars arrive

### Invalid Data
- Malformed bars are skipped
- Partial warmup is better than no warmup
- System logs warnings for debugging

### Missing Components
- If strategy doesn't support warmup, system continues
- If regime detector not initialized, warmup is skipped
- All errors are logged but don't prevent trading

## Performance Considerations

### API Rate Limits

The system implements rate limiting:
- 200ms delay between requests
- Maximum 500 bars per request (Binance limit)
- Automatic pagination for large requests

### Memory Usage

Historical bars are temporary:
- Fetched bars are fed to detector immediately
- No long-term storage of historical data
- Memory freed after warmup completes

### Startup Time

Typical warmup adds 2-5 seconds to startup:
- Fetching 70 bars: ~1-2 seconds
- Processing bars: <1 second
- Total overhead: Minimal

## Troubleshooting

### Warmup Not Working

1. **Check API connectivity**:
   ```bash
   curl https://testnet.binancefuture.com/fapi/v1/ping
   ```

2. **Verify symbol exists**:
   ```bash
   curl "https://testnet.binancefuture.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1"
   ```

3. **Check console output** for warnings/errors

### Detector Still Not Warm

1. **Increase warmup bars**:
   - Edit `BaseRunner._warmup_strategy()`
   - Change: `warmup_bars = max(slow_ema_period + 20, 70)`
   - To: `warmup_bars = 100`  # More bars

2. **Verify indicator periods** in strategy config

3. **Check detector implementation** for warmup requirements

### API Errors

1. **400 Bad Request**: Invalid symbol or parameters
2. **429 Too Many Requests**: Rate limit exceeded
3. **503 Service Unavailable**: Binance maintenance

## Testing

### Unit Test

```python
# Test BinanceDataWarmer in isolation
from naut_hedgegrid.warmup import BinanceDataWarmer

with BinanceDataWarmer(venue_cfg) as warmer:
    bars = warmer.fetch_detector_bars("BTCUSDT", 10, "1m")
    assert len(bars) == 10
```

### Integration Test

```python
# Test full warmup flow
from naut_hedgegrid.strategy.detector import RegimeDetector

detector = RegimeDetector(...)
for bar in historical_bars:
    detector.update_from_bar(bar)
assert detector.is_warm
```

## Future Enhancements

Potential improvements to the warmup system:

1. **Cache historical data** to avoid repeated API calls
2. **Support multiple timeframes** for multi-timeframe strategies
3. **Parallel warmup** for multiple instruments
4. **Warmup from local data** (Parquet files)
5. **Progressive warmup** during quiet market periods
6. **Warmup health metrics** in monitoring dashboard

## Related Documentation

- [Regime Detector Guide](REGIME_DETECTOR_GUIDE.md)
- [Trading Node Integration](TRADINGNODE_INTEGRATION.md)
- [Paper Trading Guide](PAPER_TRADING_GUIDE.md)
- [Live Trading Guide](LIVE_TRADING_GUIDE.md)