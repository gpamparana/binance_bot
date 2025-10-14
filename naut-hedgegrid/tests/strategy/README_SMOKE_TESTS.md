# HedgeGridV1 Strategy Smoke Tests

## Overview

The `test_strategy_smoke.py` file contains comprehensive smoke tests for the HedgeGridV1 NautilusTrader strategy. These tests validate end-to-end behavior and integration of all strategy components.

## Test Coverage

### 1. Initialization Tests (4 tests)
- **test_strategy_initialization**: Validates basic strategy creation
- **test_strategy_instrument_id_parsed**: Checks instrument ID parsing
- **test_on_start_loads_config**: Verifies component initialization
- **test_on_start_missing_instrument_logs_error**: Tests error handling

### 2. Bar Processing Tests (5 tests)
- **test_on_bar_first_call_initializes_state**: First bar handling
- **test_on_bar_updates_regime_detector**: Detector state updates
- **test_on_bar_generates_orders_after_warmup**: Order generation after warmup
- **test_on_bar_skips_generation_if_detector_not_warm**: Pre-warmup behavior
- **test_on_bar_updates_last_mid**: Mid price tracking

### 3. Order Generation and Position Side Tests (3 tests)
- **test_position_side_suffixes_long**: LONG orders use `-LONG` position_id
- **test_position_side_suffixes_short**: SHORT orders use `-SHORT` position_id
- **test_orders_use_correct_client_order_id_format**: Client order ID format validation

### 4. Order Lifecycle Tests (4 tests)
- **test_order_accepted_tracked**: Order tracking after acceptance
- **test_order_canceled_removed**: Order removal after cancellation
- **test_on_order_filled_attaches_tp_sl_long**: TP/SL attachment for LONG positions
- **test_on_order_filled_attaches_tp_sl_short**: TP/SL attachment for SHORT positions

### 5. Diff Engine and Minimal Churn Tests (3 tests)
- **test_diff_generates_minimal_operations**: No unnecessary operations when state unchanged
- **test_diff_adds_orders_when_needed**: Order addition when desired state expands
- **test_diff_cancels_stale_orders**: Stale order cancellation

### 6. Regime Change Tests (2 tests)
- **test_regime_change_adjusts_ladders_up_to_sideways**: Ladder adjustment on regime transition
- **test_regime_change_throttles_counter_ladder**: Counter-trend throttling per policy

### 7. Funding Adjustment Tests (1 test)
- **test_funding_adjustment_reduces_qty**: Quantity reduction near funding time

### 8. Edge Cases and Error Handling (6 tests)
- **test_handles_zero_bar_data_gracefully**: Invalid bar data handling
- **test_handles_invalid_bar_high_low**: Bar validation
- **test_empty_diff_no_operations**: Empty diff optimization
- **test_precision_guard_filters_invalid_rungs**: Min notional filtering
- **test_strategy_stops_cleanly**: Clean shutdown
- **test_full_lifecycle_sideways_regime**: Complete lifecycle integration test
- **test_full_lifecycle_regime_transition**: Regime transition integration test

## Running the Tests

### Run all smoke tests:
```bash
pytest tests/strategy/test_strategy_smoke.py -v
```

### Run specific test:
```bash
pytest tests/strategy/test_strategy_smoke.py::test_strategy_initialization -v
```

### Run with coverage:
```bash
pytest tests/strategy/test_strategy_smoke.py --cov=naut_hedgegrid.strategies.hedge_grid_v1 --cov-report=term-missing
```

### Run in parallel (if pytest-xdist installed):
```bash
pytest tests/strategy/test_strategy_smoke.py -n auto
```

## Test Requirements

### Dependencies:
- pytest >= 7.0.0
- nautilus_trader >= 1.190.0
- unittest.mock (standard library)

### Test Fixtures:
- **test_instrument**: CryptoPerpetual with realistic precision (0.01 tick, 0.001 step, 5.0 min notional)
- **hedge_grid_config_path**: Temporary HedgeGridConfig YAML with test parameters
- **strategy_config**: HedgeGridV1Config linked to test config file
- **strategy**: Mocked HedgeGridV1 instance with test harness

### Mock Strategy:
Tests use mocked dependencies:
- `cache.instrument()` returns test_instrument
- `submit_order()` captures order submissions
- `cancel_order()` captures cancellations
- `clock.timestamp_ns()` returns controlled timestamps
- `portfolio`, `log` mocked for isolation

## Key Validation Points

### Position Management (Hedge Mode):
- All LONG orders must use `position_id = "{instrument_id}-LONG"`
- All SHORT orders must use `position_id = "{instrument_id}-SHORT"`
- Required for Binance futures hedge mode

### TP/SL Orders:
- Attached on every grid order fill
- TP: Limit order, reduce-only, at TP price
- SL: Stop-market order, reduce-only, at SL trigger
- Both use same position_id as entry order

### Client Order IDs:
- Format: `{strategy}-{side}-{level:02d}-{timestamp}`
- Example: `HG1-LONG-05-1700000000000`
- Used for order tracking and diff matching

### Order Diff Engine:
- Minimizes operations via tolerance-based matching
- Price tolerance: 1 bps (0.01%)
- Quantity tolerance: 1% (0.01)
- Generates empty diff when state unchanged

### Regime Detection:
- EMA crossover: Fast EMA vs Slow EMA
- Trend strength: ADX threshold (20.0)
- Hysteresis: Prevents rapid regime flipping
- Warmup: Slow EMA period * 2 bars minimum

