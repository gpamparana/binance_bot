# Data Pipeline Module

Complete data ingestion system for building NautilusTrader Parquet catalogs for backtests.

## Overview

This module provides production-grade data pipelines for fetching, normalizing, and storing cryptocurrency market data in NautilusTrader's ParquetDataCatalog format.

### Key Features

- **Multiple Data Sources**: Tardis.dev API, CSV files, WebSocket captures
- **Strict Schema Validation**: Pydantic models ensure data quality
- **Automatic Normalization**: Timestamp conversion, timezone handling, deduplication
- **Nautilus Integration**: Direct conversion to TradeTick, MarkPrice, FundingRate
- **Daily Partitioning**: Efficient time-series storage with Parquet
- **CLI Interface**: Rich progress bars and comprehensive error handling

## Architecture

```
data/
├── schemas.py              # Pydantic schemas + Nautilus converters
├── sources/
│   ├── base.py            # Abstract DataSource interface
│   ├── tardis_source.py   # Tardis.dev API integration
│   ├── csv_source.py      # CSV file reader
│   └── websocket_source.py # JSONL WebSocket replay
├── pipelines/
│   ├── normalizer.py      # Schema normalization
│   └── replay_to_parquet.py # Main pipeline orchestrator
└── scripts/
    └── generate_sample_data.py # Sample data generator
```

## Quick Start

### 1. Generate Sample Data (Tardis.dev)

```bash
# Set API key
export TARDIS_API_KEY="your_tardis_api_key"

# Generate 3-day BTCUSDT sample
python -m naut_hedgegrid.data.scripts.generate_sample_data
```

### 2. Manual Pipeline Execution

```bash
# Tardis.dev source
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source tardis \
    --symbol BTCUSDT \
    --start 2024-01-01 \
    --end 2024-01-03 \
    --output ./data/catalog \
    --data-types trades,mark,funding

# CSV source
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source csv \
    --symbol BTCUSDT \
    --start 2024-01-01 \
    --end 2024-01-03 \
    --output ./data/catalog \
    --config csv_config.json

# WebSocket JSONL source
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source websocket \
    --symbol BTCUSDT \
    --start 2024-01-01 \
    --end 2024-01-03 \
    --output ./data/catalog \
    --config ws_config.json
```

## Data Schemas

### TradeSchema

```python
{
    "timestamp": datetime,      # UTC timezone-aware
    "price": float,             # Must be > 0
    "size": float,              # Must be > 0
    "aggressor_side": str,      # "BUY" or "SELL"
    "trade_id": str             # Unique identifier
}
```

### MarkPriceSchema

```python
{
    "timestamp": datetime,      # UTC timezone-aware
    "mark_price": float         # Must be > 0
}
```

### FundingRateSchema

```python
{
    "timestamp": datetime,           # UTC timezone-aware
    "funding_rate": float,           # Can be +/-
    "next_funding_time": datetime?   # Optional
}
```

## Data Sources

### Tardis.dev Source

Historical cryptocurrency data from Tardis.dev's replay API.

**Configuration**:
```python
{
    "api_key": "your_key",              # Or use TARDIS_API_KEY env var
    "exchange": "binance-futures",      # Default
    "cache_dir": "./tardis_cache"       # Optional local cache
}
```

**Supported Channels**:
- `aggTrade` → TradeTick
- `markPrice@1s` → MarkPrice + FundingRate

**Requirements**:
- Tardis.dev API key (free tier available)
- `tardis-client` Python package

### CSV Source

Flexible CSV file reader with auto-detection.

**Configuration Example** (csv_config.json):
```json
{
    "files": {
        "trades": {
            "file_path": "trades.csv",
            "columns": {
                "timestamp": "time",
                "price": "price",
                "size": "volume",
                "aggressor_side": "side",
                "trade_id": "id"
            }
        },
        "mark": {
            "file_path": "mark_prices.csv.gz"
        }
    },
    "base_path": "./data/raw"
}
```

**Features**:
- Auto-column mapping (price/close, size/volume/qty)
- Compressed files (.gz, .bz2)
- Flexible timestamp parsing
- Symbol filtering

### WebSocket Source

Replay captured WebSocket messages from JSONL files.

**Configuration Example** (ws_config.json):
```json
{
    "files": {
        "trades": {"file_path": "btcusdt_trades.jsonl"},
        "mark": {"file_path": "btcusdt_mark.jsonl.gz"}
    },
    "base_path": "./captures"
}
```

**JSONL Format**:
```json
{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":1640995200000,"s":"BTCUSDT","a":123,"p":"47000.00","q":"0.001","f":1,"l":1,"T":1640995200000,"m":true}}
{"stream":"btcusdt@markPrice@1s","data":{"e":"markPriceUpdate","E":1640995201000,"s":"BTCUSDT","p":"47001.50","r":"0.0001","T":1640995201000}}
```

