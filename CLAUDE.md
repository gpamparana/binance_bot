# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

naut-hedgegrid is a hedge-mode grid trading system built on NautilusTrader for Binance futures markets. The system implements adaptive grid trading with regime detection, funding rate management, and automated risk controls.

**Key Technologies:**
- **Trading Engine**: NautilusTrader >= 1.220.0 (event-driven backtesting and live trading)
- **Build System**: uv (fast Python package manager, NOT pip or poetry)
- **Linting**: ruff (replaces black, flake8, isort)
- **Type Checking**: mypy with strict mode
- **Configuration**: Pydantic v2 with YAML loading
- **CLI**: typer with rich console output
- **Data**: Parquet catalogs, pandas, polars, pyarrow
- **Testing**: pytest with hypothesis (property-based testing)
- **Monitoring**: Prometheus metrics, FastAPI control endpoints
- **Alerting**: Multi-channel notifications (Slack, Telegram)
- **Optimization**: Optuna (Bayesian hyperparameter tuning with SQLite persistence)

## Build Commands

```bash
# Setup (first time)
uv sync --all-extras
uv run pre-commit install

# Development workflow
make format          # Format and fix with ruff
make lint            # Check code quality
make typecheck       # Run mypy type checking
make test            # Run test suite
make all             # Run all checks (format, lint, typecheck, test)

# Run single test file
uv run pytest tests/strategy/test_grid.py -v

# Run single test function
uv run pytest tests/strategy/test_grid.py::test_build_ladders -v

# Run with coverage
uv run pytest tests/ --cov=naut_hedgegrid --cov-report=html
```

## CLI Commands

The system provides a unified CLI via `python -m naut_hedgegrid <command>`:

```bash
# ============================================================================
# TRADING COMMANDS
# ============================================================================

# Run backtest
python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml

# Start paper trading (simulated execution, defaults to testnet)
python -m naut_hedgegrid paper \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures_testnet.yaml

# Start paper trading with operational controls
python -m naut_hedgegrid paper \
    --enable-ops \
    --prometheus-port 9090 \
    --api-port 8080

# Start live trading (REAL MONEY)
python -m naut_hedgegrid live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml \
    --enable-ops

# ============================================================================
# OPERATIONAL CONTROL COMMANDS
# ============================================================================

# Flatten all positions (emergency)
python -m naut_hedgegrid flatten

# Flatten only LONG positions
python -m naut_hedgegrid flatten --side LONG

# Flatten only SHORT positions
python -m naut_hedgegrid flatten --side SHORT

# Query running strategy status
python -m naut_hedgegrid status

# Query status as JSON
python -m naut_hedgegrid status --format json

# Query Prometheus metrics
python -m naut_hedgegrid metrics

# Query metrics in raw Prometheus format
python -m naut_hedgegrid metrics --format raw

# ============================================================================
# OPTIMIZATION COMMANDS
# ============================================================================

# Run parameter optimization (100 trials)
python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 100 \
    --study-name my_optimization

# Run optimization with CSV export
python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 200 \
    --export-csv artifacts/optimization_results.csv

# Analyze completed optimization study
python -m naut_hedgegrid.optimization.cli analyze my_optimization \
    --top-n 10 \
    --export-csv results.csv

# Cleanup low-performing trials from database
python -m naut_hedgegrid.optimization.cli cleanup my_optimization \
    --keep-top-n 50
```

**Note:** Use `uv run` prefix for all commands when running in development:
```bash
uv run python -m naut_hedgegrid backtest
```

## Architecture

The system uses a **layered component architecture** with clear separation of concerns:

### 1. Strategy Layer (`naut_hedgegrid/strategies/`)

Complete trading strategy implementations that orchestrate components below.

- **HedgeGridV1** (`hedge_grid_v1/strategy.py`): Main strategy class
  - Subclasses `nautilus_trader.trading.strategy.Strategy`
  - Lifecycle: `on_start()` → `on_bar()` → `on_order_filled()` loop
  - Orchestrates all components in correct order per bar
  - Manages hedge mode positions (LONG/SHORT with position_id suffixes)
  - Attaches TP/SL on fills as reduce-only orders

### 2. Component Layer (`naut_hedgegrid/strategy/`)

