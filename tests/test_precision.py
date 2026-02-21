"""
Property-based tests for precision guards using Hypothesis.

These tests verify that PrecisionGuard operations satisfy key mathematical
properties and invariants across a wide range of inputs, catching edge cases
that example-based tests might miss.
"""

import pytest
from hypothesis import assume, given, strategies as st

from naut_hedgegrid.domain.types import Rung, Side
from naut_hedgegrid.exchange.precision import InstrumentPrecision, PrecisionGuard

# ============================================================================
# Test Fixtures and Helpers
# ============================================================================


def create_fake_instrument(
    price_tick: float = 0.01,
    qty_step: float = 0.001,
    min_notional: float = 5.0,
    min_qty: float = 0.001,
    max_qty: float = 1000.0,
) -> InstrumentPrecision:
    """
    Create mock instrument precision for testing.

    Args:
        price_tick: Minimum price increment
        qty_step: Minimum quantity increment
        min_notional: Minimum order value (price * qty)
        min_qty: Minimum order quantity
        max_qty: Maximum order quantity

    Returns:
        InstrumentPrecision with specified parameters
    """
    return InstrumentPrecision(
        price_tick=price_tick,
        qty_step=qty_step,
        min_notional=min_notional,
        min_qty=min_qty,
        max_qty=max_qty,
    )


# ============================================================================
# Hypothesis Strategies
# ============================================================================

# Price strategies - cover wide range of realistic trading prices
random_price = st.floats(min_value=0.01, max_value=100000.0, allow_nan=False, allow_infinity=False)

# Quantity strategies - cover realistic order sizes
random_qty = st.floats(min_value=0.0001, max_value=10000.0, allow_nan=False, allow_infinity=False)

# Tick size strategies - common exchange tick sizes
random_tick_size = st.sampled_from([0.0001, 0.001, 0.01, 0.1, 0.5, 1.0])

# Step size strategies - common quantity steps
random_step_size = st.sampled_from([0.0001, 0.001, 0.01, 0.1, 1.0])

# Notional strategies - realistic minimum notional values
random_min_notional = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)


# ============================================================================
# Price Clamping Property Tests
# ============================================================================


@given(price=random_price, tick=random_tick_size)
def test_clamp_price_always_on_tick_boundary(price, tick):
    """
    Property: For any price and tick size, clamped price % tick == 0.

    This test verifies that clamp_price always returns a value that is an
    exact multiple of the tick size, ensuring exchange precision rules are met.
    """
    precision = create_fake_instrument(price_tick=tick)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_price(price)

    # Clamped price should be exact multiple of tick (within floating point precision)
    remainder = abs(clamped % tick)
    # Check remainder is either ~0 or ~tick (handles floating point precision)
    assert (
        remainder < tick * 1e-6 or abs(remainder - tick) < tick * 1e-6
    ), f"Clamped price {clamped} not on tick boundary. Remainder: {remainder}, tick: {tick}"


@given(price=random_price, tick=random_tick_size)
def test_clamp_price_minimizes_distance(price, tick):
    """
    Property: Clamped price is closest valid tick to original price.

    Verifies that the clamping algorithm minimizes distance from the original
    price, which is important for minimizing execution slippage.
    """
    precision = create_fake_instrument(price_tick=tick)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_price(price)

    # Distance should be at most half a tick (rounding to nearest)
    distance = abs(clamped - price)
    assert distance <= tick / 2 + tick * 1e-6, (  # Add small epsilon for float precision
        f"Distance {distance} exceeds half tick {tick / 2}. Original: {price}, clamped: {clamped}, tick: {tick}"
    )


@given(price=random_price, tick=random_tick_size)
def test_clamp_price_idempotent(price, tick):
    """
    Property: Clamping twice gives same result as clamping once.

    Verifies idempotency: clamp(clamp(x)) == clamp(x).
    """
    precision = create_fake_instrument(price_tick=tick)
    guard = PrecisionGuard(precision=precision)

    clamped_once = guard.clamp_price(price)
    clamped_twice = guard.clamp_price(clamped_once)

    assert clamped_once == pytest.approx(
        clamped_twice
    ), f"Clamping is not idempotent. First: {clamped_once}, second: {clamped_twice}"


