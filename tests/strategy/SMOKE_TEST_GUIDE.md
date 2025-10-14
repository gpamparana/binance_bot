# HedgeGridV1 Smoke Tests - Developer Guide

## Quick Start

### Run all smoke tests:
```bash
cd /path/to/naut-hedgegrid
pytest tests/strategy/test_strategy_smoke.py -v
```

### Run specific test category:
```bash
# Initialization tests only
pytest tests/strategy/test_strategy_smoke.py -k "initialization" -v

# Order lifecycle tests only
pytest tests/strategy/test_strategy_smoke.py -k "lifecycle" -v

# Position side tests only
pytest tests/strategy/test_strategy_smoke.py -k "position_side" -v
```

### Run with detailed output:
```bash
pytest tests/strategy/test_strategy_smoke.py -vv --tb=short
```

## Test Development Workflow

### Step 1: Implement Strategy Skeleton

Create `/src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`:

```python
"""HedgeGridV1 strategy implementation."""

from nautilus_trader.trading.strategy import Strategy
from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.strategy import (
    RegimeDetector,
    GridEngine,
    PlacementPolicy,
    FundingGuard,
    OrderDiff,
)
from naut_hedgegrid.exchange.precision import PrecisionGuard
from naut_hedgegrid.domain.types import Side


class HedgeGridV1(Strategy):
    """
    Hedge-mode futures grid trading strategy.

    Orchestrates regime detection, ladder generation, funding adjustments,
    and minimal-churn order synchronization for Binance hedge mode.
    """

    def __init__(self, config: HedgeGridV1Config):
        super().__init__(config)

        # Component instances (initialized in on_start)
        self._detector = None
        self._grid_engine = None
        self._policy = None
        self._funding_guard = None
        self._precision_guard = None
        self._order_diff = None

        # State
        self._hedge_config = None
        self._instrument = None
        self._last_mid = 0.0
        self._live_orders = {}

    def on_start(self):
        """Initialize components and subscribe to data."""
        # Load HedgeGridConfig
        self._hedge_config = HedgeGridConfigLoader.load(
            self.config.hedge_grid_config_path
        )

        # Get instrument
        instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self._instrument = self.cache.instrument(instrument_id)

        if self._instrument is None:
            self.log.error(f"Instrument not found: {instrument_id}")
            return

        # Initialize components
        self._detector = RegimeDetector(
            ema_fast=self._hedge_config.regime.ema_fast,
            ema_slow=self._hedge_config.regime.ema_slow,
            adx_len=self._hedge_config.regime.adx_len,
            atr_len=self._hedge_config.regime.atr_len,
            hysteresis_bps=self._hedge_config.regime.hysteresis_bps,
        )

        self._grid_engine = GridEngine()
        self._policy = PlacementPolicy(self._hedge_config.policy)
        self._funding_guard = FundingGuard(self._hedge_config.funding)
        self._precision_guard = PrecisionGuard(instrument=self._instrument)
        self._order_diff = OrderDiff(
            strategy_name="HG1",
            precision_guard=self._precision_guard,
        )

        # Subscribe to bar data
        bar_type = BarType.from_str(self.config.bar_type)
        self.subscribe_bars(bar_type)

        self.log.info("HedgeGridV1 strategy started")

    def on_bar(self, bar: Bar):
        """Process bar and synchronize orders."""
        # Update detector
        self._detector.update_from_bar(bar)

        # Update last mid
        self._last_mid = bar.close

        # Wait for detector warmup
        if not self._detector.is_warm:
            return

        # Get current regime
        regime = self._detector.current()

        # Build desired ladders
        ladders = GridEngine.build_ladders(
            mid=self._last_mid,
            config=self._hedge_config,
            regime=regime,
        )

        # Apply policy adjustments
        adjusted_ladders = self._policy.adjust_ladders(ladders, regime)

        # Apply funding adjustments (if near funding time)
        final_ladders = self._funding_guard.adjust_ladders(
            adjusted_ladders,
            current_time=self.clock.timestamp_ns(),
        )

        # Generate diff
        diff = self._order_diff.diff(final_ladders, list(self._live_orders.values()))

        # Execute operations
        self._execute_diff(diff)

    def on_order_accepted(self, event: OrderAccepted):
        """Track accepted order."""
        # Add to live orders tracking
        self._live_orders[event.client_order_id] = self._create_live_order(event)

    def on_order_filled(self, event: OrderFilled):
        """Attach TP/SL on grid order fill."""
        # Extract fill details
        side = event.order_side
        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)

        # Calculate TP/SL prices
        tp_price, sl_price = self._calculate_tp_sl(side, fill_price)

        # Create position_id suffix
        position_id = self._make_position_id(side)

        # Submit TP order
        tp_order = self.order_factory.limit(
            instrument_id=event.instrument_id,
            order_side=OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY,
            quantity=Quantity.from_str(str(fill_qty)),
            price=Price.from_str(str(tp_price)),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            position_id=position_id,
        )
        self.submit_order(tp_order)

        # Submit SL order
        sl_order = self.order_factory.stop_market(
            instrument_id=event.instrument_id,
            order_side=OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY,
            quantity=Quantity.from_str(str(fill_qty)),
            trigger_price=Price.from_str(str(sl_price)),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            position_id=position_id,
        )
        self.submit_order(sl_order)

    def on_order_canceled(self, event: OrderCanceled):
        """Remove canceled order from tracking."""
        if event.client_order_id in self._live_orders:
            del self._live_orders[event.client_order_id]

    def on_stop(self):
        """Clean up resources."""
        self.log.info("HedgeGridV1 strategy stopped")

    def _make_position_id(self, side: OrderSide) -> PositionId:
        """Create position ID with hedge mode suffix."""
        suffix = "LONG" if side == OrderSide.BUY else "SHORT"
        return PositionId(f"{self._instrument.id}-{suffix}")

    def _calculate_tp_sl(self, side: OrderSide, entry_price: float) -> tuple[float, float]:
        """Calculate TP and SL prices from config."""
        cfg = self._hedge_config
        step = self._last_mid * (cfg.grid.grid_step_bps / 10000)

        if side == OrderSide.BUY:  # LONG position
            tp_price = entry_price + (step * cfg.exit.tp_steps)
            sl_price = entry_price - (step * cfg.exit.sl_steps)
        else:  # SHORT position
            tp_price = entry_price - (step * cfg.exit.tp_steps)
            sl_price = entry_price + (step * cfg.exit.sl_steps)

        return tp_price, sl_price
```

