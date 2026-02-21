# naut-hedgegrid

**Hedge-mode grid trading system built on NautilusTrader**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![NautilusTrader](https://img.shields.io/badge/nautilus-1.220.0+-green.svg)](https://nautilustrader.io/)
[![uv](https://img.shields.io/badge/uv-package%20manager-purple.svg)](https://github.com/astral-sh/uv)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](docker/README.md)

An algorithmic trading system implementing hedge-mode grid strategies for perpetual futures. Built on the NautilusTrader event-driven framework with active runtime risk controls, live funding integration, comprehensive testing, monitoring, and operational tooling.

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Safety Notes](#safety-notes)
- [Hedge Mode Checklist](#hedge-mode-checklist)
- [Trading Modes](#trading-modes)
- [Project Structure](#project-structure)
- [Development](#development)
- [Data Pipeline](#data-pipeline)
- [Docker Deployment](#docker-deployment)
- [Examples](#examples)
- [License](#license)

## Quick Start

Get running in under 5 minutes (assuming you have Python 3.11+ installed):

```bash
# 1. Install dependencies with uv
uv sync --all-extras

# 2. Verify data pipeline (optional)
uv run python examples/verify_data_pipeline.py

# 3. Run backtest with default config
uv run python -m naut_hedgegrid backtest \
  --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
  --strategy-config configs/strategies/hedge_grid_v1.yaml

# 4. View results
ls -la artifacts/
```

Or use Docker:

```bash
# Build image
docker compose build

# Run backtest
docker compose run --rm backtest

# View results
ls -la artifacts/
```

For interactive tutorial, see [examples/quick_backtest.ipynb](examples/quick_backtest.ipynb).

## Architecture

### System Overview

```
                        ┌──────────────────────────────────┐
                        │   Operational Layer (ops/)       │
                        │  - Prometheus Metrics            │
                        │  - Alert System (Telegram/Email) │
                        │  - Kill Switch                   │
                        └──────────┬───────────────────────┘
                                   │
                        ┌──────────▼───────────────────────┐
                        │   UI/API Layer (ui/)             │
                        │  - FastAPI REST API              │
                        │  - Status/Control/Metrics        │
                        └──────────┬───────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────┐
│                         HedgeGridV1 Strategy                         │
│           (Entry point: implements NautilusTrader lifecycle)         │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Risk Controls Gate                                │
│        (drawdown limit, circuit breaker, position validation)        │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ (pass)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Component Orchestration                          │
│                       (on_bar execution)                             │
└──────────────────────────────────────────────────────────────────────┘
         │                │               │              │
         ▼                ▼               ▼              ▼
    ┌────────┐      ┌──────────┐    ┌─────────┐   ┌──────────┐
    │ Regime │      │   Grid   │    │ Funding │   │  Order   │
    │Detector│─────▶│  Engine  │───▶│  Guard  │──▶│   Diff   │
    └────────┘      └──────────┘    └─────────┘   └──────────┘
         │                │               │              │
         │                ▼               │              │
         │          ┌──────────┐          │              │
         │          │Placement │          │              │
         └─────────▶│  Policy  │──────────┘              │
                    └──────────┘                         │
                         │                               │
                         └───────────────────────────────┘
                                       │
                                       ▼
                              ┌─────────────────┐
                              │Execute Operations│
                              │(submit/cancel)   │
                              └────────┬─────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │   Exchange Layer            │
                        │  - PrecisionGuard           │
                        │  - Binance Adapter          │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │   Data Pipeline (data/)     │
                        │  - Binance/Tardis Sources   │
                        │  - Parquet Normalization    │
                        │  - Historical Warmup        │
                        └─────────────────────────────┘
```

### Component Flow

**0. Risk Controls Gate** → Checks drawdown limits and circuit breaker status. Halts trading if thresholds are breached. Position size validated before every order submission.

**1. Regime Detection** → Determines market state (trending/ranging) using EMA crossovers, ADX trend strength, ATR volatility.

**2. Grid Engine** → Computes ladder of entry prices based on regime and configuration.

**3. Placement Policy** → Shapes ladder quantities per regime (throttles counter-trend side).

**4. Funding Guard** → Adjusts orders based on live funding rate data (received via `BinanceFuturesMarkPriceUpdate` subscription).

**5. Throttle** → Scales ladder quantities by operational throttle factor (controllable via API).

**6. Order Diff** → Compares desired vs actual orders, generates minimal modification set.

**7. Execution** → Submits new orders, cancels stale ones, attaches TP/SL on fills.

### Hedge Mode

This system uses **OmsType.HEDGING**, allowing simultaneous LONG and SHORT positions on the same instrument:

- **Position IDs**: `BINANCE-BTCUSDT-PERP.BINANCE-LONG` and `.BINANCE-SHORT`
- **TP/SL Attachment**: Reduce-only orders attached on fill events
- **Risk Management**: Independent sizing and lifecycle for each side
- **Exchange Support**: Binance Futures (more venues coming)

## Safety Notes

### ⚠️ CRITICAL WARNINGS

**1. LIVE TRADING USES REAL MONEY**
- Always test in paper mode first (minimum 1 week)
- Start with minimal capital (1-5% of total)
- Use stop-losses and position limits
- Monitor 24/7 or use kill switches

**2. HEDGE MODE REQUIREMENTS**
- Exchange account must be set to **Hedge Mode** (not One-Way Mode)
- Verify hedge mode is enabled before going live
- Test with small positions first
- See [Hedge Mode Checklist](#hedge-mode-checklist) below

**3. FUNDING RATES**
- Perpetual futures charge funding every 8 hours
- Negative funding can erode profits on grid strategies
- FundingGuard component mitigates but doesn't eliminate risk
- Monitor funding rates continuously

**4. API KEY SECURITY**
- **NEVER** commit API keys to git
- Use environment variables or secrets management
- Restrict API keys to trading-only permissions (no withdrawal)
- Rotate keys regularly

**5. RISK CONTROLS**
- Drawdown protection, circuit breaker, and position size validation are active in the trading loop
- Configure thresholds in `risk_management` section of strategy config
- Kill switch is auto-started by OperationsManager when `--enable-ops` is passed
- Always verify risk controls trigger correctly before live deployment

**6. SYSTEM FAILURES**
- Network outages can prevent order cancellation
- Use exchange-side stop-losses as backup
- Grid orders cache is hydrated from exchange on restart to prevent duplicates
- Test failover scenarios

**7. BACKTEST LIMITATIONS**
- Historical performance ≠ future results
- Slippage and fees may differ from backtest
- Market conditions change continuously
- Validate with paper trading before live

### Pre-Live Checklist

Before running live trading, complete ALL items:

- [ ] Backtested strategy on ≥6 months historical data
- [ ] Ran paper trading for ≥1 week without errors
- [ ] Verified hedge mode enabled on exchange account
- [ ] Tested position flattening commands
- [ ] Verified circuit breaker and drawdown protection trigger correctly under synthetic fault/load
- [ ] Verified restart with open orders doesn't create duplicate ladder placements
- [ ] Verified funding guard activates with real funding feed
- [ ] Configured Prometheus alerting for PnL/position thresholds
- [ ] Confirmed kill switch starts via OperationsManager (`--enable-ops`)
- [ ] Documented emergency procedures
- [ ] Tested with minimum position size
- [ ] Verified API rate limits won't be exceeded
- [ ] Set up 24/7 monitoring or on-call rotation

## Hedge Mode Checklist

**Verify hedge mode is properly configured before trading:**

### On Binance Futures

1. **Check Account Position Mode**
   ```bash
   curl -X GET 'https://fapi.binance.com/fapi/v1/positionSide/dual' \
     -H 'X-MBX-APIKEY: your_api_key'

   # Response should be: {"dualSidePosition": true}
   ```

2. **Enable Hedge Mode (if needed)**
   ```bash
   curl -X POST 'https://fapi.binance.com/fapi/v1/positionSide/dual?dualSidePosition=true' \
     -H 'X-MBX-APIKEY: your_api_key' \
     -H 'X-MBX-SIGNATURE: signature'
   ```

3. **Verify in Binance UI**
   - Go to Binance Futures → Settings → Position Mode
   - Should show "Hedge Mode" (not "One-Way Mode")

### In Your Code

4. **Check Strategy Config** (`configs/strategies/hedge_grid_v1.yaml`)
   ```yaml
   oms_type: HEDGING  # Must be HEDGING, not NETTING
   ```

5. **Verify Position IDs in Logs**
   - LONG positions: `{symbol}.{venue}-LONG`
   - SHORT positions: `{symbol}.{venue}-SHORT`
   - Example: `BINANCE-BTCUSDT-PERP.BINANCE-LONG`

6. **Test with Paper Trading**
   ```bash
   # Start paper trading
   uv run python -m naut_hedgegrid paper --enable-ops

   # In another terminal, check positions
   curl http://localhost:8080/api/v1/positions

   # Should see separate LONG and SHORT positions
   ```

### Common Issues

- **"Position mode mismatch"**: Account is in One-Way mode, not Hedge mode
- **"PositionId not found"**: Check suffix is `-LONG` or `-SHORT`
- **"Reduce-only order rejected"**: TP/SL orders require open position first

## Trading Modes

### 1. Backtest

Run historical simulations with Nautilus BacktestEngine:

```bash
uv run python -m naut_hedgegrid backtest \
  --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
  --strategy-config configs/strategies/hedge_grid_v1.yaml \
  --output-dir artifacts/
```

**Output**: Performance metrics, order history, position reports in `artifacts/`.

### 2. Paper Trading

Simulated execution with live market data (no real orders). Defaults to testnet:

```bash
uv run python -m naut_hedgegrid paper \
  --strategy-config configs/strategies/hedge_grid_v1.yaml \
  --venue-config configs/venues/binance_futures_testnet.yaml \
  --enable-ops \
  --prometheus-port 9090 \
  --api-port 8080
```

**Features**:
- Real-time Binance WebSocket data
- Simulated order fills
- Prometheus metrics on `:9090/metrics`
- FastAPI control on `:8080/docs`

**Operational Commands**:
```bash
# Check status
curl http://localhost:8080/api/v1/status

# Flatten LONG positions
curl -X POST http://localhost:8080/api/v1/flatten/long

# Flatten SHORT positions
curl -X POST http://localhost:8080/api/v1/flatten/short

# View metrics
curl http://localhost:9090/metrics
```

### 3. Live Trading

**⚠️ REAL EXECUTION WITH REAL MONEY ⚠️**

```bash
# Set API credentials
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret

# Verify hedge mode is enabled on exchange!
# See Hedge Mode Checklist above

# Start live trading
uv run python -m naut_hedgegrid live \
  --strategy-config configs/strategies/hedge_grid_v1.yaml \
  --venue-config configs/venues/binance_futures.yaml \
  --enable-ops \
  --prometheus-port 9091 \
  --api-port 8081
```

**Pre-flight checks**:
1. Verify hedge mode enabled (see checklist above)
2. Test with minimum position size first
3. Monitor continuously for first 24 hours
4. Have kill switch ready

## Project Structure

```
naut-hedgegrid/
├── naut_hedgegrid/              # Main package (NOT src/naut_hedgegrid/)
│   ├── strategies/              # Complete strategy implementations
│   │   └── hedge_grid_v1/       # HedgeGridV1 strategy module
│   │       ├── strategy.py      # Main strategy class (entry point)
│   │       └── config.py        # Strategy-specific config models
│   ├── strategy/                # Reusable strategy components
│   │   ├── detector.py          # RegimeDetector (volatility, volume, trend)
│   │   ├── grid.py              # GridEngine (ladder generation)
│   │   ├── policy.py            # PlacementPolicy (regime-based shaping)
│   │   ├── funding_guard.py     # FundingGuard (funding rate filters)
│   │   └── order_sync.py        # OrderDiff (minimal order updates)
│   ├── domain/                  # Core business types
│   │   └── types.py             # Side, Regime, Rung, Ladder, LiveOrder
│   ├── exchange/                # Exchange adapters
│   │   └── precision.py         # PrecisionGuard (tick/lot rounding)
│   ├── adapters/                # Exchange-specific adapters
│   │   └── binance_testnet_patch.py  # Binance testnet compatibility
│   ├── config/                  # Configuration schemas
│   │   ├── base.py              # BaseConfig, ConfigLoader (Pydantic v2)
│   │   ├── backtest.py          # BacktestConfig (time range, data sources)
│   │   ├── strategy.py          # HedgeGridConfig (grid params, risk limits)
│   │   ├── venue.py             # VenueConfig (exchange connection settings)
│   │   └── operations.py        # OperationsConfig (API, Prometheus, alerts)
│   ├── runners/                 # CLI execution runners
│   │   ├── base_runner.py       # BaseRunner (shared TradingNode setup)
│   │   ├── run_backtest.py      # BacktestRunner (Nautilus BacktestEngine)
│   │   ├── run_paper.py         # PaperRunner (Nautilus sandbox)
│   │   └── run_live.py          # LiveRunner (REAL MONEY)
│   ├── ops/                     # Operational monitoring and control
│   │   ├── manager.py           # OperationsManager (wires Prometheus, API, KillSwitch)
│   │   ├── prometheus.py        # Prometheus metrics exporter
│   │   ├── alerts.py            # Alert system (Telegram, email, webhook)
│   │   └── kill_switch.py       # Emergency position flattening
│   ├── ui/                      # User interfaces
│   │   └── api.py               # FastAPI REST API (status, control, metrics)
│   ├── data/                    # Data pipeline
│   │   ├── sources/             # Data source connectors
│   │   │   ├── binance_source.py     # Binance REST/WebSocket
│   │   │   ├── tardis_source.py      # Tardis historical data
│   │   │   ├── csv_source.py         # CSV import
│   │   │   └── websocket_source.py   # Generic WebSocket
│   │   ├── pipelines/           # Data transformation pipelines
│   │   │   ├── normalizer.py         # Data normalization
│   │   │   └── replay_to_parquet.py  # Convert replay to Parquet
│   │   ├── scripts/             # Data generation utilities
│   │   │   └── generate_sample_data.py
│   │   └── schemas.py           # Parquet schema definitions
│   ├── warmup/                  # Historical data warmup utilities
│   │   └── binance_warmer.py    # Binance data warmup for RegimeDetector
│   ├── metrics/                 # Performance analysis
│   │   └── report.py            # ReportGenerator (32 metrics)
│   ├── utils/                   # Shared utilities
│   │   └── yamlio.py            # YAML I/O helpers
│   ├── cli.py                   # Unified CLI (Typer + Rich)
│   └── __main__.py              # Module entry point
├── configs/                     # YAML configuration files
│   ├── backtest/                # Backtest configs (time ranges, data sources)
│   ├── strategies/              # Strategy configs (grid params, risk settings)
│   └── venues/                  # Venue configs (Binance, more coming)
├── tests/                       # Test suite (645 tests collected)
│   ├── strategy/                # Strategy component tests
│   │   ├── test_grid.py         # GridEngine tests
│   │   ├── test_policy.py       # PlacementPolicy tests
│   │   ├── test_detector.py     # RegimeDetector tests
│   │   ├── test_funding_guard.py
│   │   ├── test_order_sync.py
│   │   └── test_strategy_smoke.py
│   ├── test_parity.py           # Backtest vs paper trading validation
│   └── test_precision.py        # PrecisionGuard tests
├── examples/                    # Runnable examples
│   ├── quick_backtest.ipynb     # Interactive tutorial (Jupyter)
│   ├── kill_switch_integration.py   # Emergency stop implementation
│   └── verify_data_pipeline.py  # Data catalog validation
├── docker/                      # Docker deployment
│   ├── Dockerfile               # Multi-stage build (uv, non-root)
│   └── README.md                # Deployment guide (550+ lines)
├── docs/                        # Documentation
│   ├── QUICKSTART_TRADING.md    # Quick start guide for live trading
│   ├── OPERATIONS.md            # Operational procedures
│   ├── RUNNER_API_REFERENCE.md  # Runner API documentation
│   └── FIX_TODO.md              # Known issues and fixes
├── data/                        # Parquet data catalogs (mount point)
├── artifacts/                   # Backtest results and logs
└── docker-compose.yml           # Service orchestration (backtest, paper, live)
```

## Development

### Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management (5-10x faster than pip):

```bash
# Install dependencies
uv sync --all-extras

# Install pre-commit hooks
uv run pre-commit install
```

### Workflow

```bash
# Format code (ruff)
make format

# Lint code (ruff)
make lint

# Type check (mypy)
make typecheck

# Run tests (pytest)
make test

# Run single test file
uv run pytest tests/strategy/test_grid.py -v

# Run single test function
uv run pytest tests/strategy/test_grid.py::test_build_ladders -v

# Run with coverage
uv run pytest tests/ --cov=naut_hedgegrid --cov-report=html

# Run all checks
make all
```

### Testing Philosophy

- **645 tests collected, 608 passing, 37 skipped** (as of 2026-02-20)
- **Unit tests**: Pure functions (grid, policy, detector) tested in isolation
- **Component tests**: Strategy components (funding_guard, order_sync)
- **Integration tests**: Multi-component orchestration
- **Parity tests**: Backtest vs paper trading consistency
- **Property-based tests**: Hypothesis for edge case discovery

### Code Conventions

- **Formatter**: ruff (line length 100)
- **Linter**: ruff (select = ["E", "F", "I"])
- **Type checker**: mypy (strict mode)
- **Config schemas**: Pydantic v2
- **Testing**: pytest + hypothesis
- **CLI**: Typer + Rich

## Data Pipeline

### Data Format

This system uses **NautilusTrader Parquet catalogs** for historical data:

```
data/
└── catalog/
    ├── trade_ticks/         # Trade ticks (price, size, timestamp)
    ├── quote_ticks/         # Quotes (bid, ask, bid_size, ask_size)
    ├── bars/                # OHLCV bars (mark price, index price)
    └── instruments/         # Instrument definitions
```

### Data Sources

Supported sources:
- **Binance Futures**: WebSocket for live, historical via REST API
- **Tardis**: High-quality historical data (via `tardis-machine`)
- **CSV Import**: Custom data ingestion

### Data Validation

Verify your data pipeline before backtesting:

```bash
uv run python examples/verify_data_pipeline.py
```

This script checks:
- Parquet catalog exists and is readable
- Data covers configured time range
- No gaps in time series
- Instrument definitions are valid

## Docker Deployment

### Quick Start

```bash
# Build image (multi-stage, ~200MB)
docker compose build

# Run backtest
docker compose run --rm backtest

# Start paper trading (background)
docker compose --profile paper up -d

# View logs
docker compose logs -f paper

# Stop paper trading
docker compose --profile paper down
```

### Services

- **backtest**: One-shot backtest execution
- **paper**: Long-running paper trading with Prometheus metrics
- **live**: Long-running live trading (REAL MONEY)

### Configuration

All services mount:
- `./data:/app/data` - Parquet data catalogs
- `./artifacts:/app/artifacts` - Backtest reports, logs
- `./configs:/app/configs` - YAML configuration files

See [docker/README.md](docker/README.md) for comprehensive deployment guide (550+ lines).

## Examples

### Interactive Tutorial

[examples/quick_backtest.ipynb](examples/quick_backtest.ipynb) - Jupyter notebook tutorial:
- Load configuration
- Run 1-day backtest
- Display KPIs in Rich table
- Visualize trades with matplotlib
- **Target**: Get newcomers running in <10 minutes

### Kill Switch Integration

[examples/kill_switch_integration.py](examples/kill_switch_integration.py) - Emergency stop implementation:
- Monitor PnL/drawdown thresholds
- Flatten all positions on trigger
- Cancel all orders
- Send alerts (Telegram, email, webhook)

### Data Pipeline Validation

[examples/verify_data_pipeline.py](examples/verify_data_pipeline.py) - Validate data catalog:
- Check Parquet files exist
- Verify time range coverage
- Detect gaps in time series
- Validate instrument definitions

## Configuration

### Example Strategy Config

```yaml
# configs/strategies/hedge_grid_v1.yaml
strategy:
  class_name: "HedgeGridV1"
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 50          # 50 bps between rungs
  grid_levels_long: 10       # 10 rungs below mid
  grid_levels_short: 10      # 10 rungs above mid
  base_qty: 0.01             # Base quantity (BTC)
  qty_scale: 1.1             # Geometric growth factor

exit:
  tp_steps: 2                # Take profit after 2 grid steps
  sl_steps: 5                # Stop loss after 5 grid steps

position:
  max_position_pct: 0.3      # Max 30% of capital per side

risk_management:
  enable_circuit_breaker: true
  max_errors_per_minute: 10
  circuit_breaker_cooldown_seconds: 300
  enable_drawdown_protection: true
  max_drawdown_pct: 10.0     # Pause trading at 10% drawdown
  enable_position_validation: true

# OMS type (CRITICAL - must be HEDGING for hedge mode)
oms_type: HEDGING
```

## License

MIT License - see LICENSE file for details.

---

**Questions or Issues?**

**Documentation:**
- [QUICKSTART_TRADING.md](docs/QUICKSTART_TRADING.md) - Quick start guide for live trading
- [OPERATIONS.md](docs/OPERATIONS.md) - Operational procedures and monitoring
- [RUNNER_API_REFERENCE.md](docs/RUNNER_API_REFERENCE.md) - Runner API documentation
- [docker/README.md](docker/README.md) - Docker deployment guide (550+ lines)
- [CLAUDE.md](CLAUDE.md) - Development guide for contributors

**Examples:**
- [examples/quick_backtest.ipynb](examples/quick_backtest.ipynb) - Interactive tutorial
- [examples/kill_switch_integration.py](examples/kill_switch_integration.py) - Emergency stop
- [examples/verify_data_pipeline.py](examples/verify_data_pipeline.py) - Data validation

**External Resources:**
- NautilusTrader Documentation: https://nautilustrader.io/docs
- Binance Futures API: https://binance-docs.github.io/apidocs/futures/en/

**⚠️ Remember: Always test in paper mode before live trading!**