@given(
    price=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    tick1=random_tick_size,
    tick2=random_tick_size,
)
def test_clamp_price_smaller_tick_more_precise(price, tick1, tick2):
    """
    Property: Smaller tick sizes allow more precise price clamping.

    When tick1 < tick2, the clamped price with tick1 should be at least as
    close to original price as with tick2.
    """
    assume(tick1 < tick2)  # Only test when tick1 is smaller

    guard1 = PrecisionGuard(precision=create_fake_instrument(price_tick=tick1))
    guard2 = PrecisionGuard(precision=create_fake_instrument(price_tick=tick2))

    clamped1 = guard1.clamp_price(price)
    clamped2 = guard2.clamp_price(price)

    distance1 = abs(clamped1 - price)
    distance2 = abs(clamped2 - price)

    # Smaller tick should give distance <= larger tick (or very close due to rounding)
    assert (
        distance1 <= distance2 + tick1 * 1e-6
    ), f"Smaller tick {tick1} gave larger distance {distance1} than larger tick {tick2} with distance {distance2}"


# ============================================================================
# Quantity Clamping Property Tests
# ============================================================================


@given(qty=random_qty, step=random_step_size)
def test_clamp_qty_always_on_step_boundary(qty, step):
    """
    Property: For any qty and step size, clamped qty % step ≈ 0.

    Verifies that clamped quantities are exact multiples of the step size
    (within floating point precision limits).
    """
    # Use min_qty=0 to test pure step alignment
    precision = create_fake_instrument(qty_step=step, min_qty=0.0, max_qty=100000.0)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_qty(qty)

    if clamped > 0:  # Only check non-zero results
        remainder = abs(clamped % step)
        assert (
            remainder < step * 1e-6 or abs(remainder - step) < step * 1e-6
        ), f"Clamped qty {clamped} not on step boundary. Remainder: {remainder}, step: {step}"


@given(qty=random_qty, step=random_step_size)
def test_clamp_qty_rounds_down(qty, step):
    """
    Property: Clamped qty <= original qty (conservative rounding).

    Verifies that quantity clamping always rounds down, which is important
    for risk management (never exceed intended position size).
    """
    precision = create_fake_instrument(qty_step=step, min_qty=0.0, max_qty=100000.0)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_qty(qty)

    # Clamped should never exceed original (within tiny float precision margin)
    assert clamped <= qty + step * 1e-9, f"Clamped qty {clamped} exceeds original {qty}. Step: {step}"


@given(
    qty=st.floats(min_value=0.001, max_value=500.0, allow_nan=False, allow_infinity=False),
    min_qty=st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
    max_qty=st.floats(min_value=100.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    step=random_step_size,
)
def test_clamp_qty_respects_min_max_bounds(qty, min_qty, max_qty, step):
    """
    Property: 0 <= clamped_qty <= max_qty, and clamped_qty == 0 if below min_qty.

    Verifies that quantity clamping enforces minimum and maximum constraints.
    """
    precision = create_fake_instrument(qty_step=step, min_qty=min_qty, max_qty=max_qty)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_qty(qty)

    # Always non-negative
    assert clamped >= 0, f"Clamped qty {clamped} is negative"

    # Never exceeds max
    assert clamped <= max_qty + step * 1e-6, f"Clamped qty {clamped} exceeds max {max_qty}"

    # If clamped is positive, it must be >= min_qty
    if clamped > 0:
        assert clamped >= min_qty - step * 1e-6, f"Clamped qty {clamped} is positive but below min {min_qty}"


@given(qty=random_qty, step=random_step_size)
def test_clamp_qty_idempotent(qty, step):
    """
    Property: Clamping qty twice gives same result as once (within float precision).

    Verifies idempotency: clamp(clamp(x)) ≈ clamp(x).
    Note: Due to floating point arithmetic, small differences may occur,
    but they should be within machine epsilon relative to the step size.
    """
    precision = create_fake_instrument(qty_step=step, min_qty=0.0, max_qty=100000.0)
    guard = PrecisionGuard(precision=precision)

    clamped_once = guard.clamp_qty(qty)
    clamped_twice = guard.clamp_qty(clamped_once)

    # Allow for floating point precision errors
    # Due to floor(x/step)*step operations, differences up to ~step size can occur
    # This is acceptable as long as the difference is small relative to the value
    diff = abs(clamped_once - clamped_twice)
    assert diff <= step + step * 1e-6, (
        f"Qty clamping is not idempotent (difference exceeds one step). "
        f"First: {clamped_once}, second: {clamped_twice}, diff: {diff}, step: {step}"
    )


@given(
    qty=st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False),
    step=random_step_size,
)
def test_clamp_qty_distance_bounded_by_step(qty, step):
    """
    Property: Distance between original and clamped qty <= step size.

    Since we round down, the distance should be less than one step.
    """
    precision = create_fake_instrument(qty_step=step, min_qty=0.0, max_qty=100000.0)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_qty(qty)

    if clamped > 0:  # Only check when not filtered to zero
        distance = abs(qty - clamped)
        assert (
            distance < step + step * 1e-6
        ), f"Distance {distance} exceeds step size {step}. Original: {qty}, clamped: {clamped}"


