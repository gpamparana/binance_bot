# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

naut-hedgegrid is a hedge-mode grid trading system built on NautilusTrader for Binance futures markets. The system implements adaptive grid trading with regime detection, funding rate management, and automated risk controls.

**Key Technologies:**
- **Python**: 3.12+
- **Trading Engine**: NautilusTrader >= 1.223.0 (event-driven backtesting and live trading)
- **Build System**: uv (fast Python package manager, NOT pip or poetry)
- **Linting**: ruff (replaces black, flake8, isort) with line-length 120
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

## Project Structure

```
naut_hedgegrid/
├── __init__.py
├── __main__.py
├── cli.py
├── adapters/
│   └── binance_testnet_patch.py
├── config/
│   ├── base.py              # BaseYamlConfigLoader
│   ├── backtest.py
│   ├── operations.py
│   ├── strategy.py
│   └── venue.py
├── data/
│   ├── schemas.py
│   ├── pipelines/
│   │   ├── normalizer.py
│   │   └── replay_to_parquet.py
│   ├── scripts/
│   │   └── generate_sample_data.py
│   └── sources/
│       ├── base.py
│       ├── binance_source.py
│       ├── csv_source.py
│       ├── tardis_source.py
│       └── websocket_source.py
├── domain/
│   └── types.py
├── exchange/
│   └── precision.py
├── metrics/
│   └── report.py
├── ops/
│   ├── alerts.py
│   ├── kill_switch.py
│   ├── manager.py
│   └── prometheus.py
├── optimization/
│   ├── cli.py
│   ├── constraints.py
│   ├── objective.py
│   ├── optimizer.py
│   ├── parallel_runner.py
│   ├── param_space.py
│   └── results_db.py
├── runners/
│   ├── __main__.py
│   ├── base_runner.py
│   ├── run_backtest.py
│   ├── run_live.py
│   └── run_paper.py
├── strategies/
│   └── hedge_grid_v1/
│       ├── config.py
│       ├── exit_manager.py      # ExitManagerMixin
│       ├── metrics.py           # MetricsMixin
│       ├── ops_api.py           # OpsControlMixin
│       ├── order_events.py      # OrderEventsMixin
│       ├── order_executor.py    # OrderExecutionMixin
│       ├── risk_manager.py      # RiskManagementMixin
│       ├── state_persistence.py # StatePersistenceMixin
│       └── strategy.py          # HedgeGridV1 (composes all mixins)
├── strategy/
│   ├── detector.py
│   ├── funding_guard.py
│   ├── grid.py
│   ├── order_sync.py
│   └── policy.py
├── ui/
│   └── api.py
├── utils/
│   └── yamlio.py
└── warmup/
    └── binance_warmer.py

tests/
├── config/
│   └── test_config_loading.py
├── data/
│   ├── sources/
│   │   ├── test_csv_source.py
│   │   └── test_websocket_source.py
│   ├── test_normalizer.py
│   ├── test_pipeline.py
│   ├── test_schemas.py
│   └── test_schemas_property.py
├── domain/
│   └── test_types.py
├── exchange/
│   └── test_precision.py
├── ops/
│   ├── test_alerts.py
│   ├── test_api.py
│   ├── test_kill_switch.py
│   └── test_prometheus.py
├── optimization/
│   ├── test_constraints.py
│   ├── test_objective.py
│   ├── test_param_space.py
│   └── test_results_db.py
├── strategy/
│   ├── test_detector.py
│   ├── test_funding_guard.py
│   ├── test_grid.py
│   ├── test_order_sync.py
│   ├── test_policy.py
│   ├── test_state_persistence.py
│   └── test_strategy_smoke.py
├── utils/
│   └── test_yamlio.py
├── test_ops_integration.py
├── test_order_diff.py
├── test_parity.py
├── test_post_only_retry.py
└── test_precision.py

configs/
├── backtest/
│   └── btcusdt_mark_trades_funding.yaml
├── optimization/
│   └── default_optimization.yaml
├── strategies/
│   ├── hedge_grid_v1.yaml               # Primary config
│   ├── hedge_grid_v1_production.yaml
│   └── hedge_grid_v1_testnet.yaml
└── venues/
    ├── binance_futures.yaml
    └── binance_futures_testnet.yaml

examples/
├── data_configs/
│   ├── csv_source_config.json
│   ├── sample_trades.csv
│   └── websocket_source_config.json
├── kill_switch_integration.py
├── optimize_strategy.py
├── paper_trade_with_warmup.py
├── quick_backtest.ipynb
└── verify_data_pipeline.py

scripts/
└── run_backtest_with_logs.sh
```