## Normalization Pipeline

The normalizer handles:

1. **Timestamp Conversion**
   - Auto-detect format (ISO, Unix seconds/ms/us/ns)
   - Convert to UTC datetime64[ns]
   - Ensure timezone awareness

2. **Validation**
   - Price/size positivity checks
   - Aggressor side normalization (BUY/SELL)
   - Schema validation via Pydantic

3. **Cleaning**
   - Sort by timestamp (monotonic requirement)
   - Remove duplicates
   - Filter invalid records

4. **Nautilus Conversion**
   - TradeTick with proper AggressorSide enum
   - InstrumentId association
   - Nanosecond timestamp precision

## Output Structure

```
data/catalog/
├── BTCUSDT-PERP.BINANCE/
│   ├── trade_tick.parquet       # Nautilus TradeTick objects
│   ├── mark_price.parquet       # Custom parquet (timestamp, mark_price)
│   └── funding_rate.parquet     # Custom parquet (timestamp, rate, next_funding)
└── instruments.parquet           # CryptoPerpetual definitions
```

## Advanced Usage

### Programmatic API

```python
from naut_hedgegrid.data.pipelines.replay_to_parquet import run_pipeline

await run_pipeline(
    source_type="tardis",
    symbol="ETHUSDT",
    start_date="2024-01-01",
    end_date="2024-01-07",
    output_path="./data/catalog",
    data_types=["trades", "mark", "funding"],
    source_config={
        "exchange": "binance-futures",
        "cache_dir": "./cache"
    },
    exchange="BINANCE"
)
```

### Custom Data Source

Implement `DataSource` abstract base class:

```python
from naut_hedgegrid.data.sources.base import DataSource

class CustomSource(DataSource):
    async def fetch_trades(self, symbol, start, end):
        # Your implementation
        return pd.DataFrame(...)

    async def fetch_mark_prices(self, symbol, start, end):
        # Your implementation
        return pd.DataFrame(...)

    async def fetch_funding_rates(self, symbol, start, end):
        # Your implementation
        return pd.DataFrame(...)
```

## Performance Considerations

### Tardis.dev

- **Rate Limits**: Free tier has monthly data limits
- **Caching**: Enable `cache_dir` to avoid re-downloading
- **Bandwidth**: 1 day of tick data ≈ 100MB-1GB compressed

### Storage

- **TradeTick**: ~100 bytes/tick (parquet compressed)
- **MarkPrice**: ~20 bytes/update
- **FundingRate**: ~30 bytes/update

**Example**: 3 days of BTCUSDT data
- Trades: ~10M ticks = ~1GB parquet
- Mark prices: ~250K updates = ~5MB
- Funding rates: ~250K updates = ~7.5MB

### Memory

- Pipeline processes data in chunks by date
- Peak memory: ~500MB per day of tick data
- Normalize before conversion to reduce allocations

## Error Handling

The pipeline includes comprehensive error handling:

1. **Connection Validation**: Verify source accessibility before fetching
2. **Schema Validation**: Pydantic validation on all data
3. **Graceful Degradation**: Continue on partial failures
4. **Detailed Logging**: Structured logs with context
5. **Progress Tracking**: Rich progress bars with ETA

## Troubleshooting

### "Tardis API key required"
Set environment variable: `export TARDIS_API_KEY="your_key"`

### "No data fetched"
- Check symbol format (uppercase, e.g., "BTCUSDT")
- Verify date range (data availability on exchange)
- Review API rate limits

### "Timestamp parsing failed"
- Check timestamp format in source data
- Ensure timezone information is present
- Try manual format specification in normalizer

### "Missing required columns"
- Verify CSV column names match schema
- Use custom column mapping in config
- Check for null values in required fields

## Integration with Backtest Runner

The backtest runner (`runners/run_backtest.py`) automatically reads from the catalog:

```yaml
# configs/backtest/btcusdt.yaml
data:
  catalog_path: "./data/catalog"
  instruments:
    - instrument_id: "BTCUSDT-PERP.BINANCE"
      data_types:
        - type: "TradeTick"
        - type: "FundingRate"
        - type: "MarkPrice"
```

Custom data (mark prices, funding rates) is stored in separate parquet files and must be loaded manually if needed.

## Development

### Running Tests

```bash
pytest tests/data/
```

### Type Checking

```bash
mypy src/naut_hedgegrid/data/
```

### Linting

```bash
ruff check src/naut_hedgegrid/data/
```

## References

- [NautilusTrader Documentation](https://nautilustrader.io/)
- [Tardis.dev API](https://docs.tardis.dev/)
- [Binance Futures WebSocket Streams](https://binance-docs.github.io/apidocs/futures/en/)

## License

Part of the naut-hedgegrid trading system.