### Precision Guards:
- Price clamped to tick increment (0.01)
- Quantity clamped to step size (0.001)
- Min notional enforced (5.0 USDT)
- Invalid rungs filtered before submission

## Expected Behavior

### Initialization:
1. Load HedgeGridConfig from YAML
2. Initialize all components (detector, engine, policy, guards)
3. Subscribe to bar data and order events
4. Set up internal tracking structures

### Bar Processing:
1. Update RegimeDetector with bar OHLC
2. Calculate current mid price
3. Check if detector is warm (skip order generation if not)
4. Determine current regime (UP/DOWN/SIDEWAYS)
5. Build desired ladders via GridEngine + PlacementPolicy
6. Apply FundingGuard adjustments (if near funding time)
7. Generate diff: desired vs live orders
8. Submit new orders, cancel stale orders, replace mismatched orders
9. Update last_mid for re-centering checks

### Order Fill:
1. Receive OrderFilled event
2. Extract fill price, quantity, side
3. Calculate TP price (tp_steps from fill price)
4. Calculate SL price (sl_steps from fill price)
5. Submit TP limit order (reduce-only, correct position_id)
6. Submit SL stop-market order (reduce-only, correct position_id)

### Regime Change:
1. Detector signals new regime
2. PlacementPolicy adjusts ladder composition:
   - UP: Favor SHORT ladder, throttle/remove LONG
   - DOWN: Favor LONG ladder, throttle/remove SHORT
   - SIDEWAYS: Both ladders active
3. Diff engine cancels obsolete orders
4. New orders submitted per adjusted ladders

## Implementation Notes

### Strategy Class Structure (Expected):
```python
class HedgeGridV1(Strategy):
    def __init__(self, config: HedgeGridV1Config):
        # Initialize base strategy

    def on_start(self):
        # Load config, initialize components, subscribe

    def on_bar(self, bar: Bar):
        # Update detector, generate/sync orders

    def on_order_accepted(self, event: OrderAccepted):
        # Track order in _live_orders

    def on_order_filled(self, event: OrderFilled):
        # Attach TP/SL orders

    def on_order_canceled(self, event: OrderCanceled):
        # Remove from _live_orders tracking

    def on_stop(self):
        # Clean up resources
```

### Internal State:
- `_detector`: RegimeDetector instance
- `_grid_engine`: GridEngine instance
- `_policy`: PlacementPolicy instance
- `_funding_guard`: FundingGuard instance
- `_precision_guard`: PrecisionGuard instance
- `_order_diff`: OrderDiff instance
- `_hedge_config`: Loaded HedgeGridConfig
- `_last_mid`: Last mid price for re-centering
- `_live_orders`: Dict[ClientOrderId, LiveOrder] for tracking

### Position ID Format:
```python
# For LONG positions
position_id = f"{instrument_id}-LONG"  # e.g., "BTCUSDT-PERP.BINANCE-LONG"

# For SHORT positions
position_id = f"{instrument_id}-SHORT"  # e.g., "BTCUSDT-PERP.BINANCE-SHORT"
```

## Troubleshooting

### Test Failures:

**"Strategy component not initialized"**
- Ensure on_start() properly initializes all components
- Check HedgeGridConfig loaded successfully
- Verify instrument found in cache

**"Position ID suffix incorrect"**
- Check order creation uses correct position_id
- Verify hedge mode enabled (OmsType.HEDGING)
- Ensure suffixes match instrument_id exactly

**"TP/SL not attached on fill"**
- Verify on_order_filled handler implemented
- Check TP/SL price calculations from config
- Ensure reduce-only flag set correctly

**"Diff generates unnecessary operations"**
- Check tolerance values in OrderMatcher
- Verify live order tracking synchronized
- Ensure precision clamping consistent

**"Detector not warm"**
- Feed enough bars for warmup (slow_ema_period * 2)
- Check bar data validity (high >= low, etc.)
- Verify all indicators initialized

### Common Issues:

1. **Mocking Problems**: Ensure all Nautilus objects properly mocked
2. **Timing Issues**: Use controlled clock timestamps, not system time
3. **Floating Point**: Use pytest.approx() for price/quantity comparisons
4. **Order Tracking**: Synchronize _live_orders with order events

## Future Enhancements

### Additional Tests:
- [ ] Position risk limit enforcement
- [ ] Emergency stop on excessive drawdown
- [ ] Websocket connection handling
- [ ] Order retry logic (POST_ONLY rejections)
- [ ] Inventory rebalancing near max limits
- [ ] Liquidation buffer validation
- [ ] Multi-instrument support (if applicable)

### Performance Tests:
- [ ] Benchmark order diff performance (10k orders)
- [ ] Memory usage profiling
- [ ] Bar processing latency
- [ ] Order submission rate limits

### Stress Tests:
- [ ] Flash crash simulation
- [ ] Network timeout handling
- [ ] Exchange rejection handling
- [ ] Concurrent order updates

## References

- [NautilusTrader Documentation](https://nautilustrader.io/)
- [Binance Futures API - Hedge Mode](https://binance-docs.github.io/apidocs/futures/en/#change-position-mode-trade)
- [pytest Documentation](https://docs.pytest.org/)
- [Strategy Testing Best Practices](https://nautilustrader.io/docs/latest/tutorials/strategies/)
