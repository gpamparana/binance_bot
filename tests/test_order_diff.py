"""
Idempotency tests for order diff operations.

These tests verify that the order diff system converges to a stable state
after applying operations, and that re-running diff without state changes
produces no additional operations. This is critical for avoiding infinite
update loops in production.
"""

import pytest

from naut_hedgegrid.domain.types import (
    Ladder,
    OrderIntent,
    Rung,
    Side,
    format_client_order_id,
)
from naut_hedgegrid.exchange.precision import InstrumentPrecision, PrecisionGuard
from naut_hedgegrid.strategy.order_sync import LiveOrder, OrderDiff


# ============================================================================
# Test Fixtures and Helpers
# ============================================================================


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


def create_test_diff() -> OrderDiff:
    """Create test order diff engine."""
    return OrderDiff("HG1", create_test_guard())


def apply_diff_operations(
    desired_ladders: list[Ladder],
    live_orders: list[LiveOrder],
    diff_result,
) -> list[LiveOrder]:
    """
    Simulate applying diff operations to update live_orders list.

    This helper simulates what would happen in production when diff operations
    are executed against the exchange.

    Args:
        desired_ladders: Desired ladder state
        live_orders: Current live orders
        diff_result: DiffResult with operations to apply

    Returns:
        Updated list of live orders after applying all operations
    """
    # Make a copy to avoid mutating input
    updated = list(live_orders)

    # Apply cancels - remove orders
    cancelled_ids = {intent.client_order_id for intent in diff_result.cancels}
    updated = [order for order in updated if order.client_order_id not in cancelled_ids]

    # Apply replaces - update existing orders with new price/qty
    replace_map = {
        intent.client_order_id: intent
        for intent in diff_result.replaces
    }
    for i, order in enumerate(updated):
        if order.client_order_id in replace_map:
            intent = replace_map[order.client_order_id]
            # Replace with new order (simulating cancel + create)
            updated[i] = LiveOrder(
                client_order_id=intent.replace_with,  # type: ignore[arg-type]
                side=intent.side,  # type: ignore[arg-type]
                price=intent.price,  # type: ignore[arg-type]
                qty=intent.qty,  # type: ignore[arg-type]
                status="OPEN",
            )

    # Apply adds - create new orders
    for intent in diff_result.adds:
        new_order = LiveOrder(
            client_order_id=intent.client_order_id,
            side=intent.side,  # type: ignore[arg-type]
            price=intent.price,  # type: ignore[arg-type]
            qty=intent.qty,  # type: ignore[arg-type]
            status="OPEN",
        )
        updated.append(new_order)

    return updated


def convert_intents_to_live_orders(add_intents: list[OrderIntent]) -> list[LiveOrder]:
    """
    Convert ADD OrderIntents to LiveOrder objects.

    Args:
        add_intents: List of CREATE intents

    Returns:
        List of LiveOrder objects
    """
    return [
        LiveOrder(
            client_order_id=intent.client_order_id,
            side=intent.side,  # type: ignore[arg-type]
            price=intent.price,  # type: ignore[arg-type]
            qty=intent.qty,  # type: ignore[arg-type]
            status="OPEN",
        )
        for intent in add_intents
        if intent.action == "CREATE"
    ]


def remove_cancelled_orders(
    live_orders: list[LiveOrder],
    cancel_intents: list[OrderIntent],
) -> list[LiveOrder]:
    """
    Remove orders from live list based on CANCEL intents.

    Args:
        live_orders: Current live orders
        cancel_intents: List of CANCEL intents

    Returns:
        Updated list with cancelled orders removed
    """
    cancelled_ids = {intent.client_order_id for intent in cancel_intents}
    return [
        order for order in live_orders
        if order.client_order_id not in cancelled_ids
    ]


