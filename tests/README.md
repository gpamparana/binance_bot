# Test Suite Documentation

Comprehensive testing guide for the naut-hedgegrid trading system.

## Overview

The test suite validates all aspects of the trading system with 248+ passing tests covering:
- Strategy components (grid engine, regime detection, policy)
- Integration with NautilusTrader
- Backtest runner functionality
- Operational controls (kill switch, alerts, metrics)

## Quick Start

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test category
uv run pytest tests/strategy/ -v
uv run pytest tests/ops/ -v
uv run pytest tests/integration/ -v

# Run with coverage
uv run pytest tests/ --cov=src/naut_hedgegrid --cov-report=html

# Run single test file
uv run pytest tests/strategy/test_grid.py -v
```

## Test Organization

```
tests/
├── unit/                    # Component-level tests
│   ├── test_detector.py     # Regime detection
│   ├── test_grid.py         # Grid engine
│   ├── test_policy.py       # Placement policy
│   └── test_funding.py      # Funding guard
├── integration/             # Multi-component tests
│   └── test_parity.py       # Backtest vs paper parity
├── strategy/                # Strategy smoke tests
│   ├── test_strategy_smoke.py  # End-to-end strategy tests
│   └── conftest.py          # Test fixtures
└── ops/                     # Operational controls tests
    ├── test_kill_switch.py  # Kill switch tests
    ├── test_alerts.py       # Alert system tests
    └── test_prometheus.py   # Metrics tests
```

## Test Categories

### 1. Component Tests (Unit)

**Purpose**: Validate individual components in isolation

**Key test files**:
- `test_grid.py` - Grid ladder generation
- `test_detector.py` - Regime detection (EMA, ADX, ATR)
- `test_policy.py` - Placement policy logic
- `test_funding.py` - Funding rate adjustments
- `test_order_sync.py` - Order diff engine

**Run**:
```bash
pytest tests/unit/ -v
```

### 2. Strategy Smoke Tests

**Purpose**: Validate end-to-end strategy behavior with NautilusTrader integration

**Test coverage** (30 tests):
- Initialization (4 tests)
- Bar processing (5 tests)
- Order generation (3 tests)
- Order lifecycle (4 tests)
- Diff engine (3 tests)
- Regime changes (2 tests)
- Edge cases (6 tests)
- Integration (2 tests)

**Critical tests**:
- `test_position_side_suffixes_long` - LONG position IDs (hedge mode)
- `test_position_side_suffixes_short` - SHORT position IDs (hedge mode)
- `test_on_order_filled_attaches_tp_sl_long` - TP/SL attachment for LONG
- `test_on_order_filled_attaches_tp_sl_short` - TP/SL attachment for SHORT

**Run**:
```bash
pytest tests/strategy/test_strategy_smoke.py -v

# Run specific category
pytest tests/strategy/ -k "initialization" -v
pytest tests/strategy/ -k "position_side" -v
```

### 3. Integration Tests

**Purpose**: Test multi-component interactions and parity

**Key tests**:
- `test_parity.py` - Ensures backtest and paper trading produce consistent results

**Run**:
```bash
pytest tests/integration/ -v
```

### 4. Operational Controls Tests

**Purpose**: Validate kill switch, alerts, and monitoring

**Test coverage** (72 tests):
- Kill switch tests (27 tests) - Circuit breakers, position flattening
- Alert tests (25 tests) - Slack, Telegram notifications
- Prometheus tests (20 tests) - Metrics export

**Run**:
```bash
pytest tests/ops/ -v
```

## Test Fixtures

### Available Fixtures

**Instruments**:
- `test_instrument` - CryptoPerpetual with realistic precision (0.01 tick, 0.001 step, 5.0 min notional)

**Configurations**:
- `hedge_grid_config_path` - Temporary HedgeGridConfig YAML
- `strategy_config` - HedgeGridV1Config instance

**Strategy**:
- `strategy` - Mocked HedgeGridV1 with test harness

**Bars**:
- `create_test_bar()` - Helper to create Bar instances

### Using Fixtures

```python
def test_my_feature(test_instrument, strategy_config):
    # Use fixtures in your test
    assert test_instrument.price_precision == 2
    assert strategy_config.instrument_id == "BTCUSDT-PERP.BINANCE"
```

## Testing Best Practices

### 1. Property-Based Testing

Use Hypothesis for edge case discovery:

```python
from hypothesis import given
import hypothesis.strategies as st

