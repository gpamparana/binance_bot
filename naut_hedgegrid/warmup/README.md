# Data Warmup Module

This module provides data warmup functionality for live trading strategies, ensuring technical indicators and regime detectors have sufficient historical data before trading begins.

## Components

### BinanceDataWarmer

Fetches historical klines data from Binance API (testnet or production).

**Key Features:**
- Automatic endpoint selection based on venue configuration
- Rate-limited API calls with pagination support
- Converts Binance klines to NautilusTrader Bar objects
- Provides DetectorBar format for regime detectors
- Context manager support for resource cleanup

**Usage:**

```python
from naut_hedgegrid.warmup import BinanceDataWarmer
from naut_hedgegrid.config.venue import VenueConfig

# Load venue configuration
venue_cfg = VenueConfigLoader.load("configs/venues/binance_testnet.yaml")

# Fetch historical bars
with BinanceDataWarmer(venue_cfg) as warmer:
    # For NautilusTrader Bar objects
    bars = warmer.fetch_historical_bars(
        symbol="BTCUSDT",
        bar_type=bar_type,
        num_bars=100
    )

    # For RegimeDetector DetectorBar objects
    detector_bars = warmer.fetch_detector_bars(
        symbol="BTCUSDT",
        num_bars=70,
        interval="1m"
    )
```

## Integration with Strategies

The warmup system integrates seamlessly with the HedgeGridV1 strategy:

1. **Strategy Method**: `HedgeGridV1.warmup_regime_detector(historical_bars)`
   - Accepts list of DetectorBar objects
   - Feeds them to the regime detector
   - Logs warmup progress and final state

2. **Runner Integration**: `BaseRunner._warmup_strategy()`
   - Called automatically after strategy initialization
   - Fetches historical data based on configuration
   - Handles errors gracefully without blocking startup

## Configuration

The warmup system uses existing venue and strategy configurations:

### Venue Config
- `api.testnet`: Determines API endpoint (testnet vs production)
- `api.api_key/api_secret`: Optional for public klines endpoint

### Strategy Config
- `regime.ema_slow`: Determines minimum bars needed (default: 50)
- Additional buffer of 20 bars for ADX/ATR indicators
- Total: 70 bars fetched by default

## Error Handling

The warmup system is designed to be non-blocking:

- **Network failures**: Strategy starts without warmup
- **Invalid symbols**: Logged as warning, trading continues
- **Insufficient data**: Partial warmup is applied
- **API rate limits**: Automatic retry with backoff

## Performance

- **Startup overhead**: 2-5 seconds typical
- **Memory usage**: Minimal (bars processed immediately)
- **API calls**: 1-2 requests for 70 bars
- **Rate limiting**: 200ms delay between requests

## Testing

Run the test suite:

```bash
# Test warmup in isolation
python test_warmup.py

# Test with paper trading
python examples/paper_trade_with_warmup.py
```

## Files

- `__init__.py`: Module exports
- `binance_warmer.py`: BinanceDataWarmer implementation
- `README.md`: This documentation