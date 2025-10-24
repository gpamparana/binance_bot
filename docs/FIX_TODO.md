# Known Issues and TODO Items

This document tracks active issues and improvements that need attention in the naut-hedgegrid codebase.

## Critical Issues 🔴 (Fix Immediately)

### 1. TP Order Price Precision Error
**Severity**: MEDIUM - Occasional TP order rejection
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:_create_tp_order`
**Problem**: TP price not always conforming to Binance tick size
**Error**: `BinanceClientError({'code': -4014, 'msg': 'Price not increased by tick size.'})`

**Recommended Fix**:
```python
# Ensure TP prices are properly rounded to tick size
tp_price_rounded = self._instrument.price_precision.round(tp_price)
```

### 2. Thread Safety Violations in Strategy State Management
**Severity**: CRITICAL - Can cause trading losses
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:566-571, 1465-1480`
**Problem**: Multiple state variables accessed from both event handlers and API threads without proper synchronization. Reading multiple related fields is NOT atomic.

**Current Code Problem**:
```python
# strategy.py:1465-1480
def get_ladders_snapshot(self) -> dict:
    # DANGER: Reading two references is NOT atomic!
    if self._last_long_ladder is None and self._last_short_ladder is None:
        return {"long_ladder": [], "short_ladder": [], "mid_price": 0.0}

    return {
        "long_ladder": [...],  # Could change between reads
        "short_ladder": [...], # Leading to inconsistent snapshot
    }
```

**Fix Required**:
```python
# Add to __init__
self._ladder_lock = threading.Lock()

# Fixed version with lock
def get_ladders_snapshot(self) -> dict:
    with self._ladder_lock:  # Ensure atomic read
        if self._last_long_ladder is None and self._last_short_ladder is None:
            return {"long_ladder": [], "short_ladder": [], "mid_price": 0.0}

        # Now snapshot is consistent
        return {
            "long_ladder": [self._ladder_to_dict(rung) for rung in self._last_long_ladder],
            "short_ladder": [self._ladder_to_dict(rung) for rung in self._last_short_ladder],
            "mid_price": self._last_mid_price
        }
```

### 3. Duplicate TP/SL Order Creation Race Condition
**Severity**: CRITICAL - Can violate exchange order limits
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:694-700`
**Problem**: Lock is taken AFTER checking set membership, creating race window

**Current Code Problem**:
```python
# strategy.py:694-700
fill_key = f"{side.value}-{level}"
# RACE WINDOW HERE - another thread could add between check and add
if fill_key in self._fills_with_exits:
    return

with self._fills_lock:
    self._fills_with_exits.add(fill_key)
```

**Fix Required**:
```python
fill_key = f"{side.value}-{level}"
with self._fills_lock:
    if fill_key in self._fills_with_exits:
        return
    # Atomic check-and-add within lock
    self._fills_with_exits.add(fill_key)
