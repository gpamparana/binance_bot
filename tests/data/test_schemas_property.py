"""
Property-based tests for data schemas using Hypothesis.

These tests verify that schemas handle a wide range of valid inputs correctly
and reject invalid inputs consistently, using property-based testing to explore
the input space more thoroughly than example-based tests.
"""

from datetime import UTC, datetime

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from naut_hedgegrid.data.schemas import (
    FundingRateSchema,
    MarkPriceSchema,
    TradeSchema,
)

# Strategy for generating valid timestamps
# Note: hypothesis datetime strategy requires naive datetimes
valid_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
)


# ============================================================================
# TradeSchema Property Tests
# ============================================================================


@given(
    price=st.floats(min_value=0.01, max_value=1_000_000, allow_nan=False, allow_infinity=False),
    size=st.floats(min_value=0.00001, max_value=10_000, allow_nan=False, allow_infinity=False),
)
def test_trade_schema_valid_price_and_size_ranges(price, size):
    """
    Property test: any positive price and size should be valid.

    This test verifies that TradeSchema accepts all positive prices and sizes
    within reasonable trading ranges.
    """
    trade = TradeSchema(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        price=price,
        size=size,
        aggressor_side="BUY",
        trade_id="test_123",
    )

    assert trade.price == price
    assert trade.size == size


@given(
    timestamp=valid_timestamps,
    aggressor_side=st.sampled_from(["BUY", "SELL", "buy", "sell", "Buy", "Sell"]),
)
def test_trade_schema_valid_timestamps_and_sides(timestamp, aggressor_side):
    """
    Property test: all valid timestamps and aggressor sides should work.

    Tests that timestamps are handled correctly and aggressor_side is
    normalized to uppercase regardless of input case.
    """
    trade = TradeSchema(
        timestamp=timestamp,
        price=50000.0,
        size=0.1,
        aggressor_side=aggressor_side,
        trade_id="test_123",
    )

    assert trade.timestamp.tzinfo == UTC
    assert trade.aggressor_side in ["BUY", "SELL"]


@given(
    price=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
)
def test_trade_schema_nonpositive_price_fails(price):
    """
    Property test: any non-positive price should fail validation.

    Verifies that zero and negative prices are consistently rejected.
    """
    with pytest.raises(ValidationError):
        TradeSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            price=price,
            size=0.1,
            aggressor_side="BUY",
            trade_id="test_123",
        )


@given(
    size=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
)
def test_trade_schema_nonpositive_size_fails(size):
    """
    Property test: any non-positive size should fail validation.

    Verifies that zero and negative sizes are consistently rejected.
    """
    with pytest.raises(ValidationError):
        TradeSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            price=50000.0,
            size=size,
            aggressor_side="BUY",
            trade_id="test_123",
        )


@given(
    trade_id=st.text(min_size=1, max_size=100),
)
def test_trade_schema_arbitrary_trade_ids(trade_id):
    """
    Property test: any non-empty string should work as trade_id.

    Verifies that TradeSchema accepts arbitrary string identifiers.
    """
    trade = TradeSchema(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        price=50000.0,
        size=0.1,
        aggressor_side="BUY",
        trade_id=trade_id,
    )

    assert trade.trade_id == trade_id


# ============================================================================
# MarkPriceSchema Property Tests
# ============================================================================


@given(
    mark_price=st.floats(min_value=0.01, max_value=1_000_000, allow_nan=False, allow_infinity=False),
    timestamp=valid_timestamps,
)
def test_mark_price_schema_valid_ranges(mark_price, timestamp):
    """
    Property test: any positive mark price should be valid.

    Verifies that MarkPriceSchema accepts all positive prices within
    reasonable ranges and handles timestamps correctly.
    """
    mark = MarkPriceSchema(
        timestamp=timestamp,
        mark_price=mark_price,
    )

    assert mark.mark_price == mark_price
    assert mark.timestamp.tzinfo == UTC


@given(
    mark_price=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
)
def test_mark_price_schema_nonpositive_fails(mark_price):
    """
    Property test: any non-positive mark price should fail validation.

    Verifies that zero and negative mark prices are consistently rejected.
    """
    with pytest.raises(ValidationError):
        MarkPriceSchema(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            mark_price=mark_price,
        )