### Step 2: Run Initialization Tests

```bash
pytest tests/strategy/test_strategy_smoke.py::test_strategy_initialization -v
pytest tests/strategy/test_strategy_smoke.py::test_on_start_loads_config -v
```

**Expected output:**
```
tests/strategy/test_strategy_smoke.py::test_strategy_initialization PASSED
tests/strategy/test_strategy_smoke.py::test_on_start_loads_config PASSED
```

### Step 3: Run Bar Processing Tests

```bash
pytest tests/strategy/test_strategy_smoke.py -k "on_bar" -v
```

**Fix any failures** related to:
- Detector not updating
- Orders not generating after warmup
- _last_mid not tracking

### Step 4: Run Position Side Tests

```bash
pytest tests/strategy/test_strategy_smoke.py -k "position_side" -v
```

**Critical checks:**
- LONG orders must have `position_id` ending with `-LONG`
- SHORT orders must have `position_id` ending with `-SHORT`
- Both must match instrument_id exactly

### Step 5: Run Order Lifecycle Tests

```bash
pytest tests/strategy/test_strategy_smoke.py -k "order_accepted or order_filled or order_canceled" -v
```

**Validate:**
- Order tracking synchronized
- TP/SL orders attached on fills
- Quantities and prices correct
- reduce_only flag set

### Step 6: Run Integration Tests

```bash
pytest tests/strategy/test_strategy_smoke.py::test_full_lifecycle_sideways_regime -v
pytest tests/strategy/test_strategy_smoke.py::test_full_lifecycle_regime_transition -v
```

**Final smoke test** - If these pass, core functionality is working.

## Debugging Failed Tests

### Test: `test_on_start_loads_config`

**Failure**: `AttributeError: 'HedgeGridV1' object has no attribute '_detector'`

**Fix**: Ensure on_start() initializes all components:
```python
def on_start(self):
    self._detector = RegimeDetector(...)
    self._grid_engine = GridEngine()
    # ... initialize other components
```