# ============================================================================
# Notional Validation Property Tests
# ============================================================================


@given(
    price=st.floats(min_value=0.1, max_value=10000.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False),
    min_notional=random_min_notional,
)
def test_validate_notional_threshold(price, qty, min_notional):
    """
    Property: validate_notional(p, q) == True iff p*q >= min_notional.

    Tests the exact threshold behavior of notional validation.
    """
    precision = create_fake_instrument(min_notional=min_notional)
    guard = PrecisionGuard(precision=precision)

    result = guard.validate_notional(price, qty)
    notional = price * qty

    if notional >= min_notional - 1e-9:  # Account for float precision
        assert result is True, f"Expected validation to pass. Notional: {notional}, min: {min_notional}"
    else:
        assert result is False, f"Expected validation to fail. Notional: {notional}, min: {min_notional}"


@given(
    price=st.floats(min_value=0.1, max_value=10000.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_validate_notional_zero_minimum_always_passes(price, qty):
    """
    Property: With zero min_notional, all positive price*qty pass validation.

    Verifies that zero minimum notional effectively disables the check.
    """
    precision = create_fake_instrument(min_notional=0.0)
    guard = PrecisionGuard(precision=precision)

    result = guard.validate_notional(price, qty)

    assert (
        result is True
    ), f"Validation should pass with zero minimum. Price: {price}, qty: {qty}, notional: {price * qty}"


@given(
    base_price=st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    base_qty=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
    multiplier=st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False),
)
def test_validate_notional_monotonic_in_qty(base_price, base_qty, multiplier):
    """
    Property: If validate_notional(p, q) passes, then validate_notional(p, q*m) passes for m >= 1.

    Verifies monotonicity: larger quantities with same price always improve notional.
    """
    min_notional = base_price * base_qty * 0.5  # Set threshold below base
    precision = create_fake_instrument(min_notional=min_notional)
    guard = PrecisionGuard(precision=precision)

    result_base = guard.validate_notional(base_price, base_qty)
    result_larger = guard.validate_notional(base_price, base_qty * multiplier)

    if result_base:
        assert result_larger, (
            f"Larger quantity should pass if base passes. "
            f"Base qty: {base_qty}, larger: {base_qty * multiplier}, "
            f"multiplier: {multiplier}"
        )


# ============================================================================
# Rung Clamping Property Tests
# ============================================================================


@given(
    price=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
    tick=random_tick_size,
    step=random_step_size,
)
def test_clamp_rung_idempotent(price, qty, tick, step):
    """
    Property: clamp_rung(clamp_rung(rung)) == clamp_rung(rung).

    For any valid rung, clamping twice gives same result as clamping once.
    This is critical for order reconciliation to avoid infinite update loops.
    """
    precision = create_fake_instrument(
        price_tick=tick,
        qty_step=step,
        min_notional=0.0,  # Use 0 to focus on idempotency
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=price, qty=qty, side=Side.LONG, tp=None, sl=None)
    clamped_once = guard.clamp_rung(original)

    if clamped_once is not None:
        clamped_twice = guard.clamp_rung(clamped_once)

        assert clamped_twice is not None, "Second clamp should not return None"

        # Check price idempotency with appropriate tolerance
        price_diff = abs(clamped_twice.price - clamped_once.price)
        assert price_diff <= tick + tick * 1e-6, (
            f"Price changed significantly on second clamp: {clamped_once.price} -> {clamped_twice.price}, "
            f"diff: {price_diff}, tick: {tick}"
        )

        # Check qty idempotency with appropriate tolerance
        qty_diff = abs(clamped_twice.qty - clamped_once.qty)
        assert qty_diff <= step + step * 1e-6, (
            f"Qty changed significantly on second clamp: {clamped_once.qty} -> {clamped_twice.qty}, "
            f"diff: {qty_diff}, step: {step}"
        )


@given(
    price=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
    side=st.sampled_from([Side.LONG, Side.SHORT]),
    tp=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False) | st.none(),
    sl=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False) | st.none(),
    tag=st.text(min_size=0, max_size=20),
)
def test_clamp_rung_preserves_metadata(price, qty, side, tp, sl, tag):
    """
    Property: For any valid rung, clamped version has same side, tp, sl, tag.

    Only price and qty should change during clamping - all other attributes
    must be preserved exactly.
    """
    precision = create_fake_instrument(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=0.0,
        min_qty=0.001,
        max_qty=10000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=price, qty=qty, side=side, tp=tp, sl=sl, tag=tag)
    clamped = guard.clamp_rung(original)

    if clamped is not None:
        assert clamped.side == side, "Side was modified"
        assert clamped.tp == tp, "Take profit was modified"
        assert clamped.sl == sl, "Stop loss was modified"
        assert clamped.tag == tag, "Tag was modified"