Reusable, composable strategy building blocks with pure functional interfaces:

- **RegimeDetector** (`detector.py`): Classifies market regime (UP/DOWN/SIDEWAYS)
  - Uses EMA crossovers, ADX trend strength, ATR volatility
  - Hysteresis prevents regime flip-flop
  - Requires warmup period before trading

- **GridEngine** (`grid.py`): Builds price ladders (grid levels)
  - Pure functional: `build_ladders(mid, cfg, regime) -> list[Ladder]`
  - Geometric quantity scaling per level
  - Separate ladders for LONG (buy below) and SHORT (sell above)

- **PlacementPolicy** (`policy.py`): Shapes ladders based on regime
  - Throttles counter-trend side (reduce levels or quantities)
  - Two strategies: "core-and-scalp" or "throttled-counter"
  - Pure functional transformation

- **FundingGuard** (`funding_guard.py`): Reduces exposure near funding
  - Tracks funding rate costs over time window
  - Adjusts ladder quantities when costs exceed threshold
  - Fed live funding data via `on_data()` handler (subscribes to `BinanceFuturesMarkPriceUpdate`)
  - Stateful (tracks funding history)

- **OrderDiff** (`order_sync.py`): Minimizes order churn
  - Diffs desired ladders vs live orders
  - Generates minimal operations: adds, cancels, replaces
  - Uses price/qty tolerance to avoid unnecessary updates

### 3. Domain Layer (`naut_hedgegrid/domain/`)

Core types and value objects:

- **Side**: Enum for LONG/SHORT position sides
- **Regime**: Enum for UP/DOWN/SIDEWAYS market states
- **Rung**: Single grid level (price, qty, level)
- **Ladder**: Collection of rungs for one side
- **LiveOrder**: Tracking struct for open orders
- **OrderIntent**: Instruction for order operations (create/cancel/replace)

### 4. Exchange Adapter Layer (`naut_hedgegrid/exchange/`)

- **PrecisionGuard** (`precision.py`): Enforces exchange requirements
  - Rounds prices to tick size
  - Rounds quantities to step size
  - Validates minimum notional
  - Filters invalid rungs from ladders

### 5. Configuration Layer (`naut_hedgegrid/config/`)

Pydantic v2 models with YAML loading:

- **HedgeGridConfig** (`strategy.py`): Main strategy configuration
  - Contains: grid, exit, rebalance, execution, funding, regime, position, policy, risk_management
  - **Important**: Risk values are nested under `position.*` and `risk_management.*` (e.g., `cfg.position.max_position_pct`, `cfg.risk_management.max_drawdown_pct`). Do NOT use `getattr()` on the root config object for these.
  - Loaded via `HedgeGridConfigLoader.load(path)`

- **BacktestConfig** (`backtest.py`): Backtest run configuration
  - Time range, data sources, venues, strategies, execution sim, risk controls

- **VenueConfig** (`venue.py`): Exchange connection settings
  - API credentials, hedge mode, leverage, rate limits

### 6. Runner Layer (`naut_hedgegrid/runners/`)

CLI-driven execution:

- **BaseRunner** (`base_runner.py`): Abstract base with common logic
  - Environment validation
  - Nautilus node configuration
  - Strategy warmup orchestration
  - Operations manager integration

- **BacktestRunner** (`run_backtest.py`): Orchestrates backtests
  - Loads data from Parquet catalog
  - Configures Nautilus BacktestEngine
  - Runs simulation and extracts results
  - Saves artifacts (JSON + CSV)

- **PaperRunner** (`run_paper.py`): Paper trading execution
  - Connects to real market data
  - Simulates order fills
  - No real money at risk

- **LiveRunner** (`run_live.py`): Live trading execution
  - Connects to exchange with execution enabled
  - Places real orders with real money
  - Requires API credentials

### 7. Metrics Layer (`naut_hedgegrid/metrics/`)

Performance analysis:

- **PerformanceMetrics** (`report.py`): 32 metrics across 7 categories
  - Returns: total PnL, annualized return, CAGR
  - Risk: Sharpe, Sortino, Calmar, volatility
  - Drawdown: max, average, recovery time
  - Trades: win rate, profit factor, expectancy
  - Execution: fill rate, maker ratio, slippage
  - Funding: paid/received/net
  - Ladder utilization: depth, fill rate

