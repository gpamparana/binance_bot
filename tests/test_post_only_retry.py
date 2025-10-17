"""Tests for post-only retry logic with bounded attempts."""

from datetime import UTC, datetime

import pytest

from naut_hedgegrid.domain.types import OrderIntent, Side
from naut_hedgegrid.exchange.precision import InstrumentPrecision, PrecisionGuard
from naut_hedgegrid.strategy.order_sync import PostOnlyRetryHandler


# =====================================================================
# FIXTURES
# =====================================================================


@pytest.fixture
def test_precision():
    """Create test precision parameters."""
    return InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )


@pytest.fixture
def precision_guard(test_precision):
    """Create test precision guard."""
    return PrecisionGuard(precision=test_precision)


@pytest.fixture
def retry_handler(precision_guard):
    """Create test retry handler with default settings."""
    return PostOnlyRetryHandler(
        precision_guard=precision_guard,
        max_attempts=3,
        enabled=True,
    )


@pytest.fixture
def disabled_retry_handler(precision_guard):
    """Create disabled retry handler."""
    return PostOnlyRetryHandler(
        precision_guard=precision_guard,
        max_attempts=3,
        enabled=False,
    )


# =====================================================================
# TESTS: INITIALIZATION AND CONFIGURATION
# =====================================================================


def test_retry_handler_initialization(precision_guard):
    """Test retry handler initializes with correct parameters."""
    handler = PostOnlyRetryHandler(
        precision_guard=precision_guard,
        max_attempts=5,
        enabled=True,
    )

    assert handler.enabled is True
    assert handler._max_attempts == 5
    assert handler._enabled is True
    assert len(handler._retry_history) == 0


def test_retry_handler_disabled_when_configured(precision_guard):
    """Test retry handler is disabled when enabled=False."""
    handler = PostOnlyRetryHandler(
        precision_guard=precision_guard,
        max_attempts=3,
        enabled=False,
    )

    assert handler.enabled is False
    assert handler.should_retry("Post-only order would be filled immediately") is False


def test_retry_handler_disabled_when_max_attempts_zero(precision_guard):
    """Test retry handler is disabled when max_attempts=0."""
    handler = PostOnlyRetryHandler(
        precision_guard=precision_guard,
        max_attempts=0,
        enabled=True,
    )

    assert handler.enabled is False


def test_retry_handler_rejects_negative_max_attempts(precision_guard):
    """Test retry handler rejects negative max_attempts."""
    with pytest.raises(ValueError, match="max_attempts must be non-negative"):
        PostOnlyRetryHandler(
            precision_guard=precision_guard,
            max_attempts=-1,
            enabled=True,
        )


# =====================================================================
# TESTS: REJECTION REASON DETECTION
# =====================================================================


def test_should_retry_detects_post_only_rejections(retry_handler):
    """Test detection of various post-only rejection messages."""
    # These should all be detected as retryable
    retryable_messages = [
        "Post-only order would be filled immediately",
        "Order would immediately match and take",
        "POST_ONLY order would execute as taker",
        "Would take liquidity (post-only)",
        "post-only rejection: would cross spread",
        "Post only flag set but order would cross",
        "Would be filled immediately as taker",
    ]

    for msg in retryable_messages:
        assert retry_handler.should_retry(msg), f"Should retry: {msg}"


def test_should_not_retry_other_rejections(retry_handler):
    """Test non-retryable rejection messages."""
    # These should NOT be retryable
    non_retryable_messages = [
        "Insufficient balance",
        "Invalid price",
        "Market closed",
        "Order size too small",
        "Rate limit exceeded",
        "Instrument not found",
    ]

    for msg in non_retryable_messages:
        assert not retry_handler.should_retry(msg), f"Should not retry: {msg}"


def test_should_retry_case_insensitive(retry_handler):
    """Test rejection detection is case-insensitive."""
    messages = [
        "POST-ONLY ORDER WOULD BE FILLED IMMEDIATELY",
        "post-only order would be filled immediately",
        "Post-Only Order Would Be Filled Immediately",
    ]

    for msg in messages:
        assert retry_handler.should_retry(msg)


# =====================================================================
# TESTS: PRICE ADJUSTMENT LOGIC
# =====================================================================


def test_adjust_price_for_long_orders(retry_handler):
    """Test LONG orders decrease price (move away from ask)."""
    original_price = 100.00

    # Attempt 1: Should decrease by 1 tick (0.01)
    adjusted_1 = retry_handler.adjust_price_for_retry(original_price, Side.LONG, 1)
    assert adjusted_1 == pytest.approx(99.99)

    # Attempt 2: Should decrease by 2 ticks (0.02)
    adjusted_2 = retry_handler.adjust_price_for_retry(original_price, Side.LONG, 2)
    assert adjusted_2 == pytest.approx(99.98)

    # Attempt 3: Should decrease by 3 ticks (0.03)
    adjusted_3 = retry_handler.adjust_price_for_retry(original_price, Side.LONG, 3)
    assert adjusted_3 == pytest.approx(99.97)