### Test: `test_position_side_suffixes_long`

**Failure**: `AssertionError: LONG order position_id should end with -LONG, got BTCUSDT-PERP.BINANCE`

**Fix**: Add position_id suffix when creating orders:
```python
position_id = PositionId(f"{instrument_id}-LONG")
order = self.order_factory.limit(
    # ... other params
    position_id=position_id,
)
```

### Test: `test_on_order_filled_attaches_tp_sl_long`

**Failure**: `AssertionError: Expected 2 orders (TP+SL), got 0`

**Fix**: Implement TP/SL attachment in on_order_filled:
```python
def on_order_filled(self, event: OrderFilled):
    tp_price, sl_price = self._calculate_tp_sl(event.order_side, float(event.last_px))

    # Submit TP order
    tp_order = self.order_factory.limit(...)
    self.submit_order(tp_order)

    # Submit SL order
    sl_order = self.order_factory.stop_market(...)
    self.submit_order(sl_order)
```

### Test: `test_diff_generates_minimal_operations`

**Failure**: `AssertionError: Diff generated unnecessary operations for unchanged state`

**Fix**: Ensure OrderDiff uses tolerance-based matching:
```python
# In OrderMatcher
def match_price(self, desired: float, live: float) -> bool:
    if live == 0:
        return False
    diff_bps = abs((desired - live) / live) * 10000
    return diff_bps <= self._price_tolerance_bps  # Default: 1 bps
```

## Test Data Patterns

### Creating Uptrend Bars:
```python
for i in range(60):
    price = 50000.0 + i * 50  # Steady increase
    bar = create_test_bar(
        open_price=price,
        high=price + 50,
        low=price - 10,
        close=price + 40,
    )
    strategy.on_bar(bar)
```

### Creating Downtrend Bars:
```python
for i in range(60):
    price = 50000.0 - i * 50  # Steady decrease
    bar = create_test_bar(
        open_price=price,
        high=price + 10,
        low=price - 50,
        close=price - 40,
    )
    strategy.on_bar(bar)
```

### Creating Sideways Bars:
```python
for i in range(60):
    price = 50000.0 + (i % 10) * 10  # Oscillating
    bar = create_test_bar(
        open_price=price,
        high=price + 50,
        low=price - 50,
        close=price + 25,
    )
    strategy.on_bar(bar)
```

## Continuous Integration

### Pre-commit Hook:
```bash
# .git/hooks/pre-commit
#!/bin/bash
pytest tests/strategy/test_strategy_smoke.py --tb=short
if [ $? -ne 0 ]; then
    echo "Smoke tests failed. Commit aborted."
    exit 1
fi
```

### GitHub Actions:
```yaml
# .github/workflows/smoke-tests.yml
name: Smoke Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - run: pip install -e ".[test]"
      - run: pytest tests/strategy/test_strategy_smoke.py -v
```

## Test Coverage Goals

### Minimum Coverage: 80%
```bash
pytest tests/strategy/test_strategy_smoke.py \
    --cov=naut_hedgegrid.strategies.hedge_grid_v1 \
    --cov-report=term-missing \
    --cov-fail-under=80
```

### Focus Areas:
- **Critical paths**: Order generation, TP/SL attachment, position IDs
- **Error handling**: Missing instrument, invalid bars, precision failures
- **State management**: Order tracking, regime transitions, diff synchronization

## Performance Benchmarks

### Expected Test Runtimes:
- Initialization tests: < 0.1s each
- Bar processing tests: < 0.5s each (60 bars)
- Integration tests: < 2s each (100+ bars)
- Full suite: < 30s

### Profiling:
```bash
pytest tests/strategy/test_strategy_smoke.py --profile
```

## Next Steps

After smoke tests pass:
1. Run component unit tests: `pytest tests/strategy/ -v`
2. Run integration tests with real data
3. Run backtest validation tests
4. Deploy to testnet with monitoring

## Support

For questions or issues:
1. Check test output messages (often contain hints)
2. Review README_SMOKE_TESTS.md for detailed test descriptions
3. Examine existing passing tests as examples
4. Debug with: `pytest --pdb tests/strategy/test_strategy_smoke.py::test_name`
