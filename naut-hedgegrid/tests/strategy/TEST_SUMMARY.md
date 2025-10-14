# HedgeGridV1 Strategy Smoke Tests - Summary

## Test Suite Overview

**File**: `tests/strategy/test_strategy_smoke.py`
**Lines of Code**: 1,150
**Number of Tests**: 30
**Coverage Target**: End-to-end strategy behavior

## Test Statistics

| Category | Count | Purpose |
|----------|-------|---------|
| Initialization | 4 | Strategy creation and component setup |
| Bar Processing | 5 | Bar handling and regime detection |
| Order Generation | 3 | Order creation and position IDs |
| Order Lifecycle | 4 | Order state tracking (accept/fill/cancel) |
| Diff Engine | 3 | Minimal operation generation |
| Regime Changes | 2 | Ladder adjustment on regime transitions |
| Funding Adjustments | 1 | Quantity reduction near funding |
| Edge Cases | 6 | Error handling and validation |
| Integration | 2 | Full lifecycle smoke tests |

## Quick Verification Checklist

Use this checklist to verify HedgeGridV1 implementation:

### Phase 1: Basic Structure
- [ ] Strategy class inherits from `nautilus_trader.trading.strategy.Strategy`
- [ ] `__init__` accepts `HedgeGridV1Config`
- [ ] `on_start()` method loads config and initializes components
- [ ] `on_stop()` method cleans up resources

### Phase 2: Component Initialization
- [ ] RegimeDetector initialized with config params
- [ ] GridEngine instance created
- [ ] PlacementPolicy initialized with policy config
- [ ] FundingGuard initialized with funding config
- [ ] PrecisionGuard initialized with instrument
- [ ] OrderDiff initialized with strategy name
- [ ] Instrument loaded from cache
- [ ] Bar subscription registered

### Phase 3: Bar Processing
- [ ] `on_bar()` updates RegimeDetector
- [ ] `_last_mid` tracks current mid price
- [ ] Order generation skipped before detector warmup
- [ ] Orders generated after detector warm
- [ ] Diff engine generates minimal operations

### Phase 4: Position IDs (Critical for Hedge Mode)
- [ ] LONG orders use `position_id = "{instrument_id}-LONG"`
- [ ] SHORT orders use `position_id = "{instrument_id}-SHORT"`
- [ ] All grid orders include correct suffix
- [ ] TP/SL orders use same position_id as parent

### Phase 5: Order Lifecycle
- [ ] `on_order_accepted()` adds order to `_live_orders` tracking
- [ ] `on_order_filled()` triggers TP/SL attachment
- [ ] `on_order_canceled()` removes order from tracking
- [ ] Client order IDs follow format: `{strategy}-{side}-{level:02d}-{timestamp}`

### Phase 6: TP/SL Attachment
- [ ] TP order: Limit, reduce-only, correct side (opposite of entry)
- [ ] SL order: Stop-market, reduce-only, correct side (opposite of entry)
- [ ] TP price: entry + (tp_steps * grid_step) for LONG, entry - (tp_steps * grid_step) for SHORT
- [ ] SL price: entry - (sl_steps * grid_step) for LONG, entry + (sl_steps * grid_step) for SHORT
- [ ] Quantities match filled quantity
- [ ] Both orders submitted immediately after fill

### Phase 7: Regime Adaptation
- [ ] UP regime: SHORT ladder active, LONG throttled/removed
- [ ] DOWN regime: LONG ladder active, SHORT throttled/removed
- [ ] SIDEWAYS regime: Both ladders active
- [ ] PlacementPolicy applies counter-trend throttling
- [ ] Regime transitions handled without crashes

### Phase 8: Precision Guards
- [ ] Prices clamped to tick increment
- [ ] Quantities clamped to step size
- [ ] Min notional enforced (orders below minimum filtered)
- [ ] Min/max quantity limits respected
- [ ] Invalid rungs filtered before submission