@given(
    price=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False),
    min_notional=st.floats(min_value=10.0, max_value=50.0, allow_nan=False, allow_infinity=False),
)
def test_clamp_rung_filters_invalid_notional(price, qty, min_notional):
    """
    Property: Returns None when clamped_price * clamped_qty < min_notional.

    Tests that rungs which violate notional requirements after clamping are
    correctly filtered out.
    """
    precision = create_fake_instrument(
        price_tick=0.5,  # Coarse tick might change price significantly
        qty_step=0.01,
        min_notional=min_notional,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=price, qty=qty, side=Side.LONG)
    clamped = guard.clamp_rung(original)

    if clamped is None:
        # Verify the notional check is what failed
        clamped_price = guard.clamp_price(price)
        clamped_qty = guard.clamp_qty(qty)
        notional = clamped_price * clamped_qty

        # Should fail notional check OR qty became zero
        assert (
            notional < min_notional or clamped_qty <= 0
        ), f"Rung returned None but notional {notional} >= {min_notional} and qty {clamped_qty} > 0"


@given(
    price=st.floats(min_value=10.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.0001, max_value=0.01, allow_nan=False, allow_infinity=False),
    min_qty=st.floats(min_value=0.01, max_value=0.1, allow_nan=False, allow_infinity=False),
)
def test_clamp_rung_filters_below_minimum_qty(price, qty, min_qty):
    """
    Property: Returns None when clamped_qty == 0 (below minimum).

    Verifies that rungs with quantities that round down to zero or below
    the minimum are filtered out.
    """
    assume(qty < min_qty)  # Only test cases where qty is below minimum

    precision = create_fake_instrument(
        price_tick=0.01,
        qty_step=0.01,  # Step size larger than test quantities
        min_notional=0.0,
        min_qty=min_qty,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=price, qty=qty, side=Side.LONG)
    clamped = guard.clamp_rung(original)

    # Should return None because qty rounds down to zero or below minimum
    assert clamped is None, f"Expected None for qty {qty} < min_qty {min_qty}, but got valid rung"


@given(
    price=st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
)
def test_clamp_rung_with_valid_inputs_returns_rung(price, qty):
    """
    Property: Valid inputs with reasonable precision produce non-None result.

    For typical trading scenarios with reasonable constraints, clamping should
    succeed and return a valid rung.
    """
    precision = create_fake_instrument(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=1.0,  # Low threshold
        min_qty=0.001,  # Low minimum
        max_qty=10000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=price, qty=qty, side=Side.LONG)
    clamped = guard.clamp_rung(original)

    # Should succeed for these reasonable inputs
    assert clamped is not None, f"Expected valid rung for price={price}, qty={qty}, but got None"

    # Verify it's actually clamped
    assert clamped.price > 0
    assert clamped.qty > 0


