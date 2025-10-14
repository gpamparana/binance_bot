"""Tests for exchange precision guards."""

import pytest

from naut_hedgegrid.domain.types import Rung, Side
from naut_hedgegrid.exchange.precision import InstrumentPrecision, PrecisionGuard

# Fixtures


def create_test_precision(
    price_tick: float = 0.01,
    qty_step: float = 0.001,
    min_notional: float = 5.0,
    min_qty: float = 0.001,
    max_qty: float = 1000.0,
) -> InstrumentPrecision:
    """Create test instrument precision."""
    return InstrumentPrecision(
        price_tick=price_tick,
        qty_step=qty_step,
        min_notional=min_notional,
        min_qty=min_qty,
        max_qty=max_qty,
    )


# InstrumentPrecision Tests


def test_instrument_precision_creation() -> None:
    """Test InstrumentPrecision creation with valid parameters."""
    precision = create_test_precision()

    assert precision.price_tick == 0.01
    assert precision.qty_step == 0.001
    assert precision.min_notional == 5.0
    assert precision.min_qty == 0.001
    assert precision.max_qty == 1000.0


def test_instrument_precision_invalid_price_tick() -> None:
    """Test InstrumentPrecision rejects non-positive price_tick."""
    with pytest.raises(ValueError, match="price_tick must be positive"):
        create_test_precision(price_tick=0.0)

    with pytest.raises(ValueError, match="price_tick must be positive"):
        create_test_precision(price_tick=-0.01)


def test_instrument_precision_invalid_qty_step() -> None:
    """Test InstrumentPrecision rejects non-positive qty_step."""
    with pytest.raises(ValueError, match="qty_step must be positive"):
        create_test_precision(qty_step=0.0)

    with pytest.raises(ValueError, match="qty_step must be positive"):
        create_test_precision(qty_step=-0.001)


def test_instrument_precision_invalid_min_notional() -> None:
    """Test InstrumentPrecision rejects negative min_notional."""
    with pytest.raises(ValueError, match="min_notional must be non-negative"):
        create_test_precision(min_notional=-5.0)


def test_instrument_precision_invalid_min_qty() -> None:
    """Test InstrumentPrecision rejects negative min_qty."""
    with pytest.raises(ValueError, match="min_qty must be non-negative"):
        create_test_precision(min_qty=-0.001)


def test_instrument_precision_invalid_max_qty() -> None:
    """Test InstrumentPrecision rejects non-positive max_qty."""
    with pytest.raises(ValueError, match="max_qty must be positive"):
        create_test_precision(max_qty=0.0)

    with pytest.raises(ValueError, match="max_qty must be positive"):
        create_test_precision(max_qty=-100.0)


def test_instrument_precision_min_exceeds_max() -> None:
    """Test InstrumentPrecision rejects min_qty > max_qty."""
    with pytest.raises(ValueError, match="min_qty.*cannot exceed max_qty"):
        create_test_precision(min_qty=10.0, max_qty=5.0)


# PrecisionGuard Initialization Tests


def test_precision_guard_with_precision() -> None:
    """Test PrecisionGuard initialization with manual precision."""
    precision = create_test_precision()
    guard = PrecisionGuard(precision=precision)

    assert guard.precision == precision


def test_precision_guard_requires_input() -> None:
    """Test PrecisionGuard requires either instrument or precision."""
    with pytest.raises(ValueError, match="Must provide either instrument or precision"):
        PrecisionGuard()


def test_precision_guard_rejects_both_inputs() -> None:
    """Test PrecisionGuard rejects both instrument and precision."""
    precision = create_test_precision()
    # Create mock instrument
    mock_instrument = type(
        "Instrument",
        (),
        {
            "price_increment": 0.01,
            "size_increment": 0.001,
            "min_quantity": 0.001,
            "max_quantity": 1000.0,
        },
    )()

    with pytest.raises(ValueError, match="Cannot provide both instrument and precision"):
        PrecisionGuard(instrument=mock_instrument, precision=precision)


# Price Clamping Tests


def test_clamp_price_rounds_to_nearest_tick() -> None:
    """Test price clamping rounds to nearest tick."""
    precision = create_test_precision(price_tick=0.01)
    guard = PrecisionGuard(precision=precision)

    # Round down
    assert guard.clamp_price(100.123) == pytest.approx(100.12)

    # Round up
    assert guard.clamp_price(100.126) == pytest.approx(100.13)

    # Exactly on tick
    assert guard.clamp_price(100.00) == pytest.approx(100.00)


