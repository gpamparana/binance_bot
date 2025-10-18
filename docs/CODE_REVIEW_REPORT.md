# Code Review Report: naut-hedgegrid

**Date**: 2025-10-14
**Reviewer**: Claude Code Analysis
**Scope**: Comprehensive codebase review for NautilusTrader best practices, architecture, and code duplication

---

## Executive Summary

**Overall Assessment**: ✅ **GOOD** - The codebase demonstrates solid understanding of NautilusTrader patterns with well-designed components. Found **3 critical issues**, **5 moderate concerns**, and several opportunities for improvement.

**Key Strengths**:
- Clean separation of concerns (strategy components are pure functions)
- Proper use of Nautilus Strategy lifecycle (on_start, on_bar, on_event)
- Excellent use of Decimal for financial calculations
- Well-documented hedge mode implementation
- Comprehensive operational controls integration

**Critical Issues to Address**:
1. Threading lock in event handler (performance bottleneck)
2. Missing async/await in retry timer callback
3. Potential race condition in operational metrics

---

## 1. NautilusTrader Correctness Review

### 1.1 Strategy Lifecycle Implementation ✅ **PASS**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`

**Positives**:
- Proper `on_start()` implementation with sequential initialization
- Correct subscription to bar data
- Clean shutdown in `on_stop()` with order cancellation
- Event routing via `on_event()` is correct

**Issues**: None

---

### 1.2 Event Handler Patterns ⚠️ **MODERATE ISSUES**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:242`

#### Issue #1: Threading Lock in Event Handler (⚠️ CRITICAL)

**Location**: Lines 318-323

```python
# Store ladder state for snapshot access (before any filtering)
with self._ops_lock:
    for ladder in ladders:
        if ladder.side == Side.LONG:
            self._last_long_ladder = ladder
        elif ladder.side == Side.SHORT:
            self._last_short_ladder = ladder
```

**Problem**: Using `threading.Lock()` in `on_bar()` event handler can block the event loop if operational metrics are being read from API thread.

**Impact**: HIGH - Potential latency spikes during bar processing.

**Recommendation**:
```python
# Option 1: Use asyncio.Lock for async compatibility
self._ops_lock = asyncio.Lock()

async with self._ops_lock:
    self._last_long_ladder = ladder

# Option 2: Use copy-on-write pattern (no lock needed)
self._last_long_ladder = ladder  # Python assignment is atomic for references
```

**Nautilus Best Practice**: Event handlers should never block. Use atomic operations or async locks.

---

#### Issue #2: Timer Callback Not Async (⚠️ CRITICAL)

**Location**: Lines 640-644

```python
self.clock.set_timer_ns(
    name=f"retry_{client_order_id}_{new_attempt}",
    interval_ns=delay_ns,
    callback=lambda: self._execute_add(new_intent),  # ⚠️ Not async!
)
```

**Problem**: `_execute_add()` submits orders via `self.submit_order()` which is async. The lambda callback is not awaited.

**Impact**: CRITICAL - Retry logic may not execute correctly.

**Recommendation**:
```python
# Make callback async
async def retry_callback():
    self._execute_add(new_intent)

self.clock.set_timer_ns(
    name=f"retry_{client_order_id}_{new_attempt}",
    interval_ns=delay_ns,
    callback=retry_callback,
)
```

---

### 1.3 Position ID Patterns ✅ **PASS**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:479,740`

**Analysis**:
```python
# Correct hedge mode position ID format
position_id = PositionId(f"{self.instrument_id}-{side.value}")
# Example: "BINANCE-BTCUSDT-PERP.BINANCE-LONG"
```

**Assessment**: ✅ Correct implementation for Nautilus hedge mode (OmsType.HEDGING).

**Best Practice Confirmed**: Using position_id suffix (-LONG, -SHORT) is the recommended Nautilus pattern for hedge mode.

---