def apply_replace_operations(
    live_orders: list[LiveOrder],
    replace_intents: list[OrderIntent],
) -> list[LiveOrder]:
    """
    Update live orders based on REPLACE intents.

    Args:
        live_orders: Current live orders
        replace_intents: List of REPLACE intents

    Returns:
        Updated list with replaced orders
    """
    replace_map = {intent.client_order_id: intent for intent in replace_intents}
    updated = []

    for order in live_orders:
        if order.client_order_id in replace_map:
            intent = replace_map[order.client_order_id]
            # Create new order with updated values
            updated.append(
                LiveOrder(
                    client_order_id=intent.replace_with,  # type: ignore[arg-type]
                    side=intent.side,  # type: ignore[arg-type]
                    price=intent.price,  # type: ignore[arg-type]
                    qty=intent.qty,  # type: ignore[arg-type]
                    status="OPEN",
                )
            )
        else:
            updated.append(order)

    return updated


# ============================================================================
# Basic Idempotency Tests
# ============================================================================


def test_diff_idempotent_empty_state() -> None:
    """
    Test: Running diff([], []) twice produces empty results both times.

    Verifies that the diff of empty state is idempotent.
    """
    diff = create_test_diff()

    result1 = diff.diff([], [])
    result2 = diff.diff([], [])

    assert result1.is_empty, "First diff should be empty"
    assert result2.is_empty, "Second diff should be empty"
    assert len(result1.adds) == len(result2.adds) == 0
    assert len(result1.cancels) == len(result2.cancels) == 0
    assert len(result1.replaces) == len(result2.replaces) == 0


def test_diff_idempotent_matched_orders() -> None:
    """
    Test: Running diff twice with matched orders produces empty results.

    When desired and live orders already match, diff should produce no
    operations on both first and second runs.
    """
    diff = create_test_diff()

    # Create matched desired + live orders
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),
        Rung(price=99.0, qty=0.6, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1234567890",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-02-1234567890",
            side=Side.LONG,
            price=99.0,
            qty=0.6,
            status="OPEN",
        ),
    ]

    result1 = diff.diff([ladder], live)
    result2 = diff.diff([ladder], live)

    assert result1.is_empty, "First diff should be empty"
    assert result2.is_empty, "Second diff should be empty"


# ============================================================================
# Idempotency After Applying Operations
# ============================================================================


def test_diff_idempotent_after_applying_adds() -> None:
    """
    Test: After applying ADD operations, re-running diff produces no operations.

    Steps:
    1. Desired: 3 rungs, Live: []
    2. Run diff → get ADD operations
    3. Simulate applying adds (convert intents to live orders)
    4. Run diff again → should be empty
    """
    diff = create_test_diff()

    # Desired: 3 rungs
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),
        Rung(price=99.0, qty=0.6, side=Side.LONG),
        Rung(price=98.0, qty=0.7, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: empty
    live: list[LiveOrder] = []

    # First diff - should produce adds
    result1 = diff.diff([ladder], live)

    assert len(result1.adds) == 3, "Should create 3 orders"
    assert len(result1.cancels) == 0
    assert len(result1.replaces) == 0

    # Simulate applying adds
    live_after_adds = convert_intents_to_live_orders(list(result1.adds))

    # Second diff - should be empty
    result2 = diff.diff([ladder], live_after_adds)

    assert result2.is_empty, (
        f"Second diff should be empty after applying adds. "
        f"Got: adds={len(result2.adds)}, cancels={len(result2.cancels)}, "
        f"replaces={len(result2.replaces)}"
    )


def test_diff_idempotent_after_applying_cancels() -> None:
    """
    Test: After applying CANCEL operations, re-running diff produces no operations.

    Steps:
    1. Desired: [], Live: 3 orders
    2. Run diff → get CANCEL operations
    3. Simulate applying cancels (remove from live)
    4. Run diff again → should be empty
    """
    diff = create_test_diff()

    # Desired: empty
    # Live: 3 orders
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.0,
            qty=0.5,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-02-2222",
            side=Side.LONG,
            price=99.0,
            qty=0.6,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-03-3333",
            side=Side.LONG,
            price=98.0,
            qty=0.7,
            status="OPEN",
        ),
    ]

    # First diff - should produce cancels
    result1 = diff.diff([], live)

    assert len(result1.adds) == 0
    assert len(result1.cancels) == 3, "Should cancel 3 orders"
    assert len(result1.replaces) == 0

    # Simulate applying cancels
    live_after_cancels = remove_cancelled_orders(live, list(result1.cancels))

    # Second diff - should be empty
    result2 = diff.diff([], live_after_cancels)

    assert result2.is_empty, (
        f"Second diff should be empty after applying cancels. "
        f"Got: adds={len(result2.adds)}, cancels={len(result2.cancels)}, "
        f"replaces={len(result2.replaces)}"
    )


