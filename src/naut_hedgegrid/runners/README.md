# Backtest Runner

Production-ready backtest infrastructure for NautilusTrader with HedgeGridV1 strategy.

## Overview

The backtest runner orchestrates end-to-end backtests with:

- **Data Loading**: Loads instruments and market data from Parquet catalogs
- **Engine Configuration**: Sets up Nautilus BacktestEngine with venues, balances, and strategies
- **Execution Simulation**: Models realistic latency, fills, and fees
- **Results Extraction**: Captures orders, positions, and account state
- **Metrics Calculation**: Computes performance metrics (PnL, Sharpe, drawdown, etc.)
- **Artifact Management**: Saves configuration, trades, metrics to structured directories
- **Rich CLI**: Beautiful console output with progress bars and summary tables

## Installation

The runner requires the following dependencies (already in pyproject.toml):

```bash
# Core dependencies
nautilus_trader>=1.200.0
pandas>=2.2.0
typer>=0.9.0
rich>=13.0.0
```

## Usage

### Basic Usage

Run a backtest with default configurations:

```bash
python -m naut_hedgegrid.runners.run_backtest
```

### Custom Configurations

Specify custom backtest and strategy configs:

```bash
python -m naut_hedgegrid.runners.run_backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml
```

### Custom Run ID

Provide a custom run identifier:

```bash
python -m naut_hedgegrid.runners.run_backtest \
    --run-id my_backtest_20241013 \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml
```

### Custom Output Directory

Override the output directory from configuration:

```bash
python -m naut_hedgegrid.runners.run_backtest \
    --output-dir ./results/backtests \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml
```

### Help

View all available options:

```bash
python -m naut_hedgegrid.runners.run_backtest --help
```

## Configuration

### Backtest Configuration

The backtest configuration (`configs/backtest/*.yaml`) defines:

- **Time Range**: Start/end times and timezone
- **Data Sources**: Catalog path, instruments, and data types to load
- **Venues**: Venue configs and starting balances
- **Strategies**: Strategy configs to run
- **Execution**: Latency, fill model, and fee simulation
- **Risk**: Max drawdown, daily loss limits
- **Output**: Report directory, save options, log level
- **Metrics**: Performance metrics to calculate

Example:

```yaml
backtest:
  name: btcusdt_mark_trades_funding
  description: BTC perpetual backtest with mark price, trades, and funding rates

time_range:
  start_time: "2024-01-01T00:00:00Z"
  end_time: "2024-12-31T23:59:59Z"
  timezone: UTC

data:
  catalog_path: ./data/catalog
  instruments:
    - instrument_id: BTCUSDT-PERP.BINANCE
      data_types:
        - type: TradeTick
        - type: Bar
        - type: FundingRate

venues:
  - config_path: ./configs/venues/binance_futures.yaml
    starting_balances:
      - currency: USDT
        total: 10000.0
        locked: 0.0

strategies:
  - config_path: ./configs/strategies/hedge_grid_v1.yaml
    enabled: true
```

### Strategy Configuration

The strategy configuration (`configs/strategies/*.yaml`) defines HedgeGridV1 parameters:

- **Grid**: Step size, levels, base quantity, scaling
- **Exit**: Take profit and stop loss steps
- **Rebalance**: Recentering triggers and inventory limits
- **Execution**: Maker-only, retries, delays
- **Funding**: Window and max cost thresholds
- **Regime**: EMA, ADX, ATR parameters for regime detection
- **Position**: Max size, leverage, liquidation buffers
- **Policy**: Placement strategy and counter-trend throttling

Example:

```yaml
strategy:
  name: hedge_grid_v1
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 25.0
  grid_levels_long: 10
  grid_levels_short: 10
  base_qty: 0.001
  qty_scale: 1.1

exit:
  tp_steps: 2
  sl_steps: 8

policy:
  strategy: throttled-counter
  counter_levels: 5
  counter_qty_scale: 0.5
```

## Output Structure

