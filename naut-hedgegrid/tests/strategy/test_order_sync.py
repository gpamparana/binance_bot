"""Tests for order synchronization and diffing."""

import pytest

from naut_hedgegrid.domain.types import (
    Ladder,
    Rung,
    Side,
)
from naut_hedgegrid.exchange.precision import InstrumentPrecision, PrecisionGuard
from naut_hedgegrid.strategy.order_sync import LiveOrder, OrderDiff, OrderMatcher

# Fixtures


def create_test_precision() -> InstrumentPrecision:
    """Create test precision parameters."""
    return InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=5.0,
        min_qty=0.001,
        max_qty=1000.0,
    )


def create_test_guard() -> PrecisionGuard:
    """Create test precision guard."""
    return PrecisionGuard(precision=create_test_precision())


# LiveOrder Tests


def test_live_order_creation() -> None:
    """Test LiveOrder creation with valid parameters."""
    order = LiveOrder(
        client_order_id="HG1-LONG-01-1234567890",
        side=Side.LONG,
        price=100.0,
        qty=0.5,
        status="OPEN",
    )

    assert order.client_order_id == "HG1-LONG-01-1234567890"
    assert order.side == Side.LONG
    assert order.price == 100.0
    assert order.qty == 0.5
    assert order.status == "OPEN"


def test_live_order_rejects_empty_id() -> None:
    """Test LiveOrder rejects empty client_order_id."""
    with pytest.raises(ValueError, match="client_order_id is required"):
        LiveOrder(client_order_id="", side=Side.LONG, price=100.0, qty=0.5, status="OPEN")


def test_live_order_rejects_invalid_price() -> None:
    """Test LiveOrder rejects non-positive price."""
    with pytest.raises(ValueError, match="price must be positive"):
        LiveOrder(
            client_order_id="test",
            side=Side.LONG,
            price=0.0,
            qty=0.5,
            status="OPEN",
        )


def test_live_order_rejects_invalid_qty() -> None:
    """Test LiveOrder rejects non-positive quantity."""
    with pytest.raises(ValueError, match="qty must be positive"):
        LiveOrder(
            client_order_id="test",
            side=Side.LONG,
            price=100.0,
            qty=0.0,
            status="OPEN",
        )


# OrderMatcher Tests


def test_order_matcher_initialization() -> None:
    """Test OrderMatcher initialization with default tolerances."""
    matcher = OrderMatcher()

    # Verify matcher works with defaults
    assert matcher.match_price(100.0, 100.005) is True  # 0.5 bps < 1 bps
    assert matcher.match_qty(0.5, 0.505) is True  # 1% = 0.01


def test_order_matcher_custom_tolerances() -> None:
    """Test OrderMatcher with custom tolerances."""
    matcher = OrderMatcher(price_tolerance_bps=5.0, qty_tolerance_pct=0.02)

    # Verify custom tolerances work
    assert matcher.match_price(100.0, 100.04) is True  # 4 bps < 5 bps
    assert matcher.match_qty(0.5, 0.51) is True  # 2% = 0.02


def test_order_matcher_rejects_negative_tolerances() -> None:
    """Test OrderMatcher rejects negative tolerances."""
    with pytest.raises(ValueError, match="price_tolerance_bps must be non-negative"):
        OrderMatcher(price_tolerance_bps=-1.0)

    with pytest.raises(ValueError, match="qty_tolerance_pct must be non-negative"):
        OrderMatcher(qty_tolerance_pct=-0.01)


def test_match_price_exact() -> None:
    """Test price matching with exact match."""
    matcher = OrderMatcher(price_tolerance_bps=1.0)

    assert matcher.match_price(100.0, 100.0) is True


def test_match_price_within_tolerance() -> None:
    """Test price matching within tolerance."""
    matcher = OrderMatcher(price_tolerance_bps=10.0)  # 0.1%

    # 100.05 vs 100.0 = 0.05% = 5 bps < 10 bps tolerance
    assert matcher.match_price(100.05, 100.0) is True

    # 100.15 vs 100.0 = 0.15% = 15 bps > 10 bps tolerance
    assert matcher.match_price(100.15, 100.0) is False


def test_match_price_outside_tolerance() -> None:
    """Test price matching outside tolerance."""
    matcher = OrderMatcher(price_tolerance_bps=1.0)  # 0.01%

    # 100.02 vs 100.0 = 0.02% = 2 bps > 1 bps tolerance
    assert matcher.match_price(100.02, 100.0) is False


def test_match_price_zero_live() -> None:
    """Test price matching handles zero live price."""
    matcher = OrderMatcher()

    assert matcher.match_price(100.0, 0.0) is False