- **ReportGenerator** (`report.py`): Calculates metrics from backtest results

### 8. Data Pipeline Layer (`naut_hedgegrid/data/`)

Complete data ingestion system for building Parquet catalogs:

- **schemas.py**: Pydantic schemas + Nautilus converters
  - TradeSchema → TradeTick
  - MarkPriceSchema → Custom parquet
  - FundingRateSchema → Custom parquet

- **sources/**: Data source adapters
  - `base.py`: Abstract DataSource interface
  - `tardis_source.py`: Tardis.dev API integration
  - `csv_source.py`: CSV file reader with auto-mapping
  - `websocket_source.py`: JSONL WebSocket replay
  - `binance_source.py`: Direct Binance API integration

- **pipelines/**: Data processing
  - `normalizer.py`: Timestamp conversion, validation, cleaning
  - `replay_to_parquet.py`: Main pipeline orchestrator

- **scripts/**: Utilities
  - `generate_sample_data.py`: Sample data generator

**Key Features:**
- Multiple data sources (Tardis.dev, CSV, WebSocket captures)
- Strict schema validation with Pydantic
- Automatic normalization and deduplication
- Daily partitioning for efficient storage
- Rich CLI with progress bars

### 9. Operational Controls Layer (`naut_hedgegrid/ops/`)

Production-grade monitoring and control infrastructure:

- **kill_switch.py**: Automated circuit breakers
  - Max drawdown triggers
  - Position size limits
  - Funding cost thresholds
  - Automatic position flattening

- **alerts.py**: Multi-channel notifications
  - Slack integration
  - Telegram bot support
  - Email alerts (planned)
  - Configurable alert levels

- **prometheus.py**: Metrics export
  - 15 key metrics exposed
  - Position, grid, risk, funding, PnL metrics
  - Standard Prometheus format
  - Integration with Grafana dashboards

**15 Key Metrics:**
- Position: long_inventory_usdt, short_inventory_usdt, net_inventory_usdt
- Grid: active_rungs_long, active_rungs_short, open_orders
- Risk: margin_ratio, maker_ratio
- Funding: funding_rate_current, funding_cost_1h_projected_usdt
- PnL: realized_pnl_usdt, unrealized_pnl_usdt, total_pnl_usdt
- Health: uptime_seconds, last_bar_timestamp

### 10. UI/API Layer (`naut_hedgegrid/ui/`)

FastAPI control endpoints for live operations:

- **api.py**: REST API for operational commands
  - `GET /health`: Health check
  - `GET /status`: Comprehensive status
  - `POST /flatten`: Emergency position closure
  - `POST /set-throttle`: Adjust aggressiveness
  - `GET /ladders`: Grid ladder snapshot
  - `GET /orders`: Open orders list
  - `POST /start`: Start trading (stub)
  - `POST /stop`: Stop trading (stub)

**Authentication:** Optional API key via `X-API-Key` header

### 11. Adapters Layer (`naut_hedgegrid/adapters/`)

Exchange-specific patches and workarounds:

- **binance_testnet_patch.py**: Binance testnet compatibility fixes
  - Instrument provider patches
  - Endpoint corrections
  - Testnet-specific configurations

### 12. Warmup Module (`naut_hedgegrid/warmup/`)

Data warmup for live trading strategies:

- **binance_warmer.py**: BinanceDataWarmer class
  - Fetches historical klines from Binance API
  - Converts to NautilusTrader Bar objects
  - Provides DetectorBar format for regime detector
  - Automatic endpoint selection (testnet vs production)
  - Rate-limited API calls with pagination

**Integration:**
- `BaseRunner._warmup_strategy()`: Automatic warmup after initialization
- `HedgeGridV1.warmup_regime_detector()`: Feeds historical bars to detector
- Default: 70 bars fetched (50 for EMA slow + 20 buffer for ADX/ATR)

**Performance:**
- Startup overhead: 2-5 seconds typical
- Non-blocking: Strategy starts even if warmup fails
- API calls: 1-2 requests for 70 bars

### 13. Utilities Layer (`naut_hedgegrid/utils/`)

Common utilities and helpers:

- Configuration loading utilities
- Logging helpers
- Time and date utilities
- Mathematical helpers

### 14. Optimization Framework (`naut_hedgegrid/optimization/`)

Bayesian hyperparameter optimization using Optuna:

- **StrategyOptimizer** (`optimizer.py`): Main orchestrator
  - Coordinates all optimization components
  - Runs trials with parameter sampling
  - Saves best configs to YAML files
  - Exports results to CSV

- **ParameterSpace** (`param_space.py`): Defines 17 tunable parameters
  - Grid parameters (5): `grid_step_bps`, `grid_levels_long`, `grid_levels_short`, `base_qty`, `qty_scale`
  - Exit parameters (2): `tp_steps`, `sl_steps`
  - Regime parameters (5): `adx_len`, `ema_fast`, `ema_slow`, `atr_len`, `hysteresis_bps`
  - Policy parameters (2): `counter_levels`, `counter_qty_scale`
  - Rebalance parameters (1): `recenter_trigger_bps`
  - Funding parameters (1): `funding_max_cost_bps`
  - Position parameters (1): `max_position_pct`

- **MultiObjectiveFunction** (`objective.py`): Weighted scoring
  - Sharpe ratio (0.35 weight)
  - Profit factor (0.30 weight)
  - Calmar ratio (0.35 weight)
  - Drawdown penalty (-0.20 weight)
  - Adaptive normalization across trials

- **ConstraintsValidator** (`constraints.py`): Hard constraint filtering
  - Minimum Sharpe ratio (default 1.0)
  - Maximum drawdown (default 20%)
  - Minimum trades (default 50)
  - Minimum win rate (default 45%)
  - Minimum profit factor (default 1.1)
  - Minimum Calmar ratio (default 0.5)

- **ParallelBacktestRunner** (`parallel_runner.py`): Concurrent execution
  - Multi-process backtest execution
  - Configurable job count (`n_jobs`)

- **OptimizationResultsDB** (`results_db.py`): SQLite persistence
  - Stores trial parameters and metrics
  - Query best trials
  - Export to CSV
  - Cleanup low-performing trials

**Parameter Bounds (designed for ~$10k account):**
| Parameter | Min | Max | Notes |
|-----------|-----|-----|-------|
| grid_step_bps | 25 | 100 | 0.25%-1.0% spacing |
| grid_levels_long | 5 | 10 | Levels below mid |
| grid_levels_short | 5 | 10 | Levels above mid |
| base_qty | 0.001 | 0.005 | BTC per level (log scale) |
| qty_scale | 1.0 | 1.1 | Geometric growth factor |
| tp_steps | 1 | 10 | Grid steps for TP |
| sl_steps | 3 | 20 | Grid steps for SL |
| adx_len | 7 | 30 | ADX indicator period |
| ema_fast | 5 | 25 | Fast EMA period |
| ema_slow | 20 | 60 | Slow EMA period |

**Optimization Usage:**
```python
from naut_hedgegrid.optimization import (
    StrategyOptimizer,
    ConstraintsValidator,
    MultiObjectiveFunction,
)
from naut_hedgegrid.optimization.constraints import ConstraintThresholds
from naut_hedgegrid.optimization.objective import ObjectiveWeights

# Custom constraint thresholds
constraints = ConstraintThresholds(
    min_sharpe_ratio=0.5,
    max_drawdown_pct=25.0,
    min_trades=30,
)

# Initialize optimizer
optimizer = StrategyOptimizer(
    backtest_config_path="configs/backtest/btcusdt.yaml",
    base_strategy_config_path="configs/strategies/hedge_grid_v1.yaml",
    n_trials=200,
    n_jobs=4,  # Parallel execution
    study_name="my_optimization",
    constraint_thresholds=constraints,
)

# Run optimization
study = optimizer.optimize()

# Best parameters saved to: configs/strategies/my_optimization_best.yaml
print(f"Best score: {study.best_value:.4f}")
print(f"Best params: {study.best_trial.params}")
```

## Configuration System

All configuration is **code-as-config** using Pydantic v2 models with YAML files.

### Strategy Configuration Pattern

1. Define Pydantic model in `naut_hedgegrid/config/`
2. Create loader class inheriting from `BaseYamlConfigLoader`
3. Store YAML configs in `configs/` directory
4. Load with: `ConfigLoader.load(path)`

Example:
```python
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader

config = HedgeGridConfigLoader.load("configs/strategies/hedge_grid_v1.yaml")
```

### Environment Variables

Use `${ENV_VAR}` syntax in YAML files for secrets:
```yaml
api:
  api_key: ${BINANCE_API_KEY}
  api_secret: ${BINANCE_API_SECRET}
```

**Required Environment Variables:**
- `BINANCE_API_KEY`: Binance API key (for paper/live trading)
- `BINANCE_API_SECRET`: Binance API secret (for paper/live trading)
- `TARDIS_API_KEY`: Tardis.dev API key (optional, for data fetching)

**Note:** Binance requires API credentials even for paper trading to load instrument definitions (metadata). No real orders are placed in paper trading mode.

## Component Orchestration Pattern

The HedgeGridV1 strategy orchestrates components in this **exact order** each bar:

```python
def on_bar(self, bar: Bar):
    # 0. Risk controls gate (early exit if unsafe)
    self._check_drawdown_limit()
    if self._pause_trading or self._circuit_breaker_active:
        return  # Do not trade when risk limits breached

    # 1. Calculate mid price
    mid = float(bar.close)

    # 2. Update regime detector
    self._regime_detector.update_from_bar(detector_bar)
    regime = self._regime_detector.current()

    # 3. Check if grid recentering needed
    if GridEngine.recenter_needed(mid, self._grid_center, cfg):
        self._grid_center = mid

    # 4. Build ladders (pure functional)
    ladders = GridEngine.build_ladders(mid, cfg, regime)

    # 5. Apply placement policy (shape by regime)
    ladders = PlacementPolicy.shape_ladders(ladders, regime, cfg)

    # 6. Apply funding guard (reduce near funding)
    ladders = self._funding_guard.adjust_ladders(ladders, now)

    # 7. Apply throttle (scale quantities by operational throttle factor)
    if self._throttle < 1.0:
        ladders = [self._apply_throttle(ladder) for ladder in ladders]

    # 8. Apply precision guard (enforce exchange rules)
    # Note: Precision guard is applied inside OrderDiff

    # 9. Generate diff vs live orders
    diff_result = self._order_diff.diff(ladders, live_orders)

    # 10. Execute diff operations
    self._execute_diff(diff_result)  # cancels → replaces → adds
```

**This order is critical**: Risk checks must run first to gate trading. Regime detection must happen before policy shaping. Funding adjustments and throttle must happen before diffing.

## Runtime Risk Controls

The strategy has three active risk enforcement mechanisms, all wired into the trading hot path:

1. **Drawdown Protection** (`_check_drawdown_limit`): Called at the start of every `on_bar()`. Tracks peak balance and pauses trading if unrealized drawdown exceeds `risk_management.max_drawdown_pct`. Controlled by `risk_management.enable_drawdown_protection`.

2. **Circuit Breaker** (`_check_circuit_breaker`): Triggered from `on_order_rejected()` and `on_order_denied()`. Tracks error rate per minute and activates cooldown if errors exceed `risk_management.max_errors_per_minute`. Cooldown duration set by `risk_management.circuit_breaker_cooldown_seconds`. Controlled by `risk_management.enable_circuit_breaker`.

3. **Position Size Validation** (`_validate_order_size`): Called in `_execute_add()` before every `submit_order()`. Rejects orders that would push position beyond `position.max_position_pct` of account balance. Controlled by `risk_management.enable_position_validation`.

**Config access pattern** (correct):
```python
# Nested access via Pydantic model
max_dd = self._hedge_grid_config.risk_management.max_drawdown_pct
max_pos = self._hedge_grid_config.position.max_position_pct

# WRONG - do not use getattr on root:
# getattr(self._hedge_grid_config, "max_drawdown_pct", 20.0)  # BUG: always returns default
```

**Startup Reconciliation**: On `on_start()`, `_hydrate_grid_orders_cache()` queries existing open orders from the exchange cache and populates `_grid_orders_cache` to prevent duplicate ladder placements after restarts.

## Hedge Mode and Position Management

Binance futures hedge mode allows separate LONG and SHORT positions on the same instrument.

**Position ID Pattern:**
```python
# For LONG side orders
position_id = PositionId(f"{instrument_id}-LONG")

# For SHORT side orders
position_id = PositionId(f"{instrument_id}-SHORT")

# Example: "BTCUSDT-PERP.BINANCE-LONG"
```

**OMS Type:**
```python
# In configs:
oms_type: OmsType.HEDGING  # NOT NETTING

# This enables separate long/short positions
```

**TP/SL Attachment on Fills:**

When a grid order fills, the strategy automatically attaches:
1. **Take Profit**: Limit order (reduce-only) at profit target
2. **Stop Loss**: Stop-market order (reduce-only) at stop loss

Both use **opposite side** from the fill (SELL to close LONG, BUY to close SHORT) and maintain the same position_id suffix.

## Data Flow and Type Conversions

Components use domain types, Nautilus uses Nautilus types. Conversions happen at strategy boundaries:

```python
# Nautilus Bar → Domain Bar (for RegimeDetector)
detector_bar = DetectorBar(
    open=float(bar.open),
    high=float(bar.high),
    low=float(bar.low),
    close=float(bar.close),
    volume=float(bar.volume),
)

# Domain Rung → Nautilus LimitOrder (in OrderDiff/Strategy)
order = self.order_factory.limit(
    instrument_id=instrument.id,
    order_side=OrderSide.BUY if side == Side.LONG else OrderSide.SELL,
    quantity=Quantity(rung.qty, precision=instrument.size_precision),
    price=Price(rung.price, precision=instrument.price_precision),
    time_in_force=TimeInForce.GTC,
    post_only=True,  # Maker-only
)
```

## Testing Strategy

```bash
# Component tests (unit tests for pure functions)
tests/strategy/test_grid.py        # GridEngine
tests/strategy/test_policy.py      # PlacementPolicy
tests/strategy/test_detector.py    # RegimeDetector
tests/strategy/test_funding_guard.py
tests/strategy/test_order_sync.py

# Integration tests (strategy lifecycle)
tests/strategy/test_strategy_smoke.py

# Operational controls tests
tests/ops/test_kill_switch.py      # 27 tests
tests/ops/test_alerts.py           # 25 tests
tests/ops/test_prometheus.py       # 20 tests

# Data pipeline tests
tests/data/

# Run subset
uv run pytest tests/strategy/ -k "grid"

# Run with coverage
uv run pytest tests/ --cov=naut_hedgegrid --cov-report=html
```

**Test Coverage:**
- 645 tests collected, 608 passing, 37 skipped
- Core component tests (grid, policy, detector, funding guard, order sync)
- Operational controls tests (kill switch, alerts, prometheus)
- Data pipeline tests
- Strategy integration tests

## Operational Controls

When paper or live trading is started with `--enable-ops`, the following services become available:

### Prometheus Metrics (Port 9090 default)

Access metrics at: `http://localhost:9090/metrics`

Query via CLI:
```bash
python -m naut_hedgegrid metrics
```

Integration with monitoring tools:
- Grafana dashboards
- Alertmanager rules
- Prometheus queries

### FastAPI Control Endpoints (Port 8080 default)

Access Swagger docs at: `http://localhost:8080/docs`

Available endpoints:
- `GET /health` - Quick health check
- `GET /api/v1/status` - Full strategy status
- `POST /api/v1/flatten/{side}` - Flatten positions (LONG/SHORT/BOTH)
- `POST /api/v1/set-throttle` - Adjust strategy aggressiveness
- `GET /api/v1/ladders` - Current grid state
- `GET /api/v1/orders` - Open orders

Query via CLI:
```bash
# Get status
python -m naut_hedgegrid status

# Flatten all positions
python -m naut_hedgegrid flatten

# Flatten only longs
python -m naut_hedgegrid flatten --side LONG
```

### Kill Switch

Automatically instantiated by `OperationsManager.start()` when `--enable-ops` is passed. Monitors:
- Max drawdown (unrealized)
- Max drawdown (realized)
- Position size limits (LONG/SHORT inventory)
- Funding cost thresholds
- Margin ratio

When triggered:
1. Cancel all open orders
2. Close all positions (market orders)
3. Send alerts via configured channels
4. Log event with full context

Configuration:
```yaml
kill_switch:
  max_drawdown_pct: 5.0
  max_position_usdt: 10000.0
  max_funding_cost_1h: 50.0
  margin_ratio_threshold: 0.9
```

### Alert System

Multi-channel notifications for:
- Kill switch triggers
- Position fills
- Error conditions
- Strategy state changes

Supported channels:
- Slack webhooks
- Telegram bot
- Console logging
- File logging

## Data Pipeline

### Quick Start

Generate sample data from Tardis.dev:
```bash
export TARDIS_API_KEY="your_key"
python -m naut_hedgegrid.data.scripts.generate_sample_data
```

### Manual Pipeline Execution

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
    --config csv_config.json \
    --output ./data/catalog

# WebSocket JSONL source
python -m naut_hedgegrid.data.pipelines.replay_to_parquet \
    --source websocket \
    --symbol BTCUSDT \
    --config ws_config.json \
    --output ./data/catalog
```

### Output Structure

```
data/catalog/
├── BTCUSDT-PERP.BINANCE/
│   ├── trade_tick.parquet       # Nautilus TradeTick objects
│   ├── mark_price.parquet       # Custom parquet (timestamp, mark_price)
│   └── funding_rate.parquet     # Custom parquet (timestamp, rate, next_funding)
└── instruments.parquet           # CryptoPerpetual definitions
```

### Integration with Backtests

Backtest runner automatically reads from catalog:
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

## Code Style Conventions

**Use ruff, NOT black/flake8/isort:**
```bash
uv run ruff format .      # Format code
uv run ruff check --fix . # Auto-fix violations
```

**Pydantic v2, NOT v1:**
```python
from pydantic import BaseModel, Field  # v2 API

class Config(BaseModel):
    name: str = Field(description="Name field")
```

**Type hints required on public APIs:**
```python
def build_ladders(mid: float, cfg: HedgeGridConfig, regime: Regime) -> list[Ladder]:
    ...
```

**Property-based testing with hypothesis:**
```python
from hypothesis import given
import hypothesis.strategies as st

@given(st.floats(min_value=1.0, max_value=100000.0))
def test_grid_prices_always_positive(mid: float):
    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    assert all(rung.price > 0 for ladder in ladders for rung in ladder)
```

## Module Import Organization

Ruff handles import sorting automatically. Standard order:
1. Standard library
2. Third-party (NautilusTrader, pydantic, etc.)
3. Local application (`naut_hedgegrid.*`)

Prefer absolute imports from package root:
```python
# Good
from naut_hedgegrid.strategy.grid import GridEngine
from naut_hedgegrid.domain.types import Side, Regime

# Avoid relative imports
from ..strategy.grid import GridEngine  # NO
```

## Logging Conventions

Use Nautilus strategy logger (available as `self.log` in Strategy subclass):

```python
self.log.info("Strategy starting")
self.log.warning("No instruments found")
self.log.error("Failed to load config", exc_info=True)
self.log.debug("Order accepted: {order_id}")
```

Rich console for CLI output:
```python
from rich.console import Console

console = Console()
console.print("[green]✓[/green] Success message")
console.print("[yellow]⚠[/yellow] Warning message")
console.print("[red]Error[/red] Error message")
```

## Configuration Examples

Example strategy config structure:
```yaml
# configs/strategies/hedge_grid_v1.yaml
strategy:
  name: HedgeGrid-BTCUSDT
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 50.0      # 0.5% spacing
  grid_levels_long: 10     # 10 levels below mid
  grid_levels_short: 10    # 10 levels above mid
  base_qty: 0.01           # Base order size
  qty_scale: 1.1           # 10% increase per level

exit:
  tp_steps: 2              # TP after 2 grid steps
  sl_steps: 5              # SL after 5 grid steps

regime:
  adx_len: 14
  ema_fast: 21
  ema_slow: 50
  atr_len: 14
  hysteresis_bps: 10.0

policy:
  strategy: throttled-counter
  counter_levels: 5
  counter_qty_scale: 0.5
```

## Backtest Workflow

```bash
# 1. Prepare Parquet data catalog
data/catalog/
├── btcusdt/
│   ├── trades/
│   │   └── 2024-01-01.parquet
│   ├── bars/
│   │   └── 2024-01-01.parquet
│   └── funding/
│       └── 2024-01-01.parquet

# 2. Configure backtest
configs/backtest/btcusdt_mark_trades_funding.yaml

# 3. Run backtest
uv run python -m naut_hedgegrid backtest

# 4. View results
artifacts/backtests/20241014_120000/
├── config.json       # Full config used
├── summary.json      # Metrics summary
├── orders.csv        # All orders
├── positions.csv     # Position history
└── metrics.csv       # Performance metrics
```

## Optimization Workflow

```bash
# 1. Ensure backtest data is prepared (see Backtest Workflow)

# 2. Run optimization
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 200 \
    --study-name btcusdt_grid_opt

# 3. View optimization progress (live during run)
# Results are saved continuously to SQLite database

# 4. Analyze results after completion
uv run python -m naut_hedgegrid.optimization.cli analyze btcusdt_grid_opt \
    --top-n 10

# 5. Best config automatically saved to:
configs/strategies/btcusdt_grid_opt_best.yaml

# 6. Run backtest with optimized parameters
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/btcusdt_grid_opt_best.yaml
```

**Output Files:**
- `optimization_results.db`: SQLite database with all trial results
- `configs/strategies/{study_name}_best.yaml`: Best parameters as strategy config
- `artifacts/optimization_results.csv`: Optional CSV export of all trials

**Overnight Optimization Script:**

For long-running optimizations, use the provided script:
```bash
# Run 200 trials with 4 parallel workers
uv run python run_optimization_overnight.py
```

This script:
- Uses relaxed constraints for exploration
- Runs 200 trials with parallel execution
- Saves best results automatically
- Provides detailed summary statistics

## NautilusTrader Integration Notes

**Strategy Lifecycle:**
- `__init__()`: Initialize state variables
- `on_start()`: Load config, initialize components, subscribe to data, hydrate grid orders cache
- `on_bar()`: Risk checks (drawdown/circuit breaker) → main trading logic
- `on_data()`: Process funding rate updates from `BinanceFuturesMarkPriceUpdate`
- `on_order_filled()`: Attach TP/SL, track realized PnL
- `on_order_accepted()`: Track live orders
- `on_order_canceled()`: Remove from tracking
- `on_order_rejected()` / `on_order_denied()`: Error tracking → circuit breaker evaluation
- `on_stop()`: Cancel all orders, log final state

**Data Subscriptions:**
```python
# Subscribe to bars in on_start()
self.subscribe_bars(self.bar_type)

# Subscribe to mark price / funding rate updates (live/paper only)
from nautilus_trader.adapters.binance.futures.types import BinanceFuturesMarkPriceUpdate
mark_price_type = DataType(BinanceFuturesMarkPriceUpdate, metadata={"instrument_id": self.instrument_id})
self.subscribe_data(mark_price_type)

# Bar data arrives in on_bar()
def on_bar(self, bar: Bar):
    # bar.open, bar.high, bar.low, bar.close, bar.volume
    ...

# Funding rate data arrives in on_data()
def on_data(self, data):
    # Updates self._last_funding_rate and feeds FundingGuard
    ...
```

**Order Submission with Hedge Mode:**
```python
order = self.order_factory.limit(...)
position_id = PositionId(f"{self.instrument_id}-LONG")
self.submit_order(order, position_id=position_id)
```

## Development Best Practices

1. **Always run pre-commit hooks before committing:**
   ```bash
   uv run pre-commit run --all-files
   ```

2. **Use type hints on all public APIs** - mypy runs in strict mode

3. **Write property-based tests** for pure functions using hypothesis

4. **Keep components pure and functional** where possible - easier to test and reason about

5. **Use Pydantic models for all configuration** - validation happens at load time

6. **Follow the layered architecture** - don't skip layers or create circular dependencies

7. **Document complex algorithms** with inline comments and docstrings

8. **Use domain types** (`Side`, `Regime`, `Rung`, `Ladder`) instead of primitives for type safety

9. **Test operational controls thoroughly** - they are critical for live trading safety

10. **Always enable ops in production** - monitoring and control are essential for live trading

11. **Run optimization before deploying new strategies** - use the optimization framework to find robust parameters

12. **Validate optimized parameters with out-of-sample testing** - optimize on one time period, test on another