### 1.4 Order Factory Usage ✅ **PASS**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:766-775,811-821,856-865`

**Analysis**:
```python
# Correct use of order_factory for limit orders
order = self.order_factory.limit(
    instrument_id=instrument.id,
    order_side=order_side,
    quantity=Quantity(intent.qty, precision=instrument.size_precision),
    price=Price(intent.price, precision=instrument.price_precision),
    time_in_force=TimeInForce.GTC,
    post_only=True,
    client_order_id=self.clock.generate_client_order_id(intent.client_order_id),
)
```

**Assessment**: ✅ Correct usage of:
- `self.order_factory` for order creation
- `self.clock.generate_client_order_id()` for ID generation
- Proper precision handling with instrument metadata
- Correct `post_only=True` for maker orders

---

### 1.5 Cache Usage ✅ **PASS**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:136,413,687,907`

**Analysis**:
```python
# Correct cache queries
self._instrument = self.cache.instrument(self.instrument_id)
order = self.cache.order(event.client_order_id)
open_orders = self.cache.orders_open(venue=self._venue)
position = self.cache.position(position_id)
```

**Assessment**: ✅ Excellent use of Nautilus cache:
- Preferred over maintaining separate state tracking
- Queries filtered by venue for efficiency
- Proper null checks after cache queries

**Best Practice**: Using cache queries instead of manual order tracking is the recommended Nautilus approach.

---

## 2. Architecture and Integration Review

### 2.1 Component Design ✅ **EXCELLENT**

**Files**: `src/naut_hedgegrid/strategy/*.py`

**Analysis**: Strategy components follow excellent functional patterns:

```python
# GridEngine - Pure static methods
@staticmethod
def build_ladders(mid: float, cfg: HedgeGridConfig, regime: Regime) -> list[Ladder]:
    ...

# RegimeDetector - Stateful but encapsulated
class RegimeDetector:
    def update_from_bar(self, bar: Bar) -> None:
        ...
```

**Assessment**: ✅ **EXCELLENT**
- **GridEngine**: Pure functions, deterministic, testable
- **PlacementPolicy**: Pure functions for ladder shaping
- **FundingGuard**: Pure functions for time-based adjustments
- **OrderDiff**: Stateless diff algorithm
- **RegimeDetector**: Encapsulated state with clear interface

**Best Practice**: This separation allows unit testing components independently of Nautilus infrastructure.

---

### 2.2 Configuration System ⚠️ **MODERATE DUPLICATION**

**Files**: `src/naut_hedgegrid/config/*.py`

#### Concern #1: Custom Config Pattern vs Nautilus

**Current Approach**:
```python
# Custom Pydantic v2 models + YAML loading
class BacktestConfig(BaseModel):
    backtest: BacktestInfo
    time_range: TimeRange
    data: DataConfig
    ...

# Custom loader
BacktestConfigLoader.load("config.yaml")
```

**Nautilus Native Approach**:
```python
# Nautilus has built-in config patterns
from nautilus_trader.config import BacktestEngineConfig, StrategyConfig
```

**Assessment**: ⚠️ **MODERATE DUPLICATION**

**Justification**: Custom config is acceptable because:
1. Nautilus configs are generic and don't support grid-specific params
2. Our config includes backtest metadata, data sources, and strategy combos
3. Pydantic v2 provides better validation than Nautilus default configs

**Recommendation**: **KEEP AS-IS** - The custom config system adds value beyond what Nautilus provides.

**Best Practice**: Document in CLAUDE.md that we use custom configs for flexibility.

---

### 2.3 Runners Architecture ⚠️ **MINOR DUPLICATION**

**Files**: `src/naut_hedgegrid/runners/run_backtest.py`, `run_paper.py`, `run_live.py`

#### Concern #2: Custom Runner vs Nautilus CLI

**Current Approach**:
```python
# Custom BacktestRunner class
runner = BacktestRunner(backtest_config, strategy_configs)
engine, data = runner.run(catalog)
```