def test_diff_idempotent_after_applying_replaces() -> None:
    """
    Test: After applying REPLACE operations, re-running diff produces no operations.

    Steps:
    1. Desired: price=100.0, Live: price=101.0
    2. Run diff → get REPLACE operation
    3. Simulate applying replace (update live order)
    4. Run diff again → should be empty
    """
    diff = create_test_diff()

    # Desired: 1 rung at 100.0
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: 1 order at 101.0 (mismatched price)
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=101.0,
            qty=0.5,
            status="OPEN",
        )
    ]

    # First diff - should produce replace
    result1 = diff.diff([ladder], live)

    assert len(result1.adds) == 0
    assert len(result1.cancels) == 0
    assert len(result1.replaces) == 1, "Should replace 1 order"

    # Simulate applying replace
    live_after_replace = apply_replace_operations(live, list(result1.replaces))

    # Second diff - should be empty
    result2 = diff.diff([ladder], live_after_replace)

    assert result2.is_empty, (
        f"Second diff should be empty after applying replace. "
        f"Got: adds={len(result2.adds)}, cancels={len(result2.cancels)}, "
        f"replaces={len(result2.replaces)}"
    )


def test_diff_idempotent_complex_scenario() -> None:
    """
    Test: Complex scenario with mix of adds, cancels, and replaces converges.

    Steps:
    1. Mix of operations: adds (2), cancels (1), replaces (1)
    2. Apply all operations
    3. Run diff again → should be empty
    """
    diff = create_test_diff()

    # Desired: 3 rungs
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),  # Matches live level 1
        Rung(price=99.0, qty=0.6, side=Side.LONG),   # Mismatches live level 2 (replace)
        Rung(price=98.0, qty=0.7, side=Side.LONG),   # New (add)
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
            price=99.5,  # Different price - will replace
            qty=0.6,
            status="OPEN",
        ),
        LiveOrder(
            client_order_id="HG1-LONG-99-9999",  # Level 99 not in desired
            side=Side.LONG,
            price=97.0,
            qty=0.8,
            status="OPEN",
        ),  # Will cancel
    ]

    # First diff
    result1 = diff.diff([ladder], live)

    assert len(result1.adds) == 1, "Should add 1 order (98.0)"
    assert len(result1.cancels) == 1, "Should cancel 1 order (97.0)"
    assert len(result1.replaces) == 1, "Should replace 1 order (99.5 -> 99.0)"

    # Apply all operations
    live_after_ops = apply_diff_operations([ladder], live, result1)

    # Second diff - should be empty
    result2 = diff.diff([ladder], live_after_ops)

    assert result2.is_empty, (
        f"Second diff should be empty after applying all operations. "
        f"Got: adds={len(result2.adds)}, cancels={len(result2.cancels)}, "
        f"replaces={len(result2.replaces)}"
    )


# ============================================================================
# Tolerance and Flickering Tests
# ============================================================================


def test_diff_tolerance_stable_no_flickering() -> None:
    """
    Test: Orders within tolerance don't cause flickering.

    Steps:
    1. Create desired + live within tolerance (price diff < 1 bps)
    2. Run diff → empty
    3. Slightly perturb live prices (still within tolerance)
    4. Run diff again → still empty (no flickering)
    """
    # Create diff with 10 bps tolerance
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard, price_tolerance_bps=10.0, qty_tolerance_pct=0.02)

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live with tiny differences (within tolerance)
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.05,  # 0.05% = 5 bps < 10 bps
            qty=0.505,     # 1% < 2%
            status="OPEN",
        )
    ]

    # First diff - should be empty
    result1 = diff.diff([ladder], live)
    assert result1.is_empty, "First diff should be empty (within tolerance)"

    # Perturb live slightly (still within tolerance)
    live_perturbed = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.08,  # 0.08% = 8 bps < 10 bps
            qty=0.508,     # 1.6% < 2%
            status="OPEN",
        )
    ]

    # Second diff - should still be empty
    result2 = diff.diff([ladder], live_perturbed)
    assert result2.is_empty, (
        "Second diff should be empty (still within tolerance, no flickering)"
    )