# ============================================================================
# FundingRateSchema Property Tests
# ============================================================================


@given(
    funding_rate=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    timestamp=valid_timestamps,
)
def test_funding_rate_schema_valid_ranges(funding_rate, timestamp):
    """
    Property test: funding rate can be any finite value (positive or negative).

    Unlike prices which must be positive, funding rates can be negative
    when shorts pay longs. This test verifies both positive and negative
    rates are accepted.
    """
    funding = FundingRateSchema(
        timestamp=timestamp,
        funding_rate=funding_rate,
    )

    assert funding.funding_rate == funding_rate
    assert funding.timestamp.tzinfo == UTC


@given(
    funding_rate=st.floats(min_value=-0.01, max_value=0.01, allow_nan=False, allow_infinity=False),
    has_next_funding=st.booleans(),
)
def test_funding_rate_schema_optional_next_funding(funding_rate, has_next_funding):
    """
    Property test: next_funding_time is optional.

    Verifies that FundingRateSchema works with or without next_funding_time.
    """
    if has_next_funding:
        next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    else:
        next_funding = None

    funding = FundingRateSchema(
        timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        funding_rate=funding_rate,
        next_funding_time=next_funding,
    )

    assert funding.funding_rate == funding_rate
    assert funding.next_funding_time == next_funding


@given(
    rate1=st.floats(min_value=-0.01, max_value=0.01, allow_nan=False, allow_infinity=False),
    rate2=st.floats(min_value=-0.01, max_value=0.01, allow_nan=False, allow_infinity=False),
)
def test_funding_rate_schema_immutability(rate1, rate2):
    """
    Property test: FundingRateSchema is immutable.

    Verifies that after creation, schema fields cannot be modified
    regardless of the values used.
    """
    funding = FundingRateSchema(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        funding_rate=rate1,
    )

    # Attempt to modify should fail
    with pytest.raises(ValidationError):
        funding.funding_rate = rate2


# ============================================================================
# Edge Case Property Tests
# ============================================================================


@given(
    price=st.floats(
        min_value=0.01,
        max_value=1_000_000,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_trade_schema_extreme_precision(price):
    """
    Property test: schemas handle extreme precision correctly.

    Tests that floating point values with many decimal places
    are handled correctly without precision loss issues.
    """
    trade = TradeSchema(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        price=price,
        size=0.123456789,
        aggressor_side="BUY",
        trade_id="test",
    )

    # Price should be stored accurately (within float precision)
    assert abs(trade.price - price) < 1e-10


@given(
    microseconds=st.integers(min_value=0, max_value=999999),
)
def test_schemas_preserve_microsecond_precision(microseconds):
    """
    Property test: timestamp microseconds are preserved.

    Verifies that timestamp precision down to microseconds is maintained
    through schema validation.
    """
    timestamp = datetime(2024, 1, 1, 12, 0, 0, microseconds, tzinfo=UTC)

    trade = TradeSchema(
        timestamp=timestamp,
        price=50000.0,
        size=0.1,
        aggressor_side="BUY",
        trade_id="test",
    )

    assert trade.timestamp.microsecond == microseconds


@given(
    num_trades=st.integers(min_value=1, max_value=59),  # Max 59 to fit in seconds
)
def test_trade_schema_batch_creation(num_trades):
    """
    Property test: multiple schemas can be created consistently.

    Tests that creating many schema instances works correctly and
    produces consistent results.
    """
    trades = [
        TradeSchema(
            timestamp=datetime(2024, 1, 1, 0, i // 60, i % 60, tzinfo=UTC),
            price=50000.0 + i,
            size=0.1,
            aggressor_side="BUY" if i % 2 == 0 else "SELL",
            trade_id=f"trade_{i}",
        )
        for i in range(num_trades)
    ]

    assert len(trades) == num_trades
    assert all(isinstance(trade, TradeSchema) for trade in trades)
    # Verify each trade has unique timestamp
    timestamps = [trade.timestamp for trade in trades]
    assert len(set(timestamps)) == num_trades