### Phase 9: Error Handling
- [ ] Missing instrument logged, strategy doesn't crash
- [ ] Invalid bar data validated and rejected
- [ ] Empty diff generates no operations
- [ ] Strategy stops cleanly

### Phase 10: Integration
- [ ] Full lifecycle test passes (init → warmup → orders → fills → cleanup)
- [ ] Regime transition test passes (sideways → up → order adjustments)

## Running Verification Tests

### Quick smoke test (1 minute):
```bash
pytest tests/strategy/test_strategy_smoke.py::test_strategy_initialization -v
pytest tests/strategy/test_strategy_smoke.py::test_on_start_loads_config -v
pytest tests/strategy/test_strategy_smoke.py::test_position_side_suffixes_long -v
pytest tests/strategy/test_strategy_smoke.py::test_position_side_suffixes_short -v
```

### Core functionality (5 minutes):
```bash
pytest tests/strategy/test_strategy_smoke.py -k "initialization or position_side or order_filled" -v
```

### Full smoke test (< 30 seconds):
```bash
pytest tests/strategy/test_strategy_smoke.py -v
```

### With coverage report:
```bash
pytest tests/strategy/test_strategy_smoke.py \
    --cov=naut_hedgegrid.strategies.hedge_grid_v1 \
    --cov-report=term-missing \
    --cov-report=html
```

## Expected Test Results

### All tests passing:
```
tests/strategy/test_strategy_smoke.py::test_strategy_initialization PASSED                [ 3%]
tests/strategy/test_strategy_smoke.py::test_strategy_instrument_id_parsed PASSED          [ 6%]
tests/strategy/test_strategy_smoke.py::test_on_start_loads_config PASSED                  [ 10%]
tests/strategy/test_strategy_smoke.py::test_on_start_missing_instrument_logs_error PASSED [ 13%]
tests/strategy/test_strategy_smoke.py::test_on_bar_first_call_initializes_state PASSED    [ 16%]
tests/strategy/test_strategy_smoke.py::test_on_bar_updates_regime_detector PASSED         [ 20%]
tests/strategy/test_strategy_smoke.py::test_on_bar_generates_orders_after_warmup PASSED   [ 23%]
... [27 more tests] ...
tests/strategy/test_strategy_smoke.py::test_full_lifecycle_regime_transition PASSED       [100%]

============================== 30 passed in 25.34s ===============================
```

## Critical Test Cases

### Must Pass (Blocking Issues):
1. `test_strategy_initialization` - Strategy creation
2. `test_on_start_loads_config` - Component initialization
3. `test_position_side_suffixes_long` - LONG position IDs
4. `test_position_side_suffixes_short` - SHORT position IDs
5. `test_on_order_filled_attaches_tp_sl_long` - LONG TP/SL
6. `test_on_order_filled_attaches_tp_sl_short` - SHORT TP/SL

### Should Pass (Important):
7. `test_on_bar_generates_orders_after_warmup` - Order generation
8. `test_order_accepted_tracked` - Order tracking
9. `test_order_canceled_removed` - Order cleanup
10. `test_diff_generates_minimal_operations` - Diff efficiency

### Nice to Have (Enhancement):
11. `test_regime_change_adjusts_ladders_up_to_sideways` - Regime adaptation
12. `test_funding_adjustment_reduces_qty` - Funding optimization
13. `test_full_lifecycle_sideways_regime` - Integration test
14. `test_full_lifecycle_regime_transition` - Full workflow

## Common Implementation Pitfalls

### 1. Position ID Format
**Wrong**: `position_id = "LONG"` or `position_id = instrument_id`
**Right**: `position_id = f"{instrument_id}-LONG"` → `"BTCUSDT-PERP.BINANCE-LONG"`

### 2. TP/SL Reduce-Only Flag
**Wrong**: Regular limit/stop order without reduce-only
**Right**: `reduce_only=True` in order factory call