def test_clamp_price_with_different_ticks() -> None:
    """Test price clamping with various tick sizes."""
    # Large tick (0.1)
    guard_large = PrecisionGuard(precision=create_test_precision(price_tick=0.1))
    assert guard_large.clamp_price(100.234) == pytest.approx(100.2)

    # Small tick (0.001)
    guard_small = PrecisionGuard(precision=create_test_precision(price_tick=0.001))
    assert guard_small.clamp_price(100.1234) == pytest.approx(100.123)

    # Very small tick (0.0001)
    guard_tiny = PrecisionGuard(precision=create_test_precision(price_tick=0.0001))
    assert guard_tiny.clamp_price(100.12345) == pytest.approx(100.1235)


# Quantity Clamping Tests


def test_clamp_qty_rounds_down_to_step() -> None:
    """Test quantity clamping rounds down to nearest step."""
    precision = create_test_precision(qty_step=0.001, min_qty=0.001, max_qty=1000.0)
    guard = PrecisionGuard(precision=precision)

    # Round down
    assert guard.clamp_qty(0.1234) == pytest.approx(0.123)

    # Exactly on step
    assert guard.clamp_qty(0.123) == pytest.approx(0.123)


def test_clamp_qty_enforces_minimum() -> None:
    """Test quantity clamping enforces minimum quantity."""
    precision = create_test_precision(qty_step=0.001, min_qty=0.01, max_qty=1000.0)
    guard = PrecisionGuard(precision=precision)

    # Below minimum → returns 0
    assert guard.clamp_qty(0.005) == 0.0

    # Exactly at minimum
    assert guard.clamp_qty(0.01) == pytest.approx(0.01)

    # Above minimum
    assert guard.clamp_qty(0.015) == pytest.approx(0.015)


def test_clamp_qty_enforces_maximum() -> None:
    """Test quantity clamping enforces maximum quantity."""
    precision = create_test_precision(qty_step=1.0, min_qty=1.0, max_qty=100.0)
    guard = PrecisionGuard(precision=precision)

    # Below maximum
    assert guard.clamp_qty(50.0) == pytest.approx(50.0)

    # Exactly at maximum
    assert guard.clamp_qty(100.0) == pytest.approx(100.0)

    # Above maximum → capped at max
    assert guard.clamp_qty(150.0) == pytest.approx(100.0)


def test_clamp_qty_zero_after_round_down() -> None:
    """Test quantity becomes zero when round-down hits minimum."""
    precision = create_test_precision(qty_step=0.01, min_qty=0.01, max_qty=1000.0)
    guard = PrecisionGuard(precision=precision)

    # 0.011 rounds down to 0.01 (on step)
    assert guard.clamp_qty(0.011) == pytest.approx(0.01)

    # 0.009 rounds down to 0.00 → below min → returns 0
    assert guard.clamp_qty(0.009) == 0.0


# Notional Validation Tests


def test_validate_notional_passes_above_minimum() -> None:
    """Test notional validation passes when above minimum."""
    precision = create_test_precision(min_notional=10.0)
    guard = PrecisionGuard(precision=precision)

    # 100 * 0.15 = 15.0 >= 10.0 → valid
    assert guard.validate_notional(100.0, 0.15) is True


def test_validate_notional_passes_exactly_at_minimum() -> None:
    """Test notional validation passes when exactly at minimum."""
    precision = create_test_precision(min_notional=10.0)
    guard = PrecisionGuard(precision=precision)

    # 100 * 0.1 = 10.0 >= 10.0 → valid
    assert guard.validate_notional(100.0, 0.1) is True


def test_validate_notional_fails_below_minimum() -> None:
    """Test notional validation fails when below minimum."""
    precision = create_test_precision(min_notional=10.0)
    guard = PrecisionGuard(precision=precision)

    # 100 * 0.05 = 5.0 < 10.0 → invalid
    assert guard.validate_notional(100.0, 0.05) is False


def test_validate_notional_zero_minimum() -> None:
    """Test notional validation with zero minimum (always passes)."""
    precision = create_test_precision(min_notional=0.0)
    guard = PrecisionGuard(precision=precision)

    # Any positive notional passes
    assert guard.validate_notional(100.0, 0.001) is True
    assert guard.validate_notional(0.01, 0.001) is True