def test_match_qty_exact() -> None:
    """Test quantity matching with exact match."""
    matcher = OrderMatcher(qty_tolerance_pct=0.01)

    assert matcher.match_qty(0.5, 0.5) is True


def test_match_qty_within_tolerance() -> None:
    """Test quantity matching within tolerance."""
    matcher = OrderMatcher(qty_tolerance_pct=0.02)  # 2%

    # 0.505 vs 0.5 = 1% < 2% tolerance
    assert matcher.match_qty(0.505, 0.5) is True

    # 0.515 vs 0.5 = 3% > 2% tolerance
    assert matcher.match_qty(0.515, 0.5) is False


def test_match_qty_outside_tolerance() -> None:
    """Test quantity matching outside tolerance."""
    matcher = OrderMatcher(qty_tolerance_pct=0.01)  # 1%

    # 0.51 vs 0.5 = 2% > 1% tolerance
    assert matcher.match_qty(0.51, 0.5) is False


def test_match_qty_zero_live() -> None:
    """Test quantity matching handles zero live quantity."""
    matcher = OrderMatcher()

    assert matcher.match_qty(0.5, 0.0) is False


def test_matches_all_dimensions() -> None:
    """Test matching checks side, price, and qty."""
    matcher = OrderMatcher(price_tolerance_bps=10.0, qty_tolerance_pct=0.02)

    rung = Rung(price=100.0, qty=0.5, side=Side.LONG)
    order = LiveOrder(
        client_order_id="test",
        side=Side.LONG,
        price=100.05,  # Within tolerance
        qty=0.505,  # Within tolerance
        status="OPEN",
    )

    assert matcher.matches(rung, order) is True


def test_matches_fails_on_side_mismatch() -> None:
    """Test matching fails when sides differ."""
    matcher = OrderMatcher()

    rung = Rung(price=100.0, qty=0.5, side=Side.LONG)
    order = LiveOrder(
        client_order_id="test",
        side=Side.SHORT,  # Wrong side
        price=100.0,
        qty=0.5,
        status="OPEN",
    )

    assert matcher.matches(rung, order) is False


def test_matches_fails_on_price_mismatch() -> None:
    """Test matching fails when price outside tolerance."""
    matcher = OrderMatcher(price_tolerance_bps=1.0)

    rung = Rung(price=100.0, qty=0.5, side=Side.LONG)
    order = LiveOrder(
        client_order_id="test",
        side=Side.LONG,
        price=100.5,  # 0.5% = 50 bps > 1 bps
        qty=0.5,
        status="OPEN",
    )

    assert matcher.matches(rung, order) is False


def test_matches_fails_on_qty_mismatch() -> None:
    """Test matching fails when quantity outside tolerance."""
    matcher = OrderMatcher(qty_tolerance_pct=0.01)

    rung = Rung(price=100.0, qty=0.5, side=Side.LONG)
    order = LiveOrder(
        client_order_id="test",
        side=Side.LONG,
        price=100.0,
        qty=0.6,  # 20% > 1%
        status="OPEN",
    )

    assert matcher.matches(rung, order) is False


# OrderDiff Initialization Tests


def test_order_diff_initialization() -> None:
    """Test OrderDiff initialization."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Verify diff works (initialization successful)
    result = diff.diff([], [])
    assert result.is_empty


def test_order_diff_rejects_empty_strategy_name() -> None:
    """Test OrderDiff rejects empty strategy name."""
    guard = create_test_guard()

    with pytest.raises(ValueError, match="strategy_name is required"):
        OrderDiff("", guard)


# Diff Tests - No Changes


def test_diff_empty_desired_and_live() -> None:
    """Test diff with no desired and no live orders."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    result = diff.diff([], [])

    assert result.is_empty
    assert len(result.adds) == 0
    assert len(result.cancels) == 0
    assert len(result.replaces) == 0


def test_diff_matching_orders_no_changes() -> None:
    """Test diff with matching orders produces no changes."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Create desired ladder
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Create matching live order
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234567890",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    assert result.is_empty


def test_diff_within_tolerance_no_changes() -> None:
    """Test diff with orders within tolerance produces no changes."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard, price_tolerance_bps=10.0, qty_tolerance_pct=0.02)

    # Create desired ladder
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Create live order with slight differences (within tolerance)
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234567890",
            side=Side.LONG,
            price=100.05,  # 0.05% = 5 bps < 10 bps
            qty=0.505,  # 1% < 2%
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    assert result.is_empty