@given(st.floats(min_value=1.0, max_value=100000.0))
def test_grid_prices_always_positive(mid: float):
    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    assert all(rung.price > 0 for ladder in ladders for rung in ladder.rungs)
```

### 2. Mocking NautilusTrader Components

```python
from unittest.mock import Mock

# Mock strategy dependencies
strategy.cache = Mock()
strategy.cache.instrument.return_value = test_instrument
strategy.submit_order = Mock()
strategy.cancel_order = Mock()
```

### 3. Testing Position IDs (Hedge Mode)

```python
def test_position_id_format(strategy):
    # LONG orders
    long_order = strategy._create_order(Side.LONG, price=50000, qty=0.001)
    assert str(long_order.position_id).endswith("-LONG")

    # SHORT orders
    short_order = strategy._create_order(Side.SHORT, price=50000, qty=0.001)
    assert str(short_order.position_id).endswith("-SHORT")
```

### 4. Testing TP/SL Attachment

```python
def test_tp_sl_attached_on_fill(strategy):
    # Simulate fill event
    fill_event = create_fill_event(side=OrderSide.BUY, price=50000, qty=0.001)
    strategy.on_order_filled(fill_event)

    # Verify TP and SL orders submitted
    assert strategy.submit_order.call_count == 2
    tp_order, sl_order = strategy.submit_order.call_args_list

    # Validate TP
    assert tp_order.reduce_only == True
    assert tp_order.side == OrderSide.SELL  # Opposite of entry

    # Validate SL
    assert sl_order.reduce_only == True
    assert sl_order.order_type == OrderType.STOP_MARKET
```

## Continuous Integration

### Pre-Commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
pytest tests/strategy/test_strategy_smoke.py --tb=short
if [ $? -ne 0 ]; then
    echo "Smoke tests failed. Commit aborted."
    exit 1
fi
```

### GitHub Actions

```yaml
# .github/workflows/tests.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install uv
      - run: uv sync --all-extras
      - run: uv run pytest tests/ -v --cov=src/naut_hedgegrid
```

## Test Status

### Current Status (2025-10-14)

- Core component tests: 248 passing
- Strategy smoke tests: 30 passing
- Operational controls tests: 72 passing
- **Total**: 350+ tests passing

### Known Issues

None currently. All critical paths covered and passing.

## Performance Benchmarks

| Test Suite | Runtime | Tests |
|------------|---------|-------|
| Component tests | < 5s | 248 |
| Strategy smoke tests | < 30s | 30 |
| Operational tests | < 15s | 72 |
| **Full suite** | **< 60s** | **350+** |

## Troubleshooting

### Test Failures

**"Strategy component not initialized"**
- Ensure `on_start()` properly initializes all components
- Verify HedgeGridConfig loaded successfully

**"Position ID suffix incorrect"**
- Check hedge mode enabled (OmsType.HEDGING)
- Verify position_id includes `-LONG` or `-SHORT` suffix

**"TP/SL not attached on fill"**
- Implement `on_order_filled()` handler
- Check TP/SL price calculations from config

**"Diff generates unnecessary operations"**
- Check tolerance values in OrderMatcher
- Verify precision clamping consistency

### Debug Mode

```bash
# Run with detailed output
pytest tests/strategy/ -vv --tb=long

# Interactive debugging
pytest tests/strategy/test_strategy_smoke.py::test_name --pdb

# Show print statements
pytest tests/strategy/ -v -s
```

## Coverage Goals

### Minimum Coverage: 80%

```bash
pytest tests/ --cov=src/naut_hedgegrid --cov-report=term-missing --cov-fail-under=80
```

### Coverage Report

```bash
# Generate HTML coverage report
pytest tests/ --cov=src/naut_hedgegrid --cov-report=html

# Open in browser
open htmlcov/index.html
```

## Next Steps

After all tests pass:

1. Run extended paper trading validation
2. Execute backtests with historical data
3. Deploy to testnet environment
4. Monitor for 24-48 hours
5. Gradual production rollout

## Support

For testing questions or issues:
- Review test failure messages (often include hints)
- Check test file docstrings for detailed explanations
- Examine passing tests as examples
- Use `pytest --pdb` for interactive debugging
- Consult NautilusTrader testing documentation

## References

- [NautilusTrader Testing Guide](https://nautilustrader.io/docs/latest/tutorials/strategies/)
- [pytest Documentation](https://docs.pytest.org/)
- [Hypothesis Documentation](https://hypothesis.readthedocs.io/)
- [Main README](../README.md)
- [CLAUDE.md](../CLAUDE.md)