def test_adjust_price_for_short_orders(retry_handler):
    """Test SHORT orders increase price (move away from bid)."""
    original_price = 100.00

    # Attempt 1: Should increase by 1 tick (0.01)
    adjusted_1 = retry_handler.adjust_price_for_retry(original_price, Side.SHORT, 1)
    assert adjusted_1 == pytest.approx(100.01)

    # Attempt 2: Should increase by 2 ticks (0.02)
    adjusted_2 = retry_handler.adjust_price_for_retry(original_price, Side.SHORT, 2)
    assert adjusted_2 == pytest.approx(100.02)

    # Attempt 3: Should increase by 3 ticks (0.03)
    adjusted_3 = retry_handler.adjust_price_for_retry(original_price, Side.SHORT, 3)
    assert adjusted_3 == pytest.approx(100.03)


def test_price_adjustment_respects_tick_boundaries(retry_handler):
    """Test price adjustments are clamped to tick boundaries."""
    # Use price that might not round nicely
    original_price = 100.005

    # Should clamp to nearest tick (0.01)
    adjusted = retry_handler.adjust_price_for_retry(original_price, Side.LONG, 1)

    # Check result is on tick boundary
    tick = retry_handler._precision_guard.precision.price_tick
    assert adjusted % tick == pytest.approx(0.0, abs=1e-10)


def test_price_adjustment_progressive_movement(retry_handler):
    """Test each retry moves price further from spread."""
    original_price = 100.00

    # For LONG orders, each attempt should be progressively lower
    prev_price = original_price
    for attempt in range(1, 6):
        adjusted = retry_handler.adjust_price_for_retry(original_price, Side.LONG, attempt)
        assert adjusted < prev_price
        prev_price = adjusted

    # For SHORT orders, each attempt should be progressively higher
    prev_price = original_price
    for attempt in range(1, 6):
        adjusted = retry_handler.adjust_price_for_retry(original_price, Side.SHORT, attempt)
        assert adjusted > prev_price
        prev_price = adjusted


# =====================================================================
# TESTS: RETRY ATTEMPT TRACKING
# =====================================================================


def test_record_attempt_stores_history(retry_handler):
    """Test retry attempts are recorded in history."""
    client_order_id = "HG1-LONG-01-123"

    retry_handler.record_attempt(
        client_order_id=client_order_id,
        attempt=1,
        original_price=100.00,
        adjusted_price=99.99,
        reason="Post-only would be filled immediately",
    )

    history = retry_handler.get_retry_history(client_order_id)
    assert len(history) == 1
    assert history[0].attempt_number == 1
    assert history[0].original_price == pytest.approx(100.00)
    assert history[0].adjusted_price == pytest.approx(99.99)
    assert "Post-only" in history[0].reason


def test_record_multiple_attempts(retry_handler):
    """Test multiple retry attempts are recorded."""
    client_order_id = "HG1-LONG-01-123"

    for attempt in range(1, 4):
        retry_handler.record_attempt(
            client_order_id=client_order_id,
            attempt=attempt,
            original_price=100.00,
            adjusted_price=100.00 - (0.01 * attempt),
            reason="Post-only rejection",
        )

    history = retry_handler.get_retry_history(client_order_id)
    assert len(history) == 3
    assert history[0].attempt_number == 1
    assert history[1].attempt_number == 2
    assert history[2].attempt_number == 3


def test_get_retry_history_empty_for_unknown_order(retry_handler):
    """Test getting history for unknown order returns empty list."""
    history = retry_handler.get_retry_history("UNKNOWN-ORDER-ID")
    assert len(history) == 0


def test_clear_history_removes_order(retry_handler):
    """Test clearing history removes order entries."""
    client_order_id = "HG1-LONG-01-123"

    retry_handler.record_attempt(
        client_order_id=client_order_id,
        attempt=1,
        original_price=100.00,
        adjusted_price=99.99,
        reason="Test",
    )

    assert len(retry_handler.get_retry_history(client_order_id)) == 1

    retry_handler.clear_history(client_order_id)

    assert len(retry_handler.get_retry_history(client_order_id)) == 0


def test_retry_attempt_has_timestamp(retry_handler):
    """Test retry attempts include timestamp."""
    client_order_id = "HG1-LONG-01-123"

    before_timestamp = int(datetime.now(tz=UTC).timestamp() * 1000)

    retry_handler.record_attempt(
        client_order_id=client_order_id,
        attempt=1,
        original_price=100.00,
        adjusted_price=99.99,
        reason="Test",
    )

    after_timestamp = int(datetime.now(tz=UTC).timestamp() * 1000)

    history = retry_handler.get_retry_history(client_order_id)
    assert len(history) == 1
    assert before_timestamp <= history[0].timestamp_ms <= after_timestamp