### 3. Order Tracking Synchronization
**Wrong**: Not updating `_live_orders` on accept/cancel
**Right**: Track in `on_order_accepted`, remove in `on_order_canceled`

### 4. Detector Warmup Check
**Wrong**: Generating orders before detector warm
**Right**: `if not self._detector.is_warm: return`

### 5. Client Order ID Format
**Wrong**: Simple counter or random UUID
**Right**: `f"{strategy}-{side}-{level:02d}-{timestamp}"`

## Test Fixtures Available

### Instruments
- `test_instrument`: CryptoPerpetual with 0.01 tick, 0.001 step, 5.0 min notional

### Configurations
- `hedge_grid_config_path`: Temporary YAML config file
- `strategy_config`: HedgeGridV1Config instance

### Strategy
- `strategy`: Mocked HedgeGridV1 with test harness

### Test Data Helpers
- `create_test_bar()`: Create Bar instances for testing

## File Locations

```
naut-hedgegrid/
├── tests/
│   └── strategy/
│       ├── test_strategy_smoke.py          # Main test file (1,150 lines, 30 tests)
│       ├── README_SMOKE_TESTS.md           # Detailed test documentation
│       ├── SMOKE_TEST_GUIDE.md             # Developer workflow guide
│       └── TEST_SUMMARY.md                 # This file
├── src/
│   └── naut_hedgegrid/
│       └── strategies/
│           └── hedge_grid_v1/
│               ├── __init__.py
│               ├── config.py               # HedgeGridV1Config
│               └── strategy.py             # HedgeGridV1 (to be implemented)
└── pytest.ini  # Pytest configuration
```

## Next Steps After Smoke Tests Pass

1. **Component Unit Tests**: Run `pytest tests/strategy/test_detector.py tests/strategy/test_grid.py` etc.
2. **Integration Tests**: Test with real historical data
3. **Backtest Validation**: Run strategy in backtest mode
4. **Testnet Deployment**: Deploy to Binance testnet
5. **Paper Trading**: Monitor behavior with paper account
6. **Production Deployment**: Gradual rollout with monitoring

## Performance Expectations

| Metric | Target | Notes |
|--------|--------|-------|
| Test suite runtime | < 30s | All 30 tests |
| Single test runtime | < 2s | Including 60+ bar warmup |
| Code coverage | > 80% | Strategy core paths |
| Memory usage | < 100MB | During test execution |
| Order generation latency | < 100ms | Per bar processing |

## Troubleshooting Resources

1. **Test output messages**: Often contain hints about failures
2. **README_SMOKE_TESTS.md**: Detailed descriptions of each test
3. **SMOKE_TEST_GUIDE.md**: Step-by-step debugging workflow
4. **Component tests**: Check individual component tests for examples
5. **NautilusTrader docs**: https://nautilustrader.io/docs/
6. **Pytest docs**: https://docs.pytest.org/

## Success Criteria

Strategy implementation is ready for integration testing when:
- [ ] All 30 smoke tests pass
- [ ] No warnings or errors in test output
- [ ] Code coverage > 80% for strategy module
- [ ] Tests run in < 30 seconds
- [ ] Position IDs correctly formatted (hedge mode)
- [ ] TP/SL orders attached on all fills
- [ ] Order tracking synchronized
- [ ] Regime transitions handled gracefully
- [ ] Error cases handled without crashes

## Support

For questions or issues:
- Review test failure messages (usually include diagnostic info)
- Check SMOKE_TEST_GUIDE.md for debugging workflows
- Examine passing component tests for examples
- Use `pytest --pdb` for interactive debugging
- Check Nautilus test kit for mock object examples

---

**Last Updated**: 2025-10-13
**Test Framework**: pytest >= 7.0.0
**Strategy Version**: HedgeGridV1
**Nautilus Version**: >= 1.190.0