def test_diff_tolerance_boundary_no_replace() -> None:
    """
    Test: Orders exactly at tolerance boundary don't trigger replace.

    Verifies that tolerance is inclusive (<=, not <).
    """
    guard = create_test_guard()
    diff = OrderDiff("HG1", guard, price_tolerance_bps=10.0, qty_tolerance_pct=0.01)

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live exactly at tolerance boundary
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-01-1111",
            side=Side.LONG,
            price=100.1,  # Exactly 0.1% = 10 bps
            qty=0.505,    # Exactly 1%
            status="OPEN",
        )
    ]

    result = diff.diff([ladder], live)

    # Should not replace at boundary
    assert result.is_empty, (
        "Should not replace at exact tolerance boundary"
    )


# ============================================================================
# Iterative Convergence Tests
# ============================================================================


def test_diff_idempotent_multiple_iterations() -> None:
    """
    Test: Multiple iterations of diff+apply converge to stable state.

    Steps:
    1. Start with mismatched state
    2. Run diff → apply operations → repeat 3 times
    3. Each iteration should produce fewer operations
    4. Final iteration should be empty (converged)
    """
    diff = create_test_diff()

    # Initial desired state
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),
        Rung(price=99.0, qty=0.6, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Initial live state (completely mismatched)
    live = [
        LiveOrder(
            client_order_id="HG1-LONG-99-9999",
            side=Side.LONG,
            price=95.0,
            qty=1.0,
            status="OPEN",
        )
    ]

    # Iteration 1
    result1 = diff.diff([ladder], live)
    assert not result1.is_empty, "First iteration should have operations"
    live = apply_diff_operations([ladder], live, result1)

    # Iteration 2
    result2 = diff.diff([ladder], live)
    assert result2.total_operations <= result1.total_operations, (
        "Second iteration should have fewer or equal operations"
    )
    live = apply_diff_operations([ladder], live, result2)

    # Iteration 3 - should converge
    result3 = diff.diff([ladder], live)
    assert result3.is_empty, (
        f"Should converge after 2 iterations. "
        f"Got: adds={len(result3.adds)}, cancels={len(result3.cancels)}, "
        f"replaces={len(result3.replaces)}"
    )


def test_diff_idempotent_converges_from_empty() -> None:
    """
    Test: Starting from empty live orders converges in one iteration.

    When starting from scratch, one diff+apply cycle should reach stable state.
    """
    diff = create_test_diff()

    # Desired state
    rungs = [
        Rung(price=100.0, qty=0.5, side=Side.LONG),
        Rung(price=99.0, qty=0.6, side=Side.LONG),
        Rung(price=98.0, qty=0.7, side=Side.LONG),
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Start from empty
    live: list[LiveOrder] = []

    # First iteration - create all orders
    result1 = diff.diff([ladder], live)
    assert len(result1.adds) == 3
    live = apply_diff_operations([ladder], live, result1)

    # Second iteration - should be stable
    result2 = diff.diff([ladder], live)
    assert result2.is_empty, "Should be stable after one iteration from empty"


# ============================================================================
# Multi-Side Idempotency Tests
# ============================================================================


def test_diff_idempotent_both_sides() -> None:
    """
    Test: Idempotency with both LONG and SHORT ladders.

    Verifies that diff operations on both sides converge independently
    without cross-contamination.
    """
    diff = create_test_diff()

    # Desired: both sides
    long_ladder = Ladder.from_list(
        Side.LONG,
        [
            Rung(price=99.0, qty=0.5, side=Side.LONG),
            Rung(price=98.0, qty=0.6, side=Side.LONG),
        ],
    )
    short_ladder = Ladder.from_list(
        Side.SHORT,
        [
            Rung(price=101.0, qty=0.5, side=Side.SHORT),
            Rung(price=102.0, qty=0.6, side=Side.SHORT),
        ],
    )

    # Live: empty
    live: list[LiveOrder] = []

    # First diff - create all orders
    result1 = diff.diff([long_ladder, short_ladder], live)
    assert len(result1.adds) == 4, "Should create 2 LONG + 2 SHORT orders"
    live = apply_diff_operations([long_ladder, short_ladder], live, result1)

    # Verify both sides present
    long_orders = [o for o in live if o.side == Side.LONG]
    short_orders = [o for o in live if o.side == Side.SHORT]
    assert len(long_orders) == 2
    assert len(short_orders) == 2

    # Second diff - should be empty
    result2 = diff.diff([long_ladder, short_ladder], live)
    assert result2.is_empty, (
        "Should be stable with both sides after one iteration"
    )


def test_diff_idempotent_one_side_only() -> None:
    """
    Test: Changing only one side doesn't affect the other.

    Verifies that operations on LONG side don't create spurious operations
    on SHORT side.
    """
    diff = create_test_diff()

    # Initial state: both sides with orders
    long_ladder = Ladder.from_list(Side.LONG, [Rung(price=99.0, qty=0.5, side=Side.LONG)])
    short_ladder = Ladder.from_list(Side.SHORT, [Rung(price=101.0, qty=0.5, side=Side.SHORT)])

    live = [
        LiveOrder("HG1-LONG-01-1111", Side.LONG, 99.0, 0.5, "OPEN"),
        LiveOrder("HG1-SHORT-01-2222", Side.SHORT, 101.0, 0.5, "OPEN"),
    ]

    # Verify stable
    result1 = diff.diff([long_ladder, short_ladder], live)
    assert result1.is_empty

    # Change only LONG side (new price)
    long_ladder_modified = Ladder.from_list(Side.LONG, [Rung(price=98.5, qty=0.5, side=Side.LONG)])

    # Should only produce operations on LONG side
    result2 = diff.diff([long_ladder_modified, short_ladder], live)

    # Check operations are only on LONG side
    for intent in result2.adds:
        assert intent.side == Side.LONG, "Adds should only be LONG"
    for intent in result2.cancels:
        order = next((o for o in live if o.client_order_id == intent.client_order_id), None)
        if order:
            assert order.side == Side.LONG, "Cancels should only be LONG"
    for intent in result2.replaces:
        assert intent.side == Side.LONG, "Replaces should only be LONG"


# ============================================================================
# Precision Clamping Idempotency Tests
# ============================================================================


def test_diff_idempotent_precision_clamping() -> None:
    """
    Test: Precision clamping doesn't cause infinite update loops.

    Steps:
    1. Desired rung with unclamped values
    2. Run diff → adds with clamped values
    3. Apply adds (with clamped values in live orders)
    4. Run diff again → should be empty (not trying to add again)
    """
    precision = InstrumentPrecision(
        price_tick=0.5,   # Coarse tick
        qty_step=0.1,     # Coarse step
        min_notional=5.0,
        min_qty=0.1,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    diff = OrderDiff("HG1", guard)

    # Desired: unclamped values
    rungs = [Rung(price=100.123, qty=0.567, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: empty
    live: list[LiveOrder] = []

    # First diff - should create with clamped values
    result1 = diff.diff([ladder], live)
    assert len(result1.adds) == 1

    # Values should be clamped
    add_intent = result1.adds[0]
    assert add_intent.price == pytest.approx(100.0)  # Clamped to 0.5 tick
    assert add_intent.qty == pytest.approx(0.5)      # Clamped down to 0.1 step

    # Apply add - live order now has clamped values
    live = convert_intents_to_live_orders(list(result1.adds))

    # Second diff - should be empty (clamped values match)
    result2 = diff.diff([ladder], live)

    assert result2.is_empty, (
        f"Should not try to update again with clamped values. "
        f"Got: adds={len(result2.adds)}, replaces={len(result2.replaces)}"
    )


def test_diff_idempotent_precision_filtering() -> None:
    """
    Test: Precision-filtered rungs don't cause repeated operations.

    When a rung fails precision guards (e.g., below min_notional), diff should
    not repeatedly try to create it.
    """
    precision = InstrumentPrecision(
        price_tick=0.01,
        qty_step=0.01,
        min_notional=100.0,  # High minimum
        min_qty=0.01,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    diff = OrderDiff("HG1", guard)

    # Desired: one valid, one invalid rung
    rungs = [
        Rung(price=100.0, qty=2.0, side=Side.LONG),  # Valid: 100*2=200 >= 100
        Rung(price=10.0, qty=0.5, side=Side.LONG),   # Invalid: 10*0.5=5 < 100
    ]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: empty
    live: list[LiveOrder] = []

    # First diff - should only add valid rung
    result1 = diff.diff([ladder], live)
    assert len(result1.adds) == 1
    assert result1.adds[0].price == 100.0

    # Apply add
    live = convert_intents_to_live_orders(list(result1.adds))

    # Second diff - should be empty (invalid rung not retried)
    result2 = diff.diff([ladder], live)

    assert result2.is_empty, (
        "Should not retry adding precision-filtered rung"
    )


def test_diff_idempotent_after_precision_boundary_change() -> None:
    """
    Test: Changes at precision boundaries converge correctly.

    When desired price changes by less than one tick, diff should not
    produce operations if within tolerance.
    """
    precision = InstrumentPrecision(
        price_tick=0.1,
        qty_step=0.01,
        min_notional=5.0,
        min_qty=0.01,
        max_qty=1000.0,
    )
    guard = PrecisionGuard(precision=precision)
    # Use 20 bps tolerance (0.2%)
    diff = OrderDiff("HG1", guard, price_tolerance_bps=20.0)

    # Initial desired
    rungs = [Rung(price=100.05, qty=0.5, side=Side.LONG)]  # Clamps to 100.0
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Create initial order
    live: list[LiveOrder] = []
    result1 = diff.diff([ladder], live)
    live = convert_intents_to_live_orders(list(result1.adds))

    # Should have clamped to 100.0
    assert live[0].price == pytest.approx(100.0)

    # Change desired slightly (still clamps to 100.0)
    rungs2 = [Rung(price=100.08, qty=0.5, side=Side.LONG)]  # Still clamps to 100.0
    ladder2 = Ladder.from_list(Side.LONG, rungs2)

    # Should not produce operations (same clamped value)
    result2 = diff.diff([ladder2], live)

    assert result2.is_empty, (
        "Should not update when clamped price remains same"
    )


# ============================================================================
# Edge Cases and Error Conditions
# ============================================================================


def test_diff_idempotent_with_pending_orders() -> None:
    """
    Test: PENDING orders don't interfere with idempotency.

    PENDING orders should be ignored by diff, not causing spurious operations.
    """
    diff = create_test_diff()

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: one OPEN, one PENDING
    live = [
        LiveOrder("HG1-LONG-01-1111", Side.LONG, 100.0, 0.5, "OPEN"),
        LiveOrder("HG1-LONG-02-2222", Side.LONG, 99.0, 0.5, "PENDING"),
    ]

    # First diff - should be empty (ignores PENDING)
    result1 = diff.diff([ladder], live)
    assert result1.is_empty

    # Second diff - should still be empty
    result2 = diff.diff([ladder], live)
    assert result2.is_empty


def test_diff_idempotent_with_malformed_client_order_id() -> None:
    """
    Test: Malformed client_order_ids don't break idempotency.

    Orders with unparseable IDs should be cancelled and not cause loops.
    """
    diff = create_test_diff()

    # Desired
    rungs = [Rung(price=100.0, qty=0.5, side=Side.LONG)]
    ladder = Ladder.from_list(Side.LONG, rungs)

    # Live: malformed ID
    live = [
        LiveOrder("MALFORMED-ID", Side.LONG, 100.0, 0.5, "OPEN")
    ]

    # First diff - should cancel malformed and add correct
    result1 = diff.diff([ladder], live)
    assert len(result1.adds) == 1
    assert len(result1.cancels) == 1

    # Apply operations
    live = apply_diff_operations([ladder], live, result1)

    # Second diff - should be stable
    result2 = diff.diff([ladder], live)
    assert result2.is_empty