# =====================================================================
# TESTS: INTEGRATION SCENARIOS
# =====================================================================


def test_tight_spread_scenario(retry_handler):
    """
    Integration test simulating tight spread where post-only would cross.

    Scenario:
    1. Submit LONG buy order at 100.00
    2. Order rejected: "Post-only would be filled immediately"
    3. Retry 1: Adjust to 99.99
    4. Still rejected
    5. Retry 2: Adjust to 99.98
    6. Still rejected
    7. Retry 3: Adjust to 99.97
    8. Finally accepted

    Verify:
    - Exactly 3 retries occur
    - Price adjusts correctly each time
    - History tracks all attempts
    """
    original_price = 100.00
    rejection_reason = "Post-only order would be filled immediately"

    # Verify retry is warranted
    assert retry_handler.should_retry(rejection_reason)

    # Simulate 3 retry attempts
    adjustments = []
    for attempt in range(1, 4):
        adjusted = retry_handler.adjust_price_for_retry(original_price, Side.LONG, attempt)
        adjustments.append(adjusted)

        retry_handler.record_attempt(
            client_order_id="HG1-LONG-01-123",
            attempt=attempt,
            original_price=original_price,
            adjusted_price=adjusted,
            reason=rejection_reason,
        )

    # Verify adjustments
    assert adjustments[0] == pytest.approx(99.99)
    assert adjustments[1] == pytest.approx(99.98)
    assert adjustments[2] == pytest.approx(99.97)

    # Verify history
    history = retry_handler.get_retry_history("HG1-LONG-01-123")
    assert len(history) == 3
    assert history[0].adjusted_price == pytest.approx(99.99)
    assert history[1].adjusted_price == pytest.approx(99.98)
    assert history[2].adjusted_price == pytest.approx(99.97)


def test_retry_preserves_quantity():
    """Test retry logic preserves order quantity."""
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    # Price adjustment should not affect quantity validation
    original_price = 100.00
    adjusted_price = handler.adjust_price_for_retry(original_price, Side.LONG, 1)

    # Both should support same quantity range
    assert guard.validate_notional(original_price, 0.1)
    assert guard.validate_notional(adjusted_price, 0.1)


def test_multiple_concurrent_retries(retry_handler):
    """Test multiple orders can retry concurrently."""
    orders = [
        ("HG1-LONG-01-123", 100.00),
        ("HG1-LONG-02-124", 99.50),
        ("HG1-SHORT-01-125", 100.50),
    ]

    # Record attempts for all orders
    for order_id, price in orders:
        side = Side.LONG if "LONG" in order_id else Side.SHORT
        adjusted = retry_handler.adjust_price_for_retry(price, side, 1)

        retry_handler.record_attempt(
            client_order_id=order_id,
            attempt=1,
            original_price=price,
            adjusted_price=adjusted,
            reason="Post-only rejection",
        )

    # Verify all have history
    for order_id, _ in orders:
        history = retry_handler.get_retry_history(order_id)
        assert len(history) == 1


def test_retry_cleanup_on_success(retry_handler):
    """Test retry history cleanup on successful order acceptance."""
    client_order_id = "HG1-LONG-01-123"

    # Record some attempts
    for attempt in range(1, 3):
        retry_handler.record_attempt(
            client_order_id=client_order_id,
            attempt=attempt,
            original_price=100.00,
            adjusted_price=100.00 - (0.01 * attempt),
            reason="Post-only rejection",
        )

    # Verify history exists
    assert len(retry_handler.get_retry_history(client_order_id)) == 2

    # Clear on success
    retry_handler.clear_history(client_order_id)

    # Verify cleaned up
    assert len(retry_handler.get_retry_history(client_order_id)) == 0


def test_retry_cleanup_on_exhaustion(retry_handler):
    """Test cleanup after max retries exhausted."""
    client_order_id = "HG1-LONG-01-123"

    # Record max attempts (3)
    for attempt in range(1, 4):
        retry_handler.record_attempt(
            client_order_id=client_order_id,
            attempt=attempt,
            original_price=100.00,
            adjusted_price=100.00 - (0.01 * attempt),
            reason="Post-only rejection",
        )

    # Verify we have 3 attempts
    history = retry_handler.get_retry_history(client_order_id)
    assert len(history) == 3

    # Abandon order (cleanup)
    retry_handler.clear_history(client_order_id)

    # Verify cleaned up
    assert len(retry_handler.get_retry_history(client_order_id)) == 0