Root-level utility scripts: `run_optimization.py`, `run_optimization_overnight.py`, `debug_backtest.py`

## Architecture

The system uses a **layered component architecture** with clear separation of concerns:

### 1. Strategy Layer (`naut_hedgegrid/strategies/`)

Complete trading strategy implementations that orchestrate all components.

**HedgeGridV1 Mixin Architecture** (`hedge_grid_v1/strategy.py`):

The `HedgeGridV1` class was refactored from a monolithic class into 7 focused mixins for clarity and testability:

```python
class HedgeGridV1(
    RiskManagementMixin,    # risk_manager.py    - Drawdown protection, circuit breaker, position validation
    MetricsMixin,           # metrics.py         - Operational metrics, inventory, PnL tracking
    OrderEventsMixin,       # order_events.py    - Order lifecycle callbacks, retry logic
    OrderExecutionMixin,    # order_executor.py  - Order creation, diff execution pipeline
    ExitManagerMixin,       # exit_manager.py    - TP/SL attachment, OCO-like cancellation
    OpsControlMixin,        # ops_api.py         - Kill switch, flatten, throttle, ladder snapshots
    StatePersistenceMixin,  # state_persistence.py - Atomic save/load of peak_balance, realized_pnl
    Strategy,               # NautilusTrader base
):
```

**Mixin responsibilities:**
- `RiskManagementMixin`: Implements `_check_drawdown_limit()`, `_check_circuit_breaker()`, `_validate_order_size()`
- `MetricsMixin`: Tracks inventory values, PnL, and exposes metrics for Prometheus
- `OrderEventsMixin`: Handles `on_order_accepted()`, `on_order_canceled()`, `on_order_rejected()`, `on_order_denied()`
- `OrderExecutionMixin`: Implements `_execute_diff()`, `_execute_add()`, `_execute_cancel()`, `_execute_replace()`
- `ExitManagerMixin`: Attaches TP/SL reduce-only orders on fills, manages OCO-like cancellation
- `OpsControlMixin`: Exposes flatten, set-throttle, and ladder snapshot operations to the REST API
- `StatePersistenceMixin`: Atomically saves and loads `peak_balance` and `realized_pnl` across restarts

**Strategy lifecycle**: `on_start()` → `on_bar()` → `on_order_filled()` loop

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

Core types and value objects (all defined in `types.py`):

- **Side**: Enum for LONG/SHORT position sides
- **Regime**: Enum for UP/DOWN/SIDEWAYS market states
- **Rung**: Single grid level (price, qty, level)
- **Ladder**: Collection of rungs for one side
- **LiveOrder**: Tracking struct for open orders
- **OrderIntent**: Instruction for order operations (create/cancel/replace)

### 4. Exchange Adapter Layer (`naut_hedgegrid/exchange/`)

- **PrecisionGuard** (`precision.py`): Enforces exchange requirements
  - Rounds prices to tick size, quantities to step size
  - Validates minimum notional
  - Filters invalid rungs from ladders
  - Applied inside OrderDiff before order submission

### 5. Configuration Layer (`naut_hedgegrid/config/`)

Pydantic v2 models with YAML loading:

- **BaseYamlConfigLoader** (`base.py`): Abstract loader with `${ENV_VAR}` interpolation
- **HedgeGridConfig** (`strategy.py`): Main strategy configuration
  - Contains: grid, exit, rebalance, execution, funding, regime, position, policy, risk_management
  - **Important**: Risk values are nested under `position.*` and `risk_management.*` (e.g., `cfg.position.max_position_pct`, `cfg.risk_management.max_drawdown_pct`). Do NOT use `getattr()` on the root config object for these.
  - Loaded via `HedgeGridConfigLoader.load(path)`
- **BacktestConfig** (`backtest.py`): Time range, data sources, venues, strategies, execution sim
- **VenueConfig** (`venue.py`): API credentials, hedge mode, leverage, rate limits
- **OperationsConfig** (`operations.py`): Kill switch thresholds, alert channels, Prometheus/API ports