```

### 4. Decimal Precision Loss in Critical Calculations
**Severity**: CRITICAL - Can cause order rejections or wrong execution prices
**Location**:
- `naut_hedgegrid/strategy/grid.py:87-91, 145-147`
- `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:712-742`

**Problem**: Using float() conversions too early loses precision

**Current Code Problem**:
```python
# grid.py:87-91
price_decimal = mid_decimal - (Decimal(level) * price_step)
price = float(price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
# Precision lost here
```

**Fix Required**:
```python
# Keep as Decimal longer
price_decimal = (mid_decimal - (Decimal(level) * price_step)).quantize(
    Decimal("0.01"), rounding=ROUND_HALF_UP
)
# Only convert when absolutely necessary (e.g., creating Nautilus Price object)
```

### 5. Missing Error Recovery in Order Event Handlers
**Severity**: CRITICAL - Can crash strategy leaving positions unhedged
**Location**:
- `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:on_order_filled (639-806)`
- `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:on_order_rejected (856-985)`

**Problem**: No try-except blocks in critical event handlers

**Fix Required**:
```python
def on_order_filled(self, event: OrderFilled) -> None:
    try:
        # ... existing logic ...
    except Exception as e:
        self.log.error(f"Critical error in on_order_filled: {e}", exc_info=True)
        # Clean up any partial state
        with self._fills_lock:
            self._fills_with_exits.discard(fill_key)
        # Set flag to pause trading
        self._critical_error = True
        # Cancel all orders to prevent further issues
        self._cancel_all_orders()

def on_order_rejected(self, event: OrderRejected) -> None:
    try:
        # ... existing logic ...
    except Exception as e:
        self.log.error(f"Critical error in on_order_rejected: {e}", exc_info=True)
        self._handle_critical_error()
```

## Risk Management Gaps 🚨 (High Priority)

### 6. Missing Position Size Validation
**Severity**: HIGH - Can exceed account limits
**Location**: Order submission logic
**Problem**: No validation against account balance before submission

**Fix Required**:
```python
def _validate_order_size(self, order: Order) -> bool:
    """Validate order size against account balance."""
    account = self.portfolio.account(self.venue)
    if not account:
        self.log.error("No account found for position validation")
        return False

    # Check notional value
    notional = order.quantity * order.price
    free_balance = account.balance_free(self.base_currency)

    if notional > free_balance * self.max_position_pct:
        self.log.warning(f"Order notional {notional} exceeds limit")
        return False

    return True
```

### 7. No Circuit Breaker for Failures
**Severity**: HIGH - Can continue trading during system issues
**Location**: Strategy error handling
**Problem**: No automatic pause on repeated failures

**Fix Required**:
```python
# Add to __init__
self._error_count = 0
self._error_window = deque(maxlen=100)
self._max_errors_per_minute = 10
self._circuit_breaker_active = False

def _check_circuit_breaker(self) -> None:
    """Check if circuit breaker should activate."""
    now = self.clock.timestamp_ns()

    # Remove old errors outside 1-minute window
    one_minute_ago = now - 60_000_000_000
    while self._error_window and self._error_window[0] < one_minute_ago:
        self._error_window.popleft()

    # Check threshold
    if len(self._error_window) >= self._max_errors_per_minute:
        self.log.critical("Circuit breaker activated - too many errors")
        self._circuit_breaker_active = True
        self._cancel_all_orders()
        # Schedule reset after cooldown
        self.clock.set_timer_ns(
            name="circuit_breaker_reset",
            interval_ns=300_000_000_000,  # 5 minutes
            callback=self._reset_circuit_breaker
        )
```

### 8. No Max Drawdown Protection
**Severity**: MEDIUM - Can exceed risk limits
**Location**: Strategy risk management
**Problem**: No automatic position reduction on drawdown

**Fix Required**:
```python
def _check_drawdown_limit(self) -> None:
    """Check and enforce max drawdown limit."""
    account = self.portfolio.account(self.venue)
    if not account:
        return

    # Calculate current drawdown
    peak_balance = self._peak_balance  # Track this
    current_balance = float(account.balance_total(self.base_currency))
    drawdown_pct = (peak_balance - current_balance) / peak_balance * 100

    if drawdown_pct > self.max_drawdown_pct:
        self.log.critical(f"Max drawdown exceeded: {drawdown_pct:.2f}%")
        self._flatten_all_positions()
        self._pause_trading = True
```

### 9. No Automatic Position Flattening
**Severity**: MEDIUM - Positions remain open during critical errors
**Location**: Error handling
**Problem**: No emergency position closure

**Fix Required**:
```python
def _handle_critical_error(self) -> None:
    """Handle critical errors by flattening positions."""
    self.log.critical("Critical error - flattening all positions")

    # Cancel all pending orders
    self._cancel_all_orders()

    # Close all open positions
    for position in self.cache.positions_open(instrument_id=self.instrument_id):
        self._close_position_market(position)

    # Set flag to prevent new trades
    self._critical_error = True
    self._pause_trading = True
```

## Performance Improvements 🟡 (Medium Priority)

### 10. Inefficient Cache Queries in Hot Path
**Severity**: HIGH - Performance degradation
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:1306-1345`
**Problem**: O(n) iteration through all orders on every bar

**Current Code**:
```python
def _get_live_grid_orders(self) -> list[LiveOrder]:
    # Iterates all orders every time
    for order in self.cache.orders_open(instrument_id=self.instrument_id):
        # Parse and check each one
```

**Fix Required**:
```python
# Add to __init__
self._grid_orders_by_level: dict[tuple[Side, int], LiveOrder] = {}

# Update on order events
def on_order_accepted(self, event: OrderAccepted) -> None:
    order = self.cache.order(event.client_order_id)
    parsed = self._parse_client_order_id(order.client_order_id.value)
    if parsed and parsed.order_type == "GRID":
        key = (parsed.side, parsed.level)
        self._grid_orders_by_level[key] = LiveOrder(...)

def on_order_canceled(self, event: OrderCanceled) -> None:
    # Remove from dict

def _get_live_grid_orders(self) -> list[LiveOrder]:
    return list(self._grid_orders_by_level.values())  # O(1)
```

### 11. Repeated Parsing of Order IDs
**Severity**: MEDIUM - Unnecessary CPU usage
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:1292-1304`
**Problem**: parse_client_order_id called multiple times for same ID

**Fix Required**:
```python
# Store parsed metadata with order tracking
class TrackedOrder:
    order: Order
    parsed_id: Optional[ParsedOrderId]

# Parse once on order acceptance and store
```

### 12. Blocking Warmup in Strategy Initialization
**Severity**: MEDIUM - Blocks event loop startup
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:249-380`
**Problem**: Synchronous HTTP calls in on_start()

**Fix Required**:
```python
# Make warmup async
async def _perform_warmup_async(self) -> None:
    async with aiohttp.ClientSession() as session:
        # Fetch data asynchronously
        tasks = [
            self._fetch_bars_async(session),
            self._fetch_funding_async(session)
        ]
        await asyncio.gather(*tasks)

# Call from on_start
def on_start(self) -> None:
    # Schedule async warmup
    asyncio.create_task(self._perform_warmup_async())
```

## Code Quality Issues 🔵 (Medium Priority)

### 13. Incorrect Event Loop Timer Usage
**Severity**: MEDIUM - Misuse of NautilusTrader API
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:969-981`
**Problem**: Using set_time_alert_ns for delays instead of set_timer_ns

**Current Code**:
```python
# Incorrect - alert is for absolute time
alert_time_ns = self.clock.timestamp_ns() + delay_ns
self.clock.set_time_alert_ns(name=f"retry_{client_order_id}", ...)
```

**Fix Required**:
```python
# Correct - timer for delays
self.clock.set_timer_ns(
    name=f"retry_{client_order_id}",
    interval_ns=delay_ns,
    callback=retry_callback,
    start=True,
    stop_after=1  # One-shot timer
)
```

### 14. Magic Numbers Throughout Code
**Severity**: LOW - Maintainability issue
**Location**: Multiple locations
**Problem**: Hard-coded values without named constants

**Fix Required**:
```python
# Add to strategy.py or create constants.py
class StrategyConstants:
    BINANCE_MAX_ORDER_ID_LENGTH = 36
    DIAGNOSTIC_LOG_INTERVAL_NS = 300_000_000_000  # 5 minutes
    NANOSECONDS_PER_MILLISECOND = 1_000_000
    DEFAULT_RETRY_DELAY_MS = 100
    MAX_RETRY_ATTEMPTS = 3
    WARMUP_TIMEOUT_SECONDS = 30
```

### 15. Missing Type Hints on Complex Returns
**Severity**: LOW - Type safety issue
**Location**: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`
**Problem**: Dict returns lack TypedDict definitions

**Fix Required**:
```python
from typing import TypedDict

class OperationalMetrics(TypedDict):
    long_inventory_usdt: float
    short_inventory_usdt: float
    total_inventory_usdt: float
    net_position_usdt: float
    long_orders: int
    short_orders: int
    total_orders: int

class LadderSnapshot(TypedDict):
    long_ladder: list[dict]
    short_ladder: list[dict]
    mid_price: float

# Update method signatures
def get_operational_metrics(self) -> OperationalMetrics:
    ...

def get_ladders_snapshot(self) -> LadderSnapshot:
    ...
```

## Testing Requirements 📝

### 16. Add Thread Safety Tests
```python
# test_thread_safety.py
import threading
import time

def test_concurrent_ladder_snapshot():
    """Test that ladder snapshots are thread-safe."""
    strategy = create_test_strategy()

    def read_snapshots():
        for _ in range(1000):
            snapshot = strategy.get_ladders_snapshot()
            # Verify consistency

    threads = [threading.Thread(target=read_snapshots) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
```

### 17. Add Error Recovery Tests
```python
def test_order_filled_error_recovery():
    """Test that strategy recovers from errors in on_order_filled."""
    strategy = create_test_strategy()

    # Mock an error
    with patch.object(strategy, '_attach_tp_sl', side_effect=Exception("Test error")):
        event = create_order_filled_event()
        strategy.on_order_filled(event)

    # Verify strategy is still operational
    assert not strategy._critical_error
    assert strategy._fills_with_exits == set()  # Cleaned up
```

## Implementation Priority

1. **Week 1 - Critical Fixes**
   - [ ] Fix thread safety violations (#2, #3)
   - [ ] Add error recovery to event handlers (#5)
   - [ ] Fix Decimal precision loss (#4)
   - [ ] Fix TP order price precision (#1)

2. **Week 2 - Risk & Performance**
   - [ ] Add position size validation (#6)
   - [ ] Implement circuit breaker (#7)
   - [ ] Add max drawdown protection (#8)
   - [ ] Optimize cache queries (#10)

3. **Week 3 - Best Practices**
   - [ ] Make warmup async (#12)
   - [ ] Fix timer usage (#13)
   - [ ] Replace magic numbers (#14)
   - [ ] Add TypedDict definitions (#15)

4. **Week 4 - Testing & Validation**
   - [ ] Add thread safety tests (#16)
   - [ ] Add error recovery tests (#17)
   - [ ] Performance profiling
   - [ ] Load testing with high order volumes

## Verification Checklist

After implementing fixes, verify:
- [ ] No race conditions in concurrent access tests
- [ ] Error recovery works for all event handlers
- [ ] Decimal precision maintained throughout calculations
- [ ] Performance metrics show O(1) order lookups
- [ ] Circuit breaker activates on error threshold
- [ ] Position sizes validated before submission
- [ ] Max drawdown protection triggers correctly
- [ ] All magic numbers replaced with constants
- [ ] Type hints pass mypy strict mode
- [ ] 100% test coverage for critical paths

## Notes

- Test all fixes in paper trading mode first
- Monitor performance metrics after optimization
- Consider gradual rollout with position size limits
- Keep audit log of all critical errors for analysis
- Review exchange API limits and adjust accordingly
