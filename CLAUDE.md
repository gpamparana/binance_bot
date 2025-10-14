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
uv run pytest tests/ --cov=src/naut_hedgegrid --cov-report=html

# Run backtest
uv run python -m naut_hedgegrid backtest \
    --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
    --strategy-config configs/strategies/hedge_grid_v1.yaml
```

## Architecture

The system uses a **layered component architecture** with clear separation of concerns:

### 1. Strategy Layer (`src/naut_hedgegrid/strategies/`)

Complete trading strategy implementations that orchestrate components below.

- **HedgeGridV1** (`hedge_grid_v1/strategy.py`): Main strategy class
  - Subclasses `nautilus_trader.trading.strategy.Strategy`
  - Lifecycle: `on_start()` → `on_bar()` → `on_order_filled()` loop
  - Orchestrates all components in correct order per bar
  - Manages hedge mode positions (LONG/SHORT with position_id suffixes)
  - Attaches TP/SL on fills as reduce-only orders

### 2. Component Layer (`src/naut_hedgegrid/strategy/`)

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
  - Stateful (tracks funding history)

- **OrderDiff** (`order_sync.py`): Minimizes order churn
  - Diffs desired ladders vs live orders
  - Generates minimal operations: adds, cancels, replaces
  - Uses price/qty tolerance to avoid unnecessary updates

### 3. Domain Layer (`src/naut_hedgegrid/domain/`)

Core types and value objects:

- **Side**: Enum for LONG/SHORT position sides
- **Regime**: Enum for UP/DOWN/SIDEWAYS market states
- **Rung**: Single grid level (price, qty, level)
- **Ladder**: Collection of rungs for one side
- **LiveOrder**: Tracking struct for open orders
- **OrderIntent**: Instruction for order operations (create/cancel/replace)

### 4. Exchange Adapter Layer (`src/naut_hedgegrid/exchange/`)

- **PrecisionGuard** (`precision.py`): Enforces exchange requirements
  - Rounds prices to tick size
  - Rounds quantities to step size
  - Validates minimum notional
  - Filters invalid rungs from ladders

### 5. Configuration Layer (`src/naut_hedgegrid/config/`)

Pydantic v2 models with YAML loading:

- **HedgeGridConfig** (`strategy.py`): Main strategy configuration
  - Contains: grid, exit, rebalance, execution, funding, regime, position, policy
  - Loaded via `HedgeGridConfigLoader.load(path)`

- **BacktestConfig** (`backtest.py`): Backtest run configuration
  - Time range, data sources, venues, strategies, execution sim, risk controls

- **VenueConfig** (`venue.py`): Exchange connection settings
  - API credentials, hedge mode, leverage, rate limits

### 6. Runner Layer (`src/naut_hedgegrid/runners/`)

CLI-driven execution:

- **BacktestRunner** (`run_backtest.py`): Orchestrates backtests
  - Loads data from Parquet catalog
  - Configures Nautilus BacktestEngine
  - Runs simulation and extracts results
  - Saves artifacts (JSON + CSV)

### 7. Metrics Layer (`src/naut_hedgegrid/metrics/`)

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

## Configuration System

All configuration is **code-as-config** using Pydantic v2 models with YAML files.

### Strategy Configuration Pattern

1. Define Pydantic model in `src/naut_hedgegrid/config/`
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

## Component Orchestration Pattern

The HedgeGridV1 strategy orchestrates components in this **exact order** each bar:

```python
def on_bar(self, bar: Bar):
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

    # 7. Apply precision guard (enforce exchange rules)
    # Note: Precision guard is applied inside OrderDiff

    # 8. Generate diff vs live orders
    diff_result = self._order_diff.diff(ladders, live_orders)

    # 9. Execute diff operations
    self._execute_diff(diff_result)  # cancels → replaces → adds
```

**This order is critical**: Each step builds on the previous. Regime detection must happen before policy shaping. Funding adjustments must happen before diffing.

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
tests/strategy/test_strategy_smoke.py  # 29 smoke tests

# Run subset
uv run pytest tests/strategy/ -k "grid"
```

**Test Status:**
- ✅ 248 core component tests passing
- ⚠️ 27 strategy smoke tests have BarType parsing errors (known Nautilus 1.220.0 issue, non-critical)

## Recent Fixes (2025-10-14)

### BarType Parsing - FIXED
**Issue**: BarType string parsing failed in HedgeGridV1 strategy.
**Solution**: Strategy now constructs BarType programmatically in `on_start()` method instead of using string parsing.

### TradingNode API - UPDATED
**Change**: Updated from deprecated `node.start()` to `node.run()` for live/paper trading.
**Impact**: All runners now use correct TradingNode lifecycle methods.

### ImportableStrategyConfig - COMPLETE
**Status**: HedgeGridV1 now fully integrates with Nautilus ImportableStrategyConfig pattern.
**Benefit**: Proper strategy loading and configuration management.

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

## NautilusTrader Integration Notes

**Strategy Lifecycle:**
- `__init__()`: Initialize state variables
- `on_start()`: Load config, initialize components, subscribe to data
- `on_bar()`: Main trading logic
- `on_order_filled()`: Attach TP/SL
- `on_order_accepted()`: Track live orders
- `on_order_canceled()`: Remove from tracking
- `on_stop()`: Cancel all orders, log final state

**Data Subscriptions:**
```python
# Subscribe to bars in on_start()
self.subscribe_bars(self.bar_type)

# Data arrives in on_bar()
def on_bar(self, bar: Bar):
    # bar.open, bar.high, bar.low, bar.close, bar.volume
    ...
```

**Order Submission with Hedge Mode:**
```python
order = self.order_factory.limit(...)
position_id = PositionId(f"{self.instrument_id}-LONG")
self.submit_order(order, position_id=position_id)
```
