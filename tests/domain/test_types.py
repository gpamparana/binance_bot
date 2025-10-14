"""Tests for domain types."""

import time

import pytest

from naut_hedgegrid.domain.types import (
    DiffResult,
    Ladder,
    OrderIntent,
    Regime,
    Rung,
    Side,
    format_client_order_id,
    parse_client_order_id,
)

# Regime Tests


def test_regime_enum_values() -> None:
    """Test Regime enum has expected values."""
    assert Regime.UP.value == "UP"
    assert Regime.DOWN.value == "DOWN"
    assert Regime.SIDEWAYS.value == "SIDEWAYS"


def test_regime_string_representation() -> None:
    """Test Regime string conversion."""
    assert str(Regime.UP) == "UP"
    assert str(Regime.DOWN) == "DOWN"
    assert str(Regime.SIDEWAYS) == "SIDEWAYS"


def test_regime_from_string() -> None:
    """Test creating Regime from string."""
    assert Regime.from_string("UP") == Regime.UP
    assert Regime.from_string("up") == Regime.UP
    assert Regime.from_string("Down") == Regime.DOWN


def test_regime_from_string_invalid() -> None:
    """Test invalid regime string raises error."""
    with pytest.raises(ValueError, match="Invalid regime"):
        Regime.from_string("INVALID")


# Side Tests


def test_side_enum_values() -> None:
    """Test Side enum has expected values."""
    assert Side.LONG.value == "LONG"
    assert Side.SHORT.value == "SHORT"


def test_side_string_representation() -> None:
    """Test Side string conversion."""
    assert str(Side.LONG) == "LONG"
    assert str(Side.SHORT) == "SHORT"


def test_side_opposite() -> None:
    """Test getting opposite side."""
    assert Side.LONG.opposite == Side.SHORT
    assert Side.SHORT.opposite == Side.LONG


def test_side_from_string() -> None:
    """Test creating Side from string."""
    assert Side.from_string("LONG") == Side.LONG
    assert Side.from_string("long") == Side.LONG
    assert Side.from_string("Short") == Side.SHORT


def test_side_from_string_invalid() -> None:
    """Test invalid side string raises error."""
    with pytest.raises(ValueError, match="Invalid side"):
        Side.from_string("BUY")


# Rung Tests


def test_rung_creation() -> None:
    """Test basic Rung creation."""
    rung = Rung(price=100.0, qty=0.1, side=Side.LONG, tp=102.0, sl=98.0, tag="test")

    assert rung.price == 100.0
    assert rung.qty == 0.1
    assert rung.side == Side.LONG
    assert rung.tp == 102.0
    assert rung.sl == 98.0
    assert rung.tag == "test"


def test_rung_minimal_creation() -> None:
    """Test Rung creation with minimal parameters."""
    rung = Rung(price=100.0, qty=0.1, side=Side.SHORT)

    assert rung.price == 100.0
    assert rung.qty == 0.1
    assert rung.side == Side.SHORT
    assert rung.tp is None
    assert rung.sl is None
    assert rung.tag == ""


def test_rung_frozen() -> None:
    """Test Rung is immutable."""
    rung = Rung(price=100.0, qty=0.1, side=Side.LONG)

    with pytest.raises((AttributeError, TypeError)):  # FrozenInstanceError
        rung.price = 101.0  # type: ignore[misc]


def test_rung_invalid_price() -> None:
    """Test Rung validation for invalid price."""
    with pytest.raises(ValueError, match="Price must be positive"):
        Rung(price=0.0, qty=0.1, side=Side.LONG)

    with pytest.raises(ValueError, match="Price must be positive"):
        Rung(price=-100.0, qty=0.1, side=Side.LONG)


def test_rung_invalid_qty() -> None:
    """Test Rung validation for invalid quantity."""
    with pytest.raises(ValueError, match="Quantity must be positive"):
        Rung(price=100.0, qty=0.0, side=Side.LONG)

    with pytest.raises(ValueError, match="Quantity must be positive"):
        Rung(price=100.0, qty=-0.1, side=Side.LONG)


