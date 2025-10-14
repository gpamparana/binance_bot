# Data Pipeline Usage Guide

Complete guide for building Parquet catalogs for NautilusTrader backtests.

## Table of Contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [Data Sources](#data-sources)
5. [Pipeline Execution](#pipeline-execution)
6. [Validation](#validation)
7. [Troubleshooting](#troubleshooting)
8. [Best Practices](#best-practices)

## Overview

The data pipeline system converts market data from various sources into NautilusTrader's ParquetDataCatalog format for backtesting.

**Supported Data Types**:
- Trade ticks (aggTrade from exchanges)
- Mark prices (1-second updates)
- Funding rates (perpetual futures)

**Supported Sources**:
- **Tardis.dev**: Historical data API (recommended)
- **CSV**: Custom CSV files
- **WebSocket**: Captured JSONL files

## Installation

### 1. Install Dependencies

```bash
# Using uv (recommended)
uv pip install tardis-client aiohttp

# Or using pip
pip install tardis-client aiohttp
```

### 2. Set Up Tardis.dev (Optional)

Sign up for a free account at [tardis.dev](https://tardis.dev/) and get your API key.

```bash
export TARDIS_API_KEY="your_api_key_here"
```

## Quick Start

### Generate Sample Data

The fastest way to get started is to generate sample data:

```bash
# Set Tardis API key
export TARDIS_API_KEY="your_key"

# Generate 3-day BTCUSDT sample
python -m naut_hedgegrid.data.scripts.generate_sample_data
```

This creates:
- `./data/catalog/BTCUSDT-PERP.BINANCE/trade_tick.parquet`
- `./data/catalog/BTCUSDT-PERP.BINANCE/mark_price.parquet`
- `./data/catalog/BTCUSDT-PERP.BINANCE/funding_rate.parquet`
- `./data/catalog/instruments.parquet`

### Run Your First Backtest

```bash
python -m naut_hedgegrid.runners.run_backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml
```

## Data Sources

### Tardis.dev (Recommended)

**Pros**:
- High-quality historical data
- Multiple exchanges supported
- Automatic caching
- Native WebSocket format

**Cons**:
- Requires API key
- Free tier has monthly limits
- Network dependency

**Usage**:

```bash
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source tardis \
    --symbol BTCUSDT \
    --start 2024-01-01 \
    --end 2024-01-07 \
    --output ./data/catalog \
    --data-types trades,mark,funding
```

**Configuration**:

```json
{
    "api_key": "your_key",
    "exchange": "binance-futures",
    "cache_dir": "./tardis_cache"
}
```

### CSV Files

**Pros**:
- Simple format
- Easy to generate from any source
- No API dependencies

**Cons**:
- Manual preprocessing required
- Less automated

**Usage**:

```bash
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source csv \
    --symbol BTCUSDT \
    --start 2024-01-01 \
    --end 2024-01-07 \
    --output ./data/catalog \
    --config examples/data_configs/csv_source_config.json
```

**CSV Format Example**:

```csv
time,symbol,price,amount,side,id
2024-01-01 00:00:00,BTCUSDT,42500.50,0.025,BUY,1001
2024-01-01 00:00:01,BTCUSDT,42501.00,0.100,SELL,1002
```

**Column Mapping**:

The pipeline auto-detects common column names:
- **timestamp**: time, datetime, date, timestamp
- **price**: price, close, last
- **size**: size, volume, quantity, amount, qty
- **aggressor_side**: side, aggressor_side, taker_side
- **trade_id**: trade_id, id, tid

You can override with custom mapping:

```json
{
  "files": {
    "trades": {
      "file_path": "trades.csv",
      "columns": {
        "timestamp": "my_time_column",
        "price": "my_price_column",
        "size": "my_size_column",
        "aggressor_side": "my_side_column",
        "trade_id": "my_id_column"
      }
    }
  }
}
```

### WebSocket JSONL

**Pros**:
- Replay actual exchange messages
- Preserves all metadata
- Useful for debugging

**Cons**:
- Requires WebSocket capture setup
- Large file sizes

**Usage**:

```bash
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source websocket \
    --symbol BTCUSDT \
    --start 2024-01-01 \
    --end 2024-01-07 \
    --output ./data/catalog \
    --config examples/data_configs/websocket_source_config.json
```

**JSONL Format** (Binance Futures):

```json
{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":1640995200000,"s":"BTCUSDT","a":123,"p":"47000.00","q":"0.001","T":1640995200000,"m":true}}
```

## Pipeline Execution

### Command-Line Interface

**Basic Execution**:

```bash
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source tardis \
    --symbol ETHUSDT \
    --start 2024-02-01 \
    --end 2024-02-08 \
    --output ./data/catalog
```

**Full Options**:

```bash
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source tardis \
    --symbol ETHUSDT \
    --start 2024-02-01 \
    --end 2024-02-08 \
    --output ./data/catalog \
    --data-types trades,mark,funding \
    --exchange BINANCE \
    --config my_config.json
```

### Programmatic API

```python
import asyncio
from naut_hedgegrid.data.pipelines.replay_to_parquet import run_pipeline

async def main():
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

asyncio.run(main())
```

### Batch Processing

Process multiple symbols:

```bash
#!/bin/bash
for symbol in BTCUSDT ETHUSDT SOLUSDT; do
    echo "Processing $symbol..."
    python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
        --source tardis \
        --symbol $symbol \
        --start 2024-01-01 \
        --end 2024-01-07 \
        --output ./data/catalog \
        --data-types trades,mark,funding
done
```

## Validation

### Verify Catalog Structure

```bash
tree data/catalog/
```

Expected output:

```
data/catalog/
├── BTCUSDT-PERP.BINANCE/
│   ├── trade_tick.parquet
│   ├── mark_price.parquet
│   └── funding_rate.parquet
└── instruments.parquet
```

### Check Data Quality

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog
import pandas as pd

# Load catalog
catalog = ParquetDataCatalog("./data/catalog")

# Check instruments
instruments = catalog.instruments()
print(f"Instruments: {len(instruments)}")
for inst in instruments:
    print(f"  {inst.id}")

# Check trade ticks
trades = catalog.trade_ticks(
    instrument_ids=["BTCUSDT-PERP.BINANCE"],
    start=pd.Timestamp("2024-01-01", tz="UTC"),
    end=pd.Timestamp("2024-01-04", tz="UTC")
)
print(f"Trade ticks: {len(trades):,}")

# Check custom data
mark_df = pd.read_parquet("./data/catalog/BTCUSDT-PERP.BINANCE/mark_price.parquet")
print(f"Mark prices: {len(mark_df):,}")

funding_df = pd.read_parquet("./data/catalog/BTCUSDT-PERP.BINANCE/funding_rate.parquet")
print(f"Funding rates: {len(funding_df):,}")
```

### Validate Data Integrity

```python
# Check for gaps in trade data
trades_df = pd.DataFrame([{
    'timestamp': t.ts_event,
    'price': float(t.price),
    'size': float(t.size)
} for t in trades])

trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'], unit='ns')
trades_df = trades_df.set_index('timestamp')

# Check for large time gaps (>1 minute)
gaps = trades_df.index.to_series().diff()
large_gaps = gaps[gaps > pd.Timedelta(minutes=1)]
print(f"Large gaps: {len(large_gaps)}")
if len(large_gaps) > 0:
    print(large_gaps)
```

## Troubleshooting

### Issue: "Tardis API key required"

**Solution**:
```bash
export TARDIS_API_KEY="your_key_here"
```

Or pass in config:
```json
{"api_key": "your_key_here"}
```

### Issue: "No data fetched"

**Causes**:
1. Symbol format incorrect (use uppercase: "BTCUSDT")
2. Date range outside data availability
3. API rate limits exceeded

**Debug**:
```bash
# Check symbol format
python -c "print('btcusdt'.upper())"  # Should be BTCUSDT

# Verify date range
# Binance Futures launched in 2019, check exchange history

# Check API limits
# Free Tardis tier: 100GB/month
```

### Issue: "Missing required columns"

**Cause**: CSV columns don't match expected schema

**Solution**:
```json
{
  "files": {
    "trades": {
      "file_path": "trades.csv",
      "columns": {
        "timestamp": "actual_timestamp_column",
        "price": "actual_price_column",
        "size": "actual_size_column",
        "aggressor_side": "actual_side_column",
        "trade_id": "actual_id_column"
      }
    }
  }
}
```

### Issue: "Timestamp parsing failed"

**Cause**: Timestamp format not recognized

**Solution**: Specify format explicitly
```json
{
  "timestamp_format": "%Y-%m-%d %H:%M:%S.%f"
}
```

Common formats:
- ISO 8601: `"%Y-%m-%dT%H:%M:%S"`
- US format: `"%m/%d/%Y %H:%M:%S"`
- Unix timestamp: auto-detected (numeric)

### Issue: "Memory error"

**Cause**: Processing too much data at once

**Solution**: Process in smaller date ranges
```bash
# Instead of 30 days at once
for day in {1..30}; do
    python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
        --start 2024-01-$(printf "%02d" $day) \
        --end 2024-01-$(printf "%02d" $((day+1))) \
        ...
done
```

## Best Practices

### 1. Data Organization

```
data/
├── raw/                    # Raw source files
│   ├── csv/
│   └── websocket/
├── catalog/                # Processed Nautilus catalog
│   ├── BTCUSDT-PERP.BINANCE/
│   └── ETHUSDT-PERP.BINANCE/
└── cache/                  # Tardis cache
    └── binance-futures/
```

### 2. Incremental Updates

Process new data without reprocessing old data:

```bash
# Initial load
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --start 2024-01-01 --end 2024-01-07 ...

# Weekly update
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --start 2024-01-07 --end 2024-01-14 ...
```

### 3. Data Quality Checks

Always validate after ingestion:

```python
# Check for duplicates
trade_ids = [t.trade_id for t in trades]
assert len(trade_ids) == len(set(trade_ids)), "Duplicate trade IDs found"

# Check chronological order
timestamps = [t.ts_event for t in trades]
assert timestamps == sorted(timestamps), "Timestamps not sorted"

# Check for zeros
prices = [float(t.price) for t in trades]
assert all(p > 0 for p in prices), "Invalid prices found"
```

### 4. Backup Strategy

```bash
# Backup catalog before updates
tar -czf catalog_backup_$(date +%Y%m%d).tar.gz data/catalog/

# Restore if needed
tar -xzf catalog_backup_20240101.tar.gz
```

### 5. Logging

Enable detailed logging for debugging:

```python
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pipeline.log'),
        logging.StreamHandler()
    ]
)
```

### 6. Resource Management

Monitor resource usage:

```bash
# Check disk space
du -sh data/catalog/

# Monitor memory during execution
python -m memory_profiler pipeline_script.py

# Limit memory (Linux)
ulimit -v 4000000  # 4GB limit
```

## Performance Tips

### Tardis.dev Optimization

1. **Enable caching**: Avoid re-downloading
   ```python
   {"cache_dir": "./tardis_cache"}
   ```

2. **Filter channels**: Only fetch needed data
   ```python
   data_types=["trades"]  # Skip mark/funding if not needed
   ```

3. **Batch by date**: Process multiple days in one call
   ```bash
   --start 2024-01-01 --end 2024-01-07  # 7 days at once
   ```

### CSV Optimization

1. **Use compression**: `.csv.gz` instead of `.csv`
2. **Pre-sort data**: Sort by timestamp before ingestion
3. **Remove unnecessary columns**: Keep only required fields

### Storage Optimization

1. **Parquet compression**: Default SNAPPY is good balance
2. **Partition by date**: Automatically handled
3. **Clean old data**: Remove unneeded catalogs

## Next Steps

1. **Generate sample data**: Run `generate_sample_data.py`
2. **Validate catalog**: Check with ParquetDataCatalog
3. **Run backtest**: Use with `run_backtest.py`
4. **Iterate**: Add more symbols, date ranges, exchanges

## Support

- NautilusTrader docs: https://nautilustrader.io/
- Tardis.dev docs: https://docs.tardis.dev/
- Repository issues: [GitHub issues](https://github.com/...)