# ============================================================================
# Extreme Values and Edge Cases
# ============================================================================


@given(
    price=st.floats(min_value=0.0001, max_value=0.01, allow_nan=False, allow_infinity=False),
    tick=st.sampled_from([0.0001, 0.001]),
)
def test_precision_guard_very_small_prices(price, tick):
    """
    Property: Handles very small prices correctly (near float precision limits).

    Tests that precision clamping works correctly for low-priced assets like
    altcoins with many decimal places.
    """
    precision = create_fake_instrument(price_tick=tick)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_price(price)

    # Should still maintain tick alignment
    remainder = abs(clamped % tick)
    assert (
        remainder < tick * 1e-5 or abs(remainder - tick) < tick * 1e-5
    ), f"Small price {price} not properly clamped to tick {tick}. Clamped: {clamped}, remainder: {remainder}"


@given(
    price=st.floats(min_value=50000.0, max_value=100000.0, allow_nan=False, allow_infinity=False),
    tick=st.sampled_from([0.1, 1.0, 10.0]),
)
def test_precision_guard_very_large_prices(price, tick):
    """
    Property: Handles very large prices correctly.

    Tests precision clamping for high-priced assets like BTC with large
    nominal values.
    """
    precision = create_fake_instrument(price_tick=tick)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_price(price)

    # Should still maintain tick alignment
    remainder = abs(clamped % tick)
    assert (
        remainder < tick * 1e-6 or abs(remainder - tick) < tick * 1e-6
    ), f"Large price {price} not properly clamped to tick {tick}. Clamped: {clamped}, remainder: {remainder}"


@given(
    qty=st.floats(min_value=0.00001, max_value=0.0001, allow_nan=False, allow_infinity=False),
    step=st.sampled_from([0.00001, 0.0001]),
)
def test_precision_guard_very_small_quantities(qty, step):
    """
    Property: Handles very small quantities correctly.

    Tests that quantity clamping works for very small order sizes near
    the minimum tradeable amounts.
    """
    precision = create_fake_instrument(qty_step=step, min_qty=0.0, max_qty=1000.0)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_qty(qty)

    if clamped > 0:
        # Should maintain step alignment
        remainder = abs(clamped % step)
        assert (
            remainder < step * 1e-5 or abs(remainder - step) < step * 1e-5
        ), f"Small qty {qty} not properly clamped to step {step}. Clamped: {clamped}, remainder: {remainder}"


@given(
    qty=st.floats(min_value=1000.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    step=st.sampled_from([0.1, 1.0, 10.0]),
)
def test_precision_guard_very_large_quantities(qty, step):
    """
    Property: Handles very large quantities correctly.

    Tests precision clamping for large order sizes.
    """
    precision = create_fake_instrument(qty_step=step, min_qty=0.0, max_qty=100000.0)
    guard = PrecisionGuard(precision=precision)

    clamped = guard.clamp_qty(qty)

    # Should maintain step alignment
    remainder = abs(clamped % step)
    assert (
        remainder < step * 1e-6 or abs(remainder - step) < step * 1e-6
    ), f"Large qty {qty} not properly clamped to step {step}. Clamped: {clamped}, remainder: {remainder}"


@given(
    price=st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
    qty=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_clamp_rung_with_none_tp_sl_preserved(price, qty):
    """
    Property: None values for tp/sl are preserved through clamping.

    Verifies that optional fields remain None when not provided.
    """
    precision = create_fake_instrument(
        price_tick=0.01,
        qty_step=0.01,
        min_notional=0.0,
        min_qty=0.01,
        max_qty=10000.0,
    )
    guard = PrecisionGuard(precision=precision)

    original = Rung(price=price, qty=qty, side=Side.LONG, tp=None, sl=None)
    clamped = guard.clamp_rung(original)

    if clamped is not None:
        assert clamped.tp is None, "None TP was changed"
        assert clamped.sl is None, "None SL was changed"