# Rung Clamping Tests


def test_clamp_rung_valid_rung() -> None:
    """Test clamp_rung returns adjusted rung when valid."""
    precision = create_test_precision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=100.123, qty=0.1234, side=Side.LONG, tp=101.0, sl=99.0)
    clamped = guard.clamp_rung(original)

    assert clamped is not None
    assert clamped.price == pytest.approx(100.12)  # Rounded to tick
    assert clamped.qty == pytest.approx(0.123)  # Rounded down to step
    assert clamped.side == Side.LONG
    assert clamped.tp == 101.0  # Preserved
    assert clamped.sl == 99.0  # Preserved


def test_clamp_rung_returns_none_for_zero_qty() -> None:
    """Test clamp_rung returns None when quantity becomes zero."""
    precision = create_test_precision(
        price_tick=0.01,
        qty_step=0.01,
        min_notional=10.0,
        min_qty=0.01,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)

    # Quantity 0.005 rounds down to 0.00 → below min → invalid
    rung = Rung(price=100.0, qty=0.005, side=Side.LONG)
    assert guard.clamp_rung(rung) is None


def test_clamp_rung_returns_none_for_low_notional() -> None:
    """Test clamp_rung returns None when notional below minimum."""
    precision = create_test_precision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=10.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)

    # Price 10.0 * qty 0.5 = 5.0 < 10.0 min_notional → invalid
    rung = Rung(price=10.0, qty=0.5, side=Side.SHORT)
    clamped = guard.clamp_rung(rung)

    # After clamping: price=10.0, qty=0.5 (on step) → notional=5.0 < 10.0
    assert clamped is None


def test_clamp_rung_preserves_metadata() -> None:
    """Test clamp_rung preserves side, tp, sl, and tag."""
    precision = create_test_precision()
    guard = PrecisionGuard(precision=precision)

    original = Rung(
        price=100.123,
        qty=1.234,
        side=Side.SHORT,
        tp=99.0,
        sl=101.0,
        tag="test_tag",
    )

    clamped = guard.clamp_rung(original)

    assert clamped is not None
    assert clamped.side == Side.SHORT
    assert clamped.tp == 99.0
    assert clamped.sl == 101.0
    assert clamped.tag == "test_tag"


def test_clamp_rung_caps_at_max_qty() -> None:
    """Test clamp_rung caps quantity at maximum."""
    precision = create_test_precision(
        price_tick=1.0,
        qty_step=1.0,
        min_notional=5.0,
        min_qty=1.0,
        max_qty=10.0,
    )
    guard = PrecisionGuard(precision=precision)

    # Quantity 15 exceeds max → capped at 10
    rung = Rung(price=100.0, qty=15.0, side=Side.LONG)
    clamped = guard.clamp_rung(rung)

    assert clamped is not None
    assert clamped.qty == pytest.approx(10.0)


def test_clamp_rung_complex_scenario() -> None:
    """Test clamp_rung with complex precision requirements."""
    precision = create_test_precision(
        price_tick=0.5,  # Large tick
        qty_step=0.1,  # Coarse step
        min_notional=50.0,  # High minimum
        min_qty=0.5,  # High min qty
        max_qty=100.0,
    )
    guard = PrecisionGuard(precision=precision)

    # Price 99.7 → rounds to nearest 0.5 tick → 99.5 (99.7/0.5=199.4, rounds to 199)
    # Qty 1.234 → rounds down to 1.2
    # Notional: 99.5 * 1.2 = 119.4 >= 50.0 → valid
    rung = Rung(price=99.7, qty=1.234, side=Side.LONG)
    clamped = guard.clamp_rung(rung)

    assert clamped is not None
    assert clamped.price == pytest.approx(99.5)
    assert clamped.qty == pytest.approx(1.2)


# Edge Cases


def test_clamp_rung_with_none_tp_sl() -> None:
    """Test clamp_rung handles None tp/sl correctly."""
    precision = create_test_precision()
    guard = PrecisionGuard(precision=precision)

    rung = Rung(price=100.123, qty=1.234, side=Side.LONG, tp=None, sl=None)
    clamped = guard.clamp_rung(rung)

    assert clamped is not None
    assert clamped.tp is None
    assert clamped.sl is None