def test_rung_invalid_tp() -> None:
    """Test Rung validation for invalid take profit."""
    with pytest.raises(ValueError, match="Take profit must be positive"):
        Rung(price=100.0, qty=0.1, side=Side.LONG, tp=0.0)


def test_rung_invalid_sl() -> None:
    """Test Rung validation for invalid stop loss."""
    with pytest.raises(ValueError, match="Stop loss must be positive"):
        Rung(price=100.0, qty=0.1, side=Side.LONG, sl=-10.0)


def test_rung_with_tag() -> None:
    """Test creating new Rung with updated tag."""
    rung1 = Rung(price=100.0, qty=0.1, side=Side.LONG, tag="old")
    rung2 = rung1.with_tag("new")

    assert rung1.tag == "old"
    assert rung2.tag == "new"
    assert rung2.price == rung1.price
    assert rung2.qty == rung1.qty


def test_rung_distance_from() -> None:
    """Test calculating distance from price."""
    rung = Rung(price=100.0, qty=0.1, side=Side.LONG)

    assert rung.distance_from(100.0) == 0.0
    assert rung.distance_from(105.0) == 5.0
    assert rung.distance_from(95.0) == 5.0


def test_rung_distance_bps_from() -> None:
    """Test calculating distance in basis points."""
    rung = Rung(price=100.0, qty=0.1, side=Side.LONG)

    assert rung.distance_bps_from(100.0) == 0.0
    # Distance from 101 to 100 is 1, relative to 101 = 1/101 = 0.0099 = 99 bps
    assert rung.distance_bps_from(101.0) == pytest.approx(99.0099, rel=1e-3)
    # Distance from 102 to 100 is 2, relative to 102 = 2/102 = 0.0196 = 196 bps
    assert rung.distance_bps_from(102.0) == pytest.approx(196.0784, rel=1e-3)


def test_rung_distance_bps_from_zero_price() -> None:
    """Test distance calculation handles zero price."""
    rung = Rung(price=100.0, qty=0.1, side=Side.LONG)
    assert rung.distance_bps_from(0.0) == 0.0


# Ladder Tests


def test_ladder_creation() -> None:
    """Test basic Ladder creation."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.1, side=Side.LONG),
    ]
    ladder = Ladder(side=Side.LONG, rungs=tuple(rungs))

    assert ladder.side == Side.LONG
    assert len(ladder) == 2


def test_ladder_empty() -> None:
    """Test empty Ladder creation."""
    ladder = Ladder(side=Side.SHORT)

    assert ladder.side == Side.SHORT
    assert len(ladder) == 0
    assert ladder.is_empty


def test_ladder_from_list() -> None:
    """Test creating Ladder from list."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.1, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    assert len(ladder) == 2
    assert ladder.side == Side.LONG


def test_ladder_invalid_side_mismatch() -> None:
    """Test Ladder validation for side mismatch."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.1, side=Side.SHORT),  # Wrong side
    ]

    with pytest.raises(ValueError, match="All rungs must have side"):
        Ladder(side=Side.LONG, rungs=tuple(rungs))


def test_ladder_iteration() -> None:
    """Test iterating over ladder rungs."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.2, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    prices = [r.price for r in ladder]
    assert prices == [100.0, 99.0]