Each backtest run creates a timestamped directory with artifacts:

```
reports/
└── 20241013_120000/
    ├── config.json          # Complete backtest configuration
    ├── summary.json         # High-level summary with metrics
    ├── orders.csv           # All orders with fills and status
    ├── positions.csv        # Position history with PnL
    └── metrics.csv          # Performance metrics table
```

### config.json

Complete backtest configuration in JSON format for reproducibility.

### summary.json

High-level summary including:

```json
{
  "run_id": "20241013_120000",
  "timestamp": "2024-10-13T12:00:00",
  "backtest_name": "btcusdt_mark_trades_funding",
  "time_range": {
    "start": "2024-01-01T00:00:00",
    "end": "2024-12-31T23:59:59"
  },
  "metrics": {
    "total_orders": 1234,
    "total_positions": 567,
    "final_balance": 10523.45,
    "total_realized_pnl": 523.45,
    "fill_rate": 0.89
  },
  "account": {
    "balance_total": 10523.45,
    "balance_free": 9800.00,
    "balance_locked": 723.45
  }
}
```

### orders.csv

Detailed order records:

| client_order_id | venue_order_id | side | quantity | filled_qty | avg_px | status |
|----------------|----------------|------|----------|------------|--------|--------|
| HG1-LONG-01-... | 123456 | BUY | 0.001 | 0.001 | 50000.0 | FILLED |
| HG1-SHORT-02-... | 123457 | SELL | 0.0011 | 0.0011 | 50250.0 | FILLED |

### positions.csv

Position history with PnL:

| position_id | instrument_id | side | quantity | entry_price | realized_pnl | unrealized_pnl |
|------------|---------------|------|----------|-------------|--------------|----------------|
| BTCUSDT-PERP.BINANCE-LONG | BTCUSDT-PERP.BINANCE | LONG | 0.001 | 50000.0 | 0.0 | 5.0 |
| BTCUSDT-PERP.BINANCE-SHORT | BTCUSDT-PERP.BINANCE | SHORT | 0.0011 | 50250.0 | 2.75 | -5.5 |

### metrics.csv

Performance metrics table (single row):

| total_orders | total_positions | final_balance | total_realized_pnl | total_unrealized_pnl | total_pnl | fill_rate |
|-------------|----------------|---------------|-------------------|---------------------|-----------|-----------|
| 1234 | 567 | 10523.45 | 523.45 | -100.0 | 423.45 | 0.89 |

## Console Output

The runner provides rich console output with progress tracking:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Nautilus Backtest Runner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Loading configurations...
✓ Backtest config: configs/backtest/btcusdt_mark_trades_funding.yaml
✓ Strategy config: configs/strategies/hedge_grid_v1.yaml
Run ID: 20241013_120000

✓ Loading catalog from: data/catalog
✓ Loaded instrument: BTCUSDT-PERP.BINANCE
⠋ Loading data...
✓ Loaded 123,456 trade ticks
✓ Loaded 43,200 bars
✓ Loaded 2,160 funding rates
✓ Added venue: BINANCE (oms=HEDGING, type=MARGIN)
✓ Added 168,816 data items to engine
✓ Added strategy: HedgeGridV1-001

Running backtest...
✓ Backtest completed in 12.34s

Extracting results...
Calculating metrics...

Saving artifacts...
✓ Saved config: reports/20241013_120000/config.json
✓ Saved summary: reports/20241013_120000/summary.json
✓ Saved orders: reports/20241013_120000/orders.csv
✓ Saved positions: reports/20241013_120000/positions.csv
✓ Saved metrics: reports/20241013_120000/metrics.csv

✓ Artifacts saved to: reports/20241013_120000

┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Metric                   ┃ Value         ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ Total Orders             │ 1,234         │
│ Total Positions          │ 567           │
│ Final Balance            │ $10,523.45    │
│ Total Realized Pnl       │ $523.45       │
│ Total Unrealized Pnl     │ $-100.00      │
│ Total Pnl                │ $423.45       │
│ Fill Rate                │ 89.00%        │
└──────────────────────────┴───────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Backtest Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Results saved to: reports/20241013_120000
```

## Programmatic Usage

You can also use the BacktestRunner programmatically:

```python
from pathlib import Path
from rich.console import Console

from naut_hedgegrid.config.backtest import BacktestConfigLoader
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.runners import BacktestRunner
from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1Config

# Load configurations
backtest_config = BacktestConfigLoader.load("configs/backtest/my_backtest.yaml")

# Load strategy configs
hedge_grid_cfg = HedgeGridConfigLoader.load("configs/strategies/hedge_grid_v1.yaml")
strategy_config = HedgeGridV1Config(
    instrument_id=hedge_grid_cfg.strategy.instrument_id,
    bar_type=f"{hedge_grid_cfg.strategy.instrument_id}-1-MINUTE-LAST",
    hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
)

# Create runner
console = Console()
runner = BacktestRunner(
    backtest_config=backtest_config,
    strategy_configs=[strategy_config],
    console=console,
)

# Setup and run
catalog = runner.setup_catalog()
engine, data = runner.run(catalog)

# Extract results
results = runner.extract_results(engine)
metrics = runner.calculate_metrics(results)

# Save artifacts
output_path = runner.save_artifacts(results, metrics)

# Print summary
runner.print_summary(metrics)
```

## Error Handling

The runner includes comprehensive error handling:

- **FileNotFoundError**: Config or catalog path not found
- **ValueError**: Invalid configuration or missing instruments
- **ConfigError**: YAML parsing or validation errors
- **Exception**: Unexpected errors during backtest execution

All errors are displayed with helpful messages and proper exit codes.

## Performance Considerations

### Data Loading

- **Lazy Loading**: Data is loaded on-demand from Parquet catalog
- **Progress Tracking**: Progress bars show loading status
- **Memory Efficient**: Streaming data to engine without excessive buffering

### Engine Performance

- **Nautilus Engine**: Uses high-performance Nautilus BacktestEngine
- **Vectorized Operations**: Efficient data processing
- **Minimal Overhead**: Clean execution without unnecessary logging

### Artifact Storage

- **JSON**: Configuration and summary (human-readable, version-controllable)
- **CSV**: Orders, positions, metrics (easy to analyze with pandas/Excel)
- **Structured Directories**: Timestamped runs for easy comparison

## Troubleshooting

### Catalog Not Found

```
Error: Catalog path not found: ./data/catalog
```

**Solution**: Ensure your data catalog exists at the configured path. Check `data.catalog_path` in your backtest config.

### Instrument Not Found

```
Warning: Instrument BTCUSDT-PERP.BINANCE not found in catalog
```

**Solution**: Verify the instrument exists in your catalog and the instrument_id format is correct.

### Missing Policy Section

```
Configuration validation failed
  • Field: policy
    Error: Field required (missing)
```

**Solution**: Add the `policy` section to your strategy config:

```yaml
policy:
  strategy: throttled-counter
  counter_levels: 5
  counter_qty_scale: 0.5
```

### Import Errors

```
ModuleNotFoundError: No module named 'nautilus_trader'
```

**Solution**: Ensure all dependencies are installed:

```bash
pip install -e .
# or
poetry install
```

## Next Steps

After running your backtest:

1. **Analyze Results**: Review the CSV files and metrics
2. **Optimize Parameters**: Adjust strategy config based on performance
3. **Compare Runs**: Use run_id to track and compare different configurations
4. **Live Testing**: Transition validated strategies to paper trading
5. **Production**: Deploy proven strategies to live trading with risk controls

## Related Documentation

- [BacktestConfig Schema](../../config/backtest.py)
- [HedgeGridV1 Strategy](../../strategies/hedge_grid_v1/README.md)
- [Venue Configuration](../../config/venue.py)
- [NautilusTrader Documentation](https://nautilustrader.io)