**Nautilus Native Approach**:
```python
# Nautilus provides nautilus CLI tool
# But it's less flexible for custom workflows
```

**Assessment**: ⚠️ **JUSTIFIED DUPLICATION**

**Justification**:
1. Our runner integrates custom config loading
2. Adds artifact management (JSON summaries, CSV exports)
3. Rich console output with progress bars
4. Custom metric calculation

**Recommendation**: **KEEP AS-IS** - Provides better UX than nautilus CLI.

**Best Practice**: Our runner wraps Nautilus BacktestEngine correctly - no architectural violations.

---

### 2.4 Data Pipeline ✅ **CORRECT INTEGRATION**

**File**: `src/naut_hedgegrid/runners/run_backtest.py:76-247`

**Analysis**:
```python
# Correct use of Nautilus ParquetDataCatalog
catalog = ParquetDataCatalog(path=str(catalog_path))
instruments = catalog.instruments(instrument_ids=[instrument_id.value])
bars = catalog.bars(instrument_ids=instrument_ids, start=start, end=end)
engine.add_data(bar)
```

**Assessment**: ✅ **EXCELLENT**
- Using Nautilus ParquetDataCatalog (not reinventing)
- Correct data loading patterns
- Proper type conversions (Bar from catalog → Bar for engine)

**Best Practice**: We're using Nautilus data abstractions correctly.

---

### 2.5 Operational Infrastructure ✅ **COMPLEMENTARY**

**Files**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:942-1246`

#### Analysis: Prometheus + FastAPI Integration

**Current Approach**:
```python
def get_operational_metrics(self) -> dict:
    """Return metrics for Prometheus export."""
    return {
        "long_inventory_usdt": self._calculate_inventory("long"),
        "unrealized_pnl_usdt": self._get_unrealized_pnl(),
        ...
    }

def flatten_side(self, side: str) -> dict:
    """Flatten positions for kill switch."""
    ...
```

**Assessment**: ✅ **COMPLEMENTARY** (Not Duplicating Nautilus)

**Justification**:
1. Nautilus doesn't provide built-in Prometheus metrics
2. Nautilus doesn't have operational control APIs
3. This adds production-ready monitoring to Nautilus strategies

**Recommendation**: **KEEP AND EXPAND** - This is valuable infrastructure.

**Best Practice**: Integration methods (`attach_kill_switch`, `get_operational_metrics`) are clean extension points.

---

## 3. Code Quality and Performance

### 3.1 Financial Calculations ✅ **EXCELLENT**

**File**: `src/naut_hedgegrid/strategy/grid.py:46-48`

```python
# Excellent use of Decimal for price calculations
mid_decimal = Decimal(str(mid))
step_bps = Decimal(str(cfg.grid.grid_step_bps))
price_step = mid_decimal * (step_bps / Decimal("10000"))
```

**Assessment**: ✅ **GOLD STANDARD**
- Using `Decimal` for all financial math
- Proper quantization with `ROUND_HALF_UP`
- Prevents floating-point precision errors

**Best Practice**: This is the correct pattern for trading systems. No changes needed.

---

### 3.2 Precision Guards ✅ **EXCELLENT**

**File**: `src/naut_hedgegrid/exchange/precision.py`

**Analysis**: PrecisionGuard filters rungs that violate exchange rules:
- Minimum notional value
- Tick size boundaries
- Lot size boundaries

**Assessment**: ✅ **EXCELLENT** - Essential for production trading.

**Best Practice**: Always validate orders before submission.

---

### 3.3 Order Diff Algorithm ✅ **EFFICIENT**

**File**: `src/naut_hedgegrid/strategy/order_sync.py:165-251`

**Analysis**:
```python
# Efficient O(n) diff with tolerance-based matching
live_by_level_side: dict[tuple[Side, int], LiveOrder] = {}
for order in open_live_orders:
    parsed = parse_client_order_id(order.client_order_id)
    key = (parsed["side"], parsed["level"])
    live_by_level_side[key] = order