def test_ladder_indexing() -> None:
    """Test ladder indexing."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.2, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    assert ladder[0].price == 100.0
    assert ladder[1].price == 99.0


def test_ladder_sorted_by_price() -> None:
    """Test sorting ladder by price."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.2, side=Side.LONG),
        Rung(price=101.0, qty=0.15, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    sorted_asc = ladder.sorted_by_price(ascending=True)
    assert [r.price for r in sorted_asc] == [99.0, 100.0, 101.0]

    sorted_desc = ladder.sorted_by_price(ascending=False)
    assert [r.price for r in sorted_desc] == [101.0, 100.0, 99.0]


def test_ladder_filter_by_tag() -> None:
    """Test filtering ladder by tag."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG, tag="active"),
        Rung(price=99.0, qty=0.2, side=Side.LONG, tag="pending"),
        Rung(price=101.0, qty=0.15, side=Side.LONG, tag="active"),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    active = ladder.filter_by_tag("active")
    assert len(active) == 2
    assert all(r.tag == "active" for r in active)


def test_ladder_total_qty() -> None:
    """Test calculating total quantity."""
    rungs = [
        Rung(price=100.0, qty=0.1, side=Side.LONG),
        Rung(price=99.0, qty=0.2, side=Side.LONG),
        Rung(price=101.0, qty=0.15, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    assert ladder.total_qty() == pytest.approx(0.45, rel=1e-6)


# OrderIntent Tests


def test_order_intent_create() -> None:
    """Test creating order creation intent."""
    intent = OrderIntent.create(
        client_order_id="test-001",
        side=Side.LONG,
        price=100.0,
        qty=0.1,
        metadata={"level": "5"},
    )

    assert intent.action == "CREATE"
    assert intent.client_order_id == "test-001"
    assert intent.side == Side.LONG
    assert intent.price == 100.0
    assert intent.qty == 0.1
    assert intent.metadata == {"level": "5"}


def test_order_intent_cancel() -> None:
    """Test creating order cancellation intent."""
    intent = OrderIntent.cancel(client_order_id="test-001")

    assert intent.action == "CANCEL"
    assert intent.client_order_id == "test-001"
    assert intent.side is None
    assert intent.price is None


def test_order_intent_replace() -> None:
    """Test creating order replacement intent."""
    intent = OrderIntent.replace(
        client_order_id="test-001",
        replace_with="test-002",
        side=Side.SHORT,
        price=99.0,
        qty=0.2,
    )

    assert intent.action == "REPLACE"
    assert intent.client_order_id == "test-001"
    assert intent.replace_with == "test-002"
    assert intent.side == Side.SHORT
    assert intent.price == 99.0
    assert intent.qty == 0.2


def test_order_intent_create_validation() -> None:
    """Test CREATE intent validation."""
    # Missing side
    with pytest.raises(ValueError, match="side is required"):
        OrderIntent(action="CREATE", client_order_id="test-001", price=100.0, qty=0.1)

    # Missing price
    with pytest.raises(ValueError, match="Valid price is required"):
        OrderIntent(action="CREATE", client_order_id="test-001", side=Side.LONG, qty=0.1)

    # Invalid price
    with pytest.raises(ValueError, match="Valid price is required"):
        OrderIntent(action="CREATE", client_order_id="test-001", side=Side.LONG, price=0.0, qty=0.1)

    # Missing qty
    with pytest.raises(ValueError, match="Valid qty is required"):
        OrderIntent(action="CREATE", client_order_id="test-001", side=Side.LONG, price=100.0)


def test_order_intent_replace_validation() -> None:
    """Test REPLACE intent validation."""
    with pytest.raises(ValueError, match="replace_with is required"):
        OrderIntent(
            action="REPLACE",
            client_order_id="test-001",
            side=Side.LONG,
            price=100.0,
            qty=0.1,
        )


def test_order_intent_missing_client_order_id() -> None:
    """Test validation for missing client_order_id."""
    with pytest.raises(ValueError, match="client_order_id is required"):
        OrderIntent(action="CANCEL", client_order_id="")


# DiffResult Tests


def test_diff_result_creation() -> None:
    """Test basic DiffResult creation."""
    adds = [OrderIntent.create("add-1", Side.LONG, 100.0, 0.1)]
    cancels = [OrderIntent.cancel("cancel-1")]
    replaces = [OrderIntent.replace("old-1", "new-1", Side.SHORT, 99.0, 0.2)]

    diff = DiffResult(
        adds=tuple(adds),
        cancels=tuple(cancels),
        replaces=tuple(replaces),
    )

    assert len(diff.adds) == 1
    assert len(diff.cancels) == 1
    assert len(diff.replaces) == 1


def test_diff_result_from_lists() -> None:
    """Test creating DiffResult from lists."""
    adds = [OrderIntent.create("add-1", Side.LONG, 100.0, 0.1)]
    cancels = [OrderIntent.cancel("cancel-1")]
    replaces = []

    diff = DiffResult.from_lists(adds, cancels, replaces)

    assert len(diff.adds) == 1
    assert len(diff.cancels) == 1
    assert len(diff.replaces) == 0


def test_diff_result_empty() -> None:
    """Test empty DiffResult."""
    diff = DiffResult()

    assert diff.is_empty
    assert diff.total_operations == 0
    assert len(diff) == 0


def test_diff_result_total_operations() -> None:
    """Test counting total operations."""
    adds = [
        OrderIntent.create("add-1", Side.LONG, 100.0, 0.1),
        OrderIntent.create("add-2", Side.LONG, 99.0, 0.1),
    ]
    cancels = [OrderIntent.cancel("cancel-1")]
    replaces = [OrderIntent.replace("old-1", "new-1", Side.SHORT, 99.0, 0.2)]

    diff = DiffResult.from_lists(adds, cancels, replaces)

    assert diff.total_operations == 4
    assert len(diff) == 4


# Client Order ID Helpers Tests


def test_format_client_order_id() -> None:
    """Test formatting client order ID."""
    order_id = format_client_order_id("HG1", Side.LONG, 5, timestamp=1234567890123)

    assert order_id == "HG1-LONG-05-1234567890123"


def test_format_client_order_id_auto_timestamp() -> None:
    """Test formatting with automatic timestamp."""
    before = int(time.time() * 1000)
    order_id = format_client_order_id("HG1", Side.SHORT, 10)
    after = int(time.time() * 1000)

    parts = order_id.split("-")
    assert len(parts) == 4
    assert parts[0] == "HG1"
    assert parts[1] == "SHORT"
    assert parts[2] == "10"

    timestamp = int(parts[3])
    assert before <= timestamp <= after


def test_format_client_order_id_zero_padding() -> None:
    """Test level number zero-padding."""
    order_id = format_client_order_id("HG1", Side.LONG, 3, timestamp=123)
    assert "03" in order_id

    order_id = format_client_order_id("HG1", Side.LONG, 15, timestamp=123)
    assert "15" in order_id


def test_parse_client_order_id() -> None:
    """Test parsing client order ID."""
    order_id = "HG1-LONG-05-1234567890123"
    parsed = parse_client_order_id(order_id)

    assert parsed["strategy"] == "HG1"
    assert parsed["side"] == Side.LONG
    assert parsed["level"] == 5
    assert parsed["timestamp"] == 1234567890123


def test_parse_client_order_id_invalid_format() -> None:
    """Test parsing invalid format raises error."""
    with pytest.raises(ValueError, match="Invalid client order ID format"):
        parse_client_order_id("INVALID")

    with pytest.raises(ValueError, match="Invalid client order ID format"):
        parse_client_order_id("HG1-LONG-05")  # Missing timestamp


def test_parse_client_order_id_invalid_components() -> None:
    """Test parsing with invalid components."""
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_client_order_id("HG1-INVALID-05-123")  # Invalid side

    with pytest.raises(ValueError, match="Failed to parse"):
        parse_client_order_id("HG1-LONG-ABC-123")  # Invalid level

    with pytest.raises(ValueError, match="Failed to parse"):
        parse_client_order_id("HG1-LONG-05-ABC")  # Invalid timestamp


def test_format_parse_roundtrip() -> None:
    """Test format -> parse roundtrip."""
    original_id = format_client_order_id("HG1", Side.SHORT, 7, timestamp=999888777)
    parsed = parse_client_order_id(original_id)

    assert parsed["strategy"] == "HG1"
    assert parsed["side"] == Side.SHORT
    assert parsed["level"] == 7
    assert parsed["timestamp"] == 999888777

    # Reconstruct and compare
    reconstructed = format_client_order_id(
        parsed["strategy"],  # type: ignore[arg-type]
        parsed["side"],  # type: ignore[arg-type]
        parsed["level"],  # type: ignore[arg-type]
        timestamp=parsed["timestamp"],  # type: ignore[arg-type]
    )
    assert reconstructed == original_id