# =====================================================================
# TESTS: EDGE CASES
# =====================================================================


def test_retry_with_very_small_tick_size():
    """Test retry works with very small tick sizes (e.g., crypto)."""
    precision = InstrumentPrecision(
        price_tick=0.00001,  # 5 decimal places
        qty_step=0.00001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    original_price = 1.23456

    # Should adjust by 0.00001 per attempt
    adjusted_1 = handler.adjust_price_for_retry(original_price, Side.LONG, 1)
    assert adjusted_1 == pytest.approx(1.23455, abs=1e-6)


def test_retry_with_large_tick_size():
    """Test retry works with large tick sizes (e.g., some futures)."""
    precision = InstrumentPrecision(
        price_tick=5.0,  # $5 tick
        qty_step=1.0,
        min_notional=10.0,
        min_qty=1.0,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    original_price = 50000.0

    # Should adjust by $5 per attempt
    adjusted_1 = handler.adjust_price_for_retry(original_price, Side.LONG, 1)
    assert adjusted_1 == pytest.approx(49995.0)

    adjusted_2 = handler.adjust_price_for_retry(original_price, Side.LONG, 2)
    assert adjusted_2 == pytest.approx(49990.0)


def test_retry_at_price_boundaries():
    """Test retry behavior at extreme price levels."""
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    # Very low price
    low_price = 0.10
    adjusted = handler.adjust_price_for_retry(low_price, Side.LONG, 1)
    assert adjusted == pytest.approx(0.09)
    assert adjusted > 0  # Should not go negative

    # Very high price
    high_price = 99999.99
    adjusted = handler.adjust_price_for_retry(high_price, Side.SHORT, 1)
    assert adjusted == pytest.approx(100000.00)


def test_retry_handler_thread_safe_access():
    """Test retry handler can be accessed from multiple contexts."""
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    # Simulate concurrent access (note: actual threading not tested)
    for i in range(10):
        order_id = f"ORDER-{i}"
        handler.record_attempt(
            client_order_id=order_id,
            attempt=1,
            original_price=100.00,
            adjusted_price=99.99,
            reason="Test",
        )

    # Verify all recorded
    for i in range(10):
        order_id = f"ORDER-{i}"
        assert len(handler.get_retry_history(order_id)) == 1


# =====================================================================
# TESTS: ORDER INTENT INTEGRATION
# =====================================================================


def test_order_intent_with_retry_fields():
    """Test OrderIntent correctly stores retry tracking fields."""
    from dataclasses import replace

    # Create initial intent
    intent = OrderIntent.create(
        client_order_id="HG1-LONG-01-123",
        side=Side.LONG,
        price=100.00,
        qty=0.5,
    )

    # Verify defaults
    assert intent.retry_count == 0
    assert intent.original_price is None

    # Create retry intent
    retry_intent = replace(
        intent,
        price=99.99,
        retry_count=1,
        original_price=100.00,
        metadata={"retry_attempt": "1"},
    )

    # Verify retry fields
    assert retry_intent.retry_count == 1
    assert retry_intent.original_price == pytest.approx(100.00)
    assert retry_intent.price == pytest.approx(99.99)
    assert retry_intent.metadata["retry_attempt"] == "1"


def test_order_intent_preserves_immutability_on_retry():
    """Test OrderIntent remains frozen during retry updates."""
    intent = OrderIntent.create(
        client_order_id="HG1-LONG-01-123",
        side=Side.LONG,
        price=100.00,
        qty=0.5,
    )

    # Attempt to modify should raise error (frozen dataclass)
    with pytest.raises(Exception, match="frozen|immutable|can't set attribute|cannot assign"):
        intent.retry_count = 1  # type: ignore[misc]


# =====================================================================
# TESTS: PERFORMANCE AND MEMORY
# =====================================================================


def test_retry_history_memory_cleanup():
    """Test retry history doesn't grow unbounded."""
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    # Create many orders
    for i in range(100):
        order_id = f"ORDER-{i}"
        handler.record_attempt(
            client_order_id=order_id,
            attempt=1,
            original_price=100.00,
            adjusted_price=99.99,
            reason="Test",
        )

    # Verify all stored
    assert len(handler._retry_history) == 100

    # Clear all
    for i in range(100):
        order_id = f"ORDER-{i}"
        handler.clear_history(order_id)

    # Verify memory freed
    assert len(handler._retry_history) == 0


def test_retry_adjustment_performance():
    """Test price adjustment is efficient for many calls."""
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    handler = PostOnlyRetryHandler(guard, max_attempts=3, enabled=True)

    # Should handle many adjustments quickly
    for i in range(1000):
        price = 100.00 + i
        adjusted = handler.adjust_price_for_retry(price, Side.LONG, 1)
        assert adjusted < price