```

**Assessment**: ✅ **EFFICIENT**
- O(n) complexity using hashmap
- Tolerance-based matching reduces order churn
- Handles malformed client_order_ids gracefully

**Best Practice**: Intelligent diffing minimizes API calls and exchange fees.

---

### 3.4 LRU Cache Usage ⚠️ **MINOR CONCERN**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:882-894`

```python
@lru_cache(maxsize=1000)
def _parse_cached_order_id(self, client_order_id: str) -> dict:
    return parse_client_order_id(client_order_id)
```

**Issue**: LRU cache on instance method doesn't work correctly (self argument prevents caching).

**Impact**: LOW - Parser is already fast, cache adds minimal benefit.

**Recommendation**:
```python
# Option 1: Make it a module-level cache
@lru_cache(maxsize=1000)
def _parse_order_id_cached(client_order_id: str) -> dict:
    return parse_client_order_id(client_order_id)

# Option 2: Use functools.cache on the function itself
# Or just remove the cache - the parser is fast enough
```

---

## 4. Critical Bugs and Edge Cases

### 4.1 Race Condition in Operational Metrics ⚠️ **CRITICAL**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:953-978`

```python
def get_operational_metrics(self) -> dict:
    with self._ops_lock:  # ⚠️ Lock held during calculations
        return {
            "long_inventory_usdt": self._calculate_inventory("long"),  # Calls cache.position()
            "unrealized_pnl_usdt": self._get_unrealized_pnl(),  # Calls cache.position()
            ...
        }
```

**Problem**: Lock is held while calling cache queries, which could block if cache is slow.

**Impact**: HIGH - Could cause latency in on_bar() if metrics are being queried.

**Recommendation**:
```python
def get_operational_metrics(self) -> dict:
    # Gather data quickly
    last_mid = self._last_mid
    last_bar_time = self._last_bar_time

    # Release lock before expensive operations
    # Cache queries don't need lock protection
    return {
        "long_inventory_usdt": self._calculate_inventory("long"),
        ...
    }
```

---

### 4.2 TP/SL Price Calculation Edge Cases ✅ **HANDLED**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:456-471`

```python
# Ensure positive prices
if sl_decimal <= 0:
    sl_decimal = fill_price_decimal * Decimal("0.01")  # Minimum 1%
sl_price = float(sl_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
```

**Assessment**: ✅ **CORRECT**
- Handles negative TP/SL prices gracefully
- Ensures prices stay positive
- Proper quantization to tick boundaries

---

### 4.3 Missing Validation in on_bar() ⚠️ **MINOR**

**File**: `src/naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:260-262`

```python
if self._hedge_grid_config is None or self._regime_detector is None:
    self.log.warning("Strategy not fully initialized, skipping bar")
    return
```

**Issue**: Should also check `self._funding_guard` and `self._order_diff`.

**Impact**: LOW - Would fail later with AttributeError if missing.

**Recommendation**:
```python
if (self._hedge_grid_config is None or
    self._regime_detector is None or
    self._funding_guard is None or
    self._order_diff is None):
    self.log.warning("Strategy not fully initialized, skipping bar")
    return
```

---

## 5. Documentation and Testing

### 5.1 Docstrings ✅ **EXCELLENT**

**Analysis**: All public methods have comprehensive docstrings with:
- Purpose and behavior description
- Args and Returns documentation
- Raises documentation for exceptions
- Examples where helpful

**Assessment**: ✅ **EXCELLENT** - Professional-grade documentation.

---

### 5.2 Type Hints ✅ **EXCELLENT**

**Analysis**:
- All function signatures have type hints
- Return types specified
- Optional types properly annotated
- Domain types used consistently

**Assessment**: ✅ **EXCELLENT** - Enables static type checking with mypy.