def test_diff_ignores_non_open_orders() -> None:
    """Test diff ignores non-OPEN orders."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Create desired ladder
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Create live orders with various statuses
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="PENDING",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-02-2222",
            side=Side.LONG,
            price=101.0,
            qty=0.5,
            status="FILLED",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-03-3333",
            side=Side.LONG,
            price=102.0,
            qty=0.5,
            status="CANCELED",
        ),
    ]

    result = diff.diff([ladder], live)

    # Should create new order since no OPEN orders match
    assert len(result.adds) == 1
    assert len(result.cancels) == 0


# Diff Tests - Adds


def test_diff_new_desired_rungs_create_adds() -> None:
    """Test diff creates ADD intents for new desired rungs."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Create desired ladder with 2 rungs
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),
        Rung(price=99.0, qty=0.6, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # No live orders
    result = diff.diff([ladder], [])

    assert len(result.adds) == 2
    assert len(result.cancels) == 0
    assert len(result.replaces) == 0

    # Check first add
    assert result.adds[0].action == "CREATE"
    assert result.adds[0].side == Side.LONG
    assert result.adds[0].price == 100.0
    assert result.adds[0].qty == 0.5


def test_diff_generates_unique_client_order_ids() -> None:
    """Test diff generates unique client_order_ids for each rung."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Create desired ladder with 3 rungs
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),
        Rung(price=99.0, qty=0.6, side=Side.LONG),
        Rung(price=98.0, qty=0.7, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    result = diff.diff([ladder], [])

    # Check all client_order_ids are unique
    ids = [intent.client_order_id for intent in result.adds]
    assert len(ids) == len(set(ids))  # All unique

    # Check format includes level
    assert "LONG-01" in ids[0]
    assert "LONG-02" in ids[1]
    assert "LONG-03" in ids[2]


def test_diff_adds_multiple_sides() -> None:
    """Test diff handles adds on both LONG and SHORT sides."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Create ladders for both sides
    long_ladder = Ladder.from_list(Side.LONG, [Rung(price=99.0, qty=0.5, side=Side.LONG)])
    short_ladder = Ladder.from_list(Side.SHORT, [Rung(price=101.0, qty=0.5, side=Side.SHORT)])

    result = diff.diff([long_ladder, short_ladder], [])

    assert len(result.adds) == 2

    # Check sides
    sides = {intent.side for intent in result.adds}
    assert sides == {Side.LONG, Side.SHORT}


def test_diff_filters_invalid_rungs() -> None:
    """Test diff excludes rungs that fail precision guards."""
    # Create precision with high min_notional
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.001,
        min_notional=100.0,  # High minimum
        min_qty=0.001,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    diff = OrderDiff("HG1", guard)

    # Create ladder with one valid and one invalid rung
    rungs = [
        Rung(price=100.0, qty=2.0, side=Side.LONG),  # Valid: 100*2=200 >= 100
        Rung(price=10.0, qty=0.5, side=Side.LONG),  # Invalid: 10*0.5=5 < 100
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    result = diff.diff([ladder], [])

    # Should only add valid rung
    assert len(result.adds) == 1
    assert result.adds[0].price == 100.0


def test_diff_precision_clamps_before_add() -> None:
    """Test diff applies precision clamping before creating adds."""
    precision = InstrumentPrecision(
        price_tick=0.1,
        qty_step=0.01,
        min_notional=5.0,
        min_qty=0.01,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    diff = OrderDiff("HG1", guard)

    # Create rung with unclamped values
    rungs = [Rung(price=100.123, qty=0.567, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    result = diff.diff([ladder], [])

    # Values should be clamped
    assert result.adds[0].price == pytest.approx(100.1)  # Rounded to 0.1 tick
    assert result.adds[0].qty == pytest.approx(0.56)  # Rounded down to 0.01 step


# Diff Tests - Cancels


def test_diff_live_not_in_desired_creates_cancels() -> None:
    """Test diff creates CANCEL intents for live orders not in desired."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # No desired orders
    # Live orders exist
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-02-5678",
            side=Side.LONG,
            price=99.0,
            qty=0.6,
            status="OPEN",
        ),
    ]

    result = diff.diff([], live)

    assert len(result.adds) == 0
    assert len(result.cancels) == 2
    assert len(result.replaces) == 0

    # Check cancel preserves client_order_id
    cancel_ids = {intent.client_order_id for intent in result.cancels}
    assert cancel_ids == {"HG1-LONG-01-1234", "HG1-LONG-02-5678"}


def test_diff_cancels_multiple_sides() -> None:
    """Test diff handles cancels on both sides."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=99.0,
            qty=0.5,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-SHORT-01-2222",
            side=Side.SHORT,
            price=101.0,
            qty=0.5,
            status="OPEN",
        ),
    ]

    result = diff.diff([], live)

    assert len(result.cancels) == 2


def test_diff_preserves_client_order_id_in_cancel() -> None:
    """Test diff preserves exact client_order_id in cancel intent."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    live = [
        LiveOrder(
            client_order_id="CUSTOM-ID-12345",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="OPEN",
        )
    ]

    result = diff.diff([], live)

    assert result.cancels[0].client_order_id == "CUSTOM-ID-12345"


# Diff Tests - Replaces


def test_diff_price_mismatch_creates_replace() -> None:
    """Test diff creates REPLACE for price mismatch."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard, price_tolerance_bps=1.0)

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live with different price (outside tolerance)
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234",
            side=Side.LONG,
            price=100.5,  # 0.5% = 50 bps > 1 bps
            qty=0.5,
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    assert len(result.adds) == 0
    assert len(result.cancels) == 0
    assert len(result.replaces) == 1

    replace = result.replaces[0]
    assert replace.action == "REPLACE"
    assert replace.client_order_id == "HG1-LONG-01-1234"
    assert replace.price == 100.0


def test_diff_qty_mismatch_creates_replace() -> None:
    """Test diff creates REPLACE for quantity mismatch."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard, qty_tolerance_pct=0.01)

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live with different qty (outside tolerance)
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234",
            side=Side.LONG,
            price=100.0,
            qty=0.6,  # 20% > 1%
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    assert len(result.replaces) == 1
    assert result.replaces[0].qty == 0.5


def test_diff_both_mismatch_single_replace() -> None:
    """Test diff creates single REPLACE when both price and qty mismatch."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live with different price and qty
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234",
            side=Side.LONG,
            price=101.0,
            qty=0.6,
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    # Should be single replace, not add+cancel
    assert len(result.replaces) == 1
    assert len(result.adds) == 0
    assert len(result.cancels) == 0


def test_diff_replace_generates_new_id() -> None:
    """Test diff generates new client_order_id for replace."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=101.0,  # Mismatch
            qty=0.5,
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    replace = result.replaces[0]
    assert replace.client_order_id == "HG1-LONG-01-1111"  # Original
    assert replace.replace_with is not None
    assert replace.replace_with != replace.client_order_id  # Different


def test_diff_replace_preserves_level() -> None:
    """Test diff preserves level in replace client_order_id."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Desired at level 1, live at level 3 (mismatch) → cancel+add, not replace
    # Let's make a test where level matches but price differs
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),  # This is level 1
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",  # Level 1 matches
            side=Side.LONG,
            price=101.0,  # Price mismatch
            qty=0.5,
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    # Should be a replace since level matches
    assert len(result.replaces) == 1
    # New ID should maintain level 01
    assert "LONG-01" in result.replaces[0].replace_with  # type: ignore[operator]


# Minimal Churn Tests


def test_diff_reordering_no_churn() -> None:
    """Test diff doesn't churn when levels match regardless of order."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Create ladder with specific order
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),  # Level 1
        Rung(price=99.0, qty=0.6, side=Side.LONG),  # Level 2
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live orders in different order but matching levels
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-02-2222",
            side=Side.LONG,
            price=99.0,  # Level 2
            qty=0.6,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.0,  # Level 1
            qty=0.5,
            status="OPEN",
        ),
    ]

    result = diff.diff([ladder], live)

    # Should match by level+side, no churn
    assert result.is_empty


def test_diff_large_live_small_desired() -> None:
    """Test diff handles mass cancels efficiently."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Small desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Large live set
    live = [
        LiveOrder(
            client_order_id=f"HG1-LONG-{i:02d}-{i*1111}",
            side=Side.LONG,
            price=100.0 - i,
            qty=0.5,
            status="OPEN",
        )
        for i in range(1, 11)  # 10 orders
    ]

    result = diff.diff([ladder], live)

    # Should cancel most, add/keep one
    assert len(result.cancels) == 9  # 10 - 1
    assert len(result.adds) == 0 or len(result.adds) == 1


def test_diff_complex_scenario() -> None:
    """Test diff with mix of add/cancel/replace."""
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard)

    # Desired: 3 rungs
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),  # Matches live
        Rung(price=99.0, qty=0.6, side=Side.LONG),  # Price changed from live
        Rung(price=98.0, qty=0.7, side=Side.LONG),  # New
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: 3 orders
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="OPEN",
        ),  # Matches
        LiveOrder(
            client_order_id="HG1-LONG-02-2222",
            side=Side.LONG,
            price=99.5,  # Different price
            qty=0.6,
            status="OPEN",
        ),  # Will replace
        LiveOrder(
            client_order_id="HG1-LONG-99-9999",
            side=Side.LONG,
            price=97.0,
            qty=0.8,
            status="OPEN",
        ),  # Not in desired → cancel
    ]

    result = diff.diff([ladder], live)

    # Should have: 0 matches, 1 replace, 1 add, 1 cancel
    assert len(result.adds) == 1  # New rung at 98.0
    assert len(result.replaces) == 1  # Price changed at 99.0
    assert len(result.cancels) == 1  # Old order at 97.0