### 6. Runner Layer (`naut_hedgegrid/runners/`)

CLI-driven execution:

- **BaseRunner** (`base_runner.py`): Abstract base — environment validation, Nautilus node config, warmup orchestration, ops manager integration
- **BacktestRunner** (`run_backtest.py`): Loads Parquet catalog, configures BacktestEngine, saves artifacts (JSON + CSV)
- **PaperRunner** (`run_paper.py`): Connects to real market data, simulates fills, no real money
- **LiveRunner** (`run_live.py`): Connects to exchange with execution enabled, places real orders

### 7. Metrics Layer (`naut_hedgegrid/metrics/`)

- **PerformanceMetrics / ReportGenerator** (`report.py`): 32 metrics across 7 categories
  - Returns: total PnL, annualized return, CAGR
  - Risk: Sharpe, Sortino, Calmar, volatility
  - Drawdown: max, average, recovery time
  - Trades: win rate, profit factor, expectancy
  - Execution: fill rate, maker ratio, slippage
  - Funding: paid/received/net
  - Ladder utilization: depth, fill rate

### 8. Data Pipeline Layer (`naut_hedgegrid/data/`)

Complete data ingestion system for building Parquet catalogs:

- **schemas.py**: Pydantic schemas + Nautilus converters (TradeSchema, MarkPriceSchema, FundingRateSchema)
- **sources/**: DataSource adapters — Tardis.dev, Binance API, CSV, WebSocket JSONL
- **pipelines/**: `normalizer.py` (timestamp conversion, deduplication), `replay_to_parquet.py` (main orchestrator)
- **scripts/**: `generate_sample_data.py`

Output catalog structure:
```
data/catalog/
├── BTCUSDT-PERP.BINANCE/
│   ├── trade_tick.parquet
│   ├── mark_price.parquet
│   └── funding_rate.parquet
└── instruments.parquet
```

### 9. Operational Controls Layer (`naut_hedgegrid/ops/`)

Production-grade monitoring and control infrastructure:

- **kill_switch.py**: Automated circuit breakers — max drawdown, position size limits, funding cost thresholds, margin ratio. When triggered: cancel all orders, close positions, send alerts.
- **alerts.py**: Multi-channel notifications — Slack webhooks, Telegram bot, console/file logging
- **prometheus.py**: 15 key metrics in standard Prometheus format
- **manager.py**: `OperationsManager` — starts/stops kill switch, Prometheus server, FastAPI server together

**15 Key Prometheus Metrics:**
- Position: `long_inventory_usdt`, `short_inventory_usdt`, `net_inventory_usdt`
- Grid: `active_rungs_long`, `active_rungs_short`, `open_orders`
- Risk: `margin_ratio`, `maker_ratio`
- Funding: `funding_rate_current`, `funding_cost_1h_projected_usdt`
- PnL: `realized_pnl_usdt`, `unrealized_pnl_usdt`, `total_pnl_usdt`
- Health: `uptime_seconds`, `last_bar_timestamp`

### 10. UI/API Layer (`naut_hedgegrid/ui/`)

FastAPI control endpoints for live operations (`api.py`):

- `GET /health` - Health check
- `GET /api/v1/status` - Full strategy status
- `POST /api/v1/flatten/{side}` - Flatten positions (LONG/SHORT/BOTH)
- `POST /api/v1/set-throttle` - Adjust strategy aggressiveness
- `GET /api/v1/ladders` - Current grid state
- `GET /api/v1/orders` - Open orders

**Authentication:** Optional API key via `X-API-Key` header. Swagger docs at `http://localhost:8080/docs`.

### 11. Adapters Layer (`naut_hedgegrid/adapters/`)

- **binance_testnet_patch.py**: Binance testnet compatibility fixes — instrument provider patches, endpoint corrections

### 12. Warmup Module (`naut_hedgegrid/warmup/`)

- **BinanceDataWarmer** (`binance_warmer.py`): Fetches historical klines from Binance API, converts to NautilusTrader Bar objects, provides DetectorBar format for regime detector
  - Automatic endpoint selection (testnet vs production)
  - Default: 70 bars fetched (50 for EMA slow + 20 buffer for ADX/ATR)
  - Startup overhead: 2-5 seconds typical; non-blocking (strategy starts even if warmup fails)

**Integration:**
- `BaseRunner._warmup_strategy()`: Automatic warmup after initialization
- `HedgeGridV1.warmup_regime_detector()`: Feeds historical bars to detector

### 13. Utilities Layer (`naut_hedgegrid/utils/`)

- **yamlio.py**: YAML loading with `${ENV_VAR}` interpolation support

### 14. Optimization Framework (`naut_hedgegrid/optimization/`)

Bayesian hyperparameter optimization using Optuna:

- **StrategyOptimizer** (`optimizer.py`): Main orchestrator — runs trials, saves best configs to YAML, exports CSV
- **ParameterSpace** (`param_space.py`): Defines 17 tunable parameters across grid, exit, regime, policy, rebalance, funding, position categories
- **MultiObjectiveFunction** (`objective.py`): Weighted scoring — Sharpe (0.35), profit factor (0.30), Calmar (0.35), drawdown penalty (-0.20)
- **ConstraintsValidator** (`constraints.py`): Hard constraint filtering — min Sharpe, max drawdown, min trades, min win rate, min profit factor, min Calmar
- **ParallelBacktestRunner** (`parallel_runner.py`): Multi-process backtest execution
- **OptimizationResultsDB** (`results_db.py`): SQLite persistence — store, query, export, cleanup trials

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
from naut_hedgegrid.optimization import StrategyOptimizer
from naut_hedgegrid.optimization.constraints import ConstraintThresholds

constraints = ConstraintThresholds(
    min_sharpe_ratio=0.5,
    max_drawdown_pct=25.0,
    min_trades=30,
)

optimizer = StrategyOptimizer(
    backtest_config_path="configs/backtest/btcusdt_mark_trades_funding.yaml",
    base_strategy_config_path="configs/strategies/hedge_grid_v1.yaml",
    n_trials=200,
    n_jobs=4,
    study_name="my_optimization",
    constraint_thresholds=constraints,
)

study = optimizer.optimize()
# Best parameters saved to: configs/strategies/my_optimization_best.yaml
print(f"Best score: {study.best_value:.4f}")
print(f"Best params: {study.best_trial.params}")
```

## Configuration System

All configuration is **code-as-config** using Pydantic v2 models with YAML files.

### Pattern

1. Define Pydantic model in `naut_hedgegrid/config/`
2. Create loader class inheriting from `BaseYamlConfigLoader`
3. Store YAML configs in `configs/` directory
4. Load with: `ConfigLoader.load(path)`

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

**Required for paper/live trading:**
- `BINANCE_API_KEY` and `BINANCE_API_SECRET`: Required even for paper trading (instrument metadata load)
- `TARDIS_API_KEY`: Optional, for data fetching only

### Strategy Config Example

```yaml
# configs/strategies/hedge_grid_v1.yaml
strategy:
  name: HedgeGrid-BTCUSDT
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 50.0
  grid_levels_long: 10
  grid_levels_short: 10
  base_qty: 0.01
  qty_scale: 1.1

exit:
  tp_steps: 2
  sl_steps: 5

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

position:
  max_position_pct: 50.0

risk_management:
  max_drawdown_pct: 10.0
  enable_drawdown_protection: true
  enable_circuit_breaker: true
  enable_position_validation: true
  max_errors_per_minute: 5
  circuit_breaker_cooldown_seconds: 60
```

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

1. **Drawdown Protection** (`RiskManagementMixin._check_drawdown_limit`): Called at the start of every `on_bar()`. Tracks peak balance and pauses trading if unrealized drawdown exceeds `risk_management.max_drawdown_pct`. Controlled by `risk_management.enable_drawdown_protection`.

2. **Circuit Breaker** (`RiskManagementMixin._check_circuit_breaker`): Triggered from `on_order_rejected()` and `on_order_denied()`. Tracks error rate per minute and activates cooldown if errors exceed `risk_management.max_errors_per_minute`. Cooldown duration set by `risk_management.circuit_breaker_cooldown_seconds`. Controlled by `risk_management.enable_circuit_breaker`.

3. **Position Size Validation** (`RiskManagementMixin._validate_order_size`): Called in `_execute_add()` before every `submit_order()`. Rejects orders that would push position beyond `position.max_position_pct` of account balance. Controlled by `risk_management.enable_position_validation`.

**Config access pattern** (correct):
```python
# Nested access via Pydantic model
max_dd = self._hedge_grid_config.risk_management.max_drawdown_pct
max_pos = self._hedge_grid_config.position.max_position_pct

# WRONG - do not use getattr on root:
# getattr(self._hedge_grid_config, "max_drawdown_pct", 20.0)  # BUG: always returns default
```

**Startup Reconciliation**: On `on_start()`, `_hydrate_grid_orders_cache()` queries existing open orders from the exchange cache and populates `_grid_orders_cache` to prevent duplicate ladder placements after restarts.

**State Persistence** (`StatePersistenceMixin`): Atomically saves `peak_balance` and `realized_pnl` to a JSON sidecar file after each fill. Loaded on `on_start()` to restore continuity across process restarts.

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
```

**TP/SL Attachment on Fills** (handled by `ExitManagerMixin`):

When a grid order fills, the strategy automatically attaches:
1. **Take Profit**: Limit order (reduce-only) at profit target
2. **Stop Loss**: Stop-market order (reduce-only) at stop loss

Both use the opposite side from the fill (SELL to close LONG, BUY to close SHORT) and maintain the same `position_id` suffix.

## Data Flow and Type Conversions

Components use domain types; Nautilus uses Nautilus types. Conversions happen at strategy boundaries:

```python
# Nautilus Bar → Domain Bar (for RegimeDetector)
detector_bar = DetectorBar(
    open=float(bar.open),
    high=float(bar.high),
    low=float(bar.low),
    close=float(bar.close),
    volume=float(bar.volume),
)

# Domain Rung → Nautilus LimitOrder (in OrderExecutionMixin)
order = self.order_factory.limit(
    instrument_id=instrument.id,
    order_side=OrderSide.BUY if side == Side.LONG else OrderSide.SELL,
    quantity=Quantity(rung.qty, precision=instrument.size_precision),
    price=Price(rung.price, precision=instrument.price_precision),
    time_in_force=TimeInForce.GTC,
    post_only=True,  # Maker-only
)
```

## Testing

```bash
# Component tests (pure functions)
tests/strategy/test_grid.py              # GridEngine
tests/strategy/test_policy.py           # PlacementPolicy
tests/strategy/test_detector.py         # RegimeDetector
tests/strategy/test_funding_guard.py    # FundingGuard
tests/strategy/test_order_sync.py       # OrderDiff
tests/strategy/test_state_persistence.py  # StatePersistenceMixin

# Strategy integration tests
tests/strategy/test_strategy_smoke.py

# Operational controls tests
tests/ops/test_kill_switch.py
tests/ops/test_alerts.py
tests/ops/test_prometheus.py
tests/ops/test_api.py                   # FastAPI control endpoints

# Data pipeline tests
tests/data/

# Optimization tests
tests/optimization/

# Top-level integration tests
tests/test_ops_integration.py
tests/test_order_diff.py
tests/test_parity.py
tests/test_post_only_retry.py
tests/test_precision.py

# Run a subset
uv run pytest tests/strategy/ -k "grid"

# Run with coverage
uv run pytest tests/ --cov=naut_hedgegrid --cov-report=html
```

**Test Coverage:** 675 tests collected. Core component tests, operational controls, data pipeline, optimization framework, and strategy integration tests.

## Operational Controls

When paper or live trading is started with `--enable-ops`, the following services become available:

### Prometheus Metrics (Port 9090 default)

Access at `http://localhost:9090/metrics`. Query via CLI: `python -m naut_hedgegrid metrics`. Integrates with Grafana and Alertmanager.

### FastAPI Control Endpoints (Port 8080 default)

Swagger docs at `http://localhost:8080/docs`. Query via CLI:
```bash
python -m naut_hedgegrid status
python -m naut_hedgegrid flatten
python -m naut_hedgegrid flatten --side LONG
```

### Kill Switch

Instantiated by `OperationsManager.start()`. Monitors drawdown, position size, funding cost, margin ratio. On trigger: cancels all orders, closes all positions (market orders), sends alerts.

```yaml
kill_switch:
  max_drawdown_pct: 5.0
  max_position_usdt: 10000.0
  max_funding_cost_1h: 50.0
  margin_ratio_threshold: 0.9
```

### Alert System

Multi-channel: Slack webhooks, Telegram bot, console/file logging. Fires on kill switch triggers, error conditions, state changes.

## Data Pipeline

### Quick Start

```bash
export TARDIS_API_KEY="your_key"
python -m naut_hedgegrid.data.scripts.generate_sample_data
```

### Manual Execution

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

## Backtest Workflow

```bash
# 1. Prepare data catalog (see Data Pipeline above)

# 2. Run backtest
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml

# 3. View results in artifacts/backtests/<timestamp>/
#    config.json, summary.json, orders.csv, positions.csv, metrics.csv
```

## Optimization Workflow

```bash
# 1. Ensure backtest data is prepared

# 2. Run optimization
uv run python -m naut_hedgegrid.optimization.cli optimize \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --trials 200 \
    --study-name btcusdt_grid_opt

# 3. Analyze results
uv run python -m naut_hedgegrid.optimization.cli analyze btcusdt_grid_opt --top-n 10

# 4. Best config saved to configs/strategies/btcusdt_grid_opt_best.yaml

# 5. Validate with backtest
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/btcusdt_grid_opt_best.yaml
```

**Output Files:**
- `optimization_results.db`: SQLite database with all trial results
- `configs/strategies/{study_name}_best.yaml`: Best parameters as strategy config
- `artifacts/optimization_results.csv`: Optional CSV export

**Overnight run:**
```bash
uv run python run_optimization_overnight.py
```

## NautilusTrader Integration Notes

**Strategy Lifecycle:**
- `__init__()`: Initialize state variables
- `on_start()`: Load config, initialize components, subscribe to data, hydrate grid orders cache, restore persisted state
- `on_bar()`: Risk checks (drawdown/circuit breaker) → main trading logic
- `on_data()`: Process funding rate updates from `BinanceFuturesMarkPriceUpdate`
- `on_order_filled()`: Attach TP/SL via `ExitManagerMixin`, track realized PnL, persist state
- `on_order_accepted()`: Track live orders (`OrderEventsMixin`)
- `on_order_canceled()`: Remove from tracking (`OrderEventsMixin`)
- `on_order_rejected()` / `on_order_denied()`: Error tracking → circuit breaker evaluation (`RiskManagementMixin`)
- `on_stop()`: Cancel all orders, log final state

**Data Subscriptions:**
```python
# Subscribe to bars in on_start()
self.subscribe_bars(self.bar_type)

# Subscribe to mark price / funding rate updates (live/paper only)
from nautilus_trader.adapters.binance.futures.types import BinanceFuturesMarkPriceUpdate
mark_price_type = DataType(BinanceFuturesMarkPriceUpdate, metadata={"instrument_id": self.instrument_id})
self.subscribe_data(mark_price_type)
```

**Order Submission with Hedge Mode:**
```python
order = self.order_factory.limit(...)
position_id = PositionId(f"{self.instrument_id}-LONG")
self.submit_order(order, position_id=position_id)
```

## Code Style Conventions

**Ruff configuration** (see `ruff.toml`):
- `line-length = 120`
- `target-version = "py311"` (for compatibility)
- 50+ rule categories enabled (comprehensive coverage beyond just E, F, I)
- `quote-style = "double"`, `indent-style = "space"`
- Per-file ignores for tests, examples, and debug scripts

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

**Type hints required on all public APIs** (mypy strict mode):
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

Prefer absolute imports:
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
console.print("[green]Success message[/green]")
console.print("[yellow]Warning message[/yellow]")
console.print("[red]Error message[/red]")
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

6. **Follow the layered architecture** - do not skip layers or create circular dependencies

7. **Document complex algorithms** with inline comments and docstrings

8. **Use domain types** (`Side`, `Regime`, `Rung`, `Ladder`) instead of primitives for type safety

9. **Test operational controls thoroughly** - they are critical for live trading safety

10. **Always enable ops in production** - monitoring and control are essential for live trading

11. **Run optimization before deploying new strategies** - use the optimization framework to find robust parameters

12. **Validate optimized parameters with out-of-sample testing** - optimize on one time period, test on another

13. **Respect the mixin boundaries** - add new strategy functionality to the appropriate mixin, not directly to `HedgeGridV1.strategy.py`

14. **Test state persistence** - after any change to persisted fields in `StatePersistenceMixin`, verify round-trip save/load in `tests/strategy/test_state_persistence.py`