---

### 5.3 Test Coverage ✅ **GOOD**

**Analysis**: 248 passing tests covering:
- Unit tests for pure components (grid, policy, detector)
- Integration tests for component orchestration
- Parity tests (backtest vs paper)

**Assessment**: ✅ **GOOD** - Comprehensive coverage of business logic.

**Opportunity**: Add tests for edge cases in event handlers.

---

## 6. Recommendations Summary

### Critical (Must Fix Before Live Trading)

1. **Fix threading lock in on_bar()** - Use atomic operations or async locks
2. **Fix retry timer callback** - Make callback async or restructure
3. **Fix race condition in metrics** - Don't hold lock during cache queries

### High Priority (Should Fix Soon)

4. **Add missing validation checks** - Check all components in on_bar()
5. **Review LRU cache usage** - Remove or fix instance method caching

### Medium Priority (Nice to Have)

6. **Add event handler tests** - Test on_order_filled, on_order_rejected edge cases
7. **Document threading model** - Clarify thread safety expectations
8. **Add performance profiling** - Identify bottlenecks in on_bar() execution

### Low Priority (Future Enhancements)

9. **Consider Nautilus logging** - Use Nautilus logger instead of Python logging
10. **Add more comprehensive metrics** - Track fill latency, rejection rates

---

## 7. Architecture Verdict

### Are We Duplicating Nautilus Functionality?

**Answer**: ❌ **NO** - We are correctly using Nautilus primitives:

✅ **Using Correctly**:
- Strategy base class and lifecycle
- Order factory and cache
- BacktestEngine and TradingNode
- ParquetDataCatalog
- Event system

✅ **Justified Custom Components**:
- Configuration system (more flexible than Nautilus defaults)
- Runner infrastructure (better UX, artifact management)
- Strategy components (domain-specific, not generic)
- Operational controls (Prometheus, FastAPI - not in Nautilus)

### Is Our Architecture Sound?

**Answer**: ✅ **YES** - Well-designed system:

1. **Separation of Concerns**: Strategy orchestrates pure components
2. **Testability**: Components tested independently
3. **Type Safety**: Comprehensive type hints
4. **Financial Precision**: Decimal for all calculations
5. **Production Ready**: Operational controls, monitoring, error handling

---

## 8. Overall Grade

| Category | Grade | Notes |
|----------|-------|-------|
| **Nautilus Integration** | A | Correct use of Strategy, cache, order factory |
| **Architecture** | A | Clean separation, testable components |
| **Code Quality** | A- | Excellent except for 3 critical issues |
| **Financial Safety** | A+ | Decimal precision, validation, guards |
| **Performance** | B+ | Good except threading lock issue |
| **Documentation** | A | Comprehensive docstrings, type hints |
| **Testing** | A- | 248 tests, could add event handler tests |

**Overall**: **A-** (Excellent with minor issues to fix)

---

## 9. Next Steps

### Before Live Trading

1. ✅ Fix critical threading issues (Sections 1.2, 4.1)
2. ✅ Add validation checks (Section 4.3)
3. ✅ Review and test event handlers thoroughly
4. ✅ Run extended paper trading (1 week minimum)

### For Continuous Improvement

5. Profile on_bar() performance under load
6. Add more comprehensive event handler tests
7. Document threading model and concurrency expectations
8. Consider adding circuit breakers for API rate limits

---

## 10. Conclusion

This is a **well-architected trading system** that correctly integrates with NautilusTrader. The codebase demonstrates strong understanding of:
- Event-driven architecture
- Financial calculation precision
- Production-ready error handling
- Comprehensive testing and documentation

**The critical issues identified are fixable** and don't represent fundamental architectural problems. With these fixes, the system is ready for extended paper trading and eventual live deployment.

**No significant code duplication** - we're using Nautilus where appropriate and adding complementary functionality where needed.

---

**Review Complete**
