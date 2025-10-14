"""Order synchronization and diffing for grid trading.

This module provides order reconciliation between desired ladder state and
live exchange orders, minimizing unnecessary order operations through
intelligent matching and tolerance-based comparisons.
"""

from dataclasses import dataclass
from typing import Literal

from naut_hedgegrid.domain.types import (
    DiffResult,
    Ladder,
    OrderIntent,
    Rung,
    Side,
    format_client_order_id,
    parse_client_order_id,
)
from naut_hedgegrid.exchange.precision import PrecisionGuard


@dataclass(frozen=True)
class LiveOrder:
    """
    Representation of a live order on the exchange.

    Captures the essential state of an order for comparison with desired state.
    """

    client_order_id: str
    side: Side
    price: float
    qty: float
    status: Literal["OPEN", "PENDING", "FILLED", "CANCELED"]

    def __post_init__(self) -> None:
        """Validate live order parameters."""
        if not self.client_order_id:
            msg = "client_order_id is required"
            raise ValueError(msg)
        if self.price <= 0:
            msg = f"price must be positive, got {self.price}"
            raise ValueError(msg)
        if self.qty <= 0:
            msg = f"qty must be positive, got {self.qty}"
            raise ValueError(msg)


class OrderMatcher:
    """
    Matcher for comparing desired rungs against live orders.

    Uses tolerance-based matching to avoid unnecessary order updates when
    differences are within acceptable thresholds.
    """

    def __init__(self, price_tolerance_bps: float = 1.0, qty_tolerance_pct: float = 0.01) -> None:
        """Initialize order matcher.

        Args:
            price_tolerance_bps: Price matching tolerance in basis points (default 1 bps = 0.01%)
            qty_tolerance_pct: Quantity matching tolerance as fraction (default 0.01 = 1%)

        """
        if price_tolerance_bps < 0:
            msg = f"price_tolerance_bps must be non-negative, got {price_tolerance_bps}"
            raise ValueError(msg)
        if qty_tolerance_pct < 0:
            msg = f"qty_tolerance_pct must be non-negative, got {qty_tolerance_pct}"
            raise ValueError(msg)

        self._price_tolerance_bps = price_tolerance_bps
        self._qty_tolerance_pct = qty_tolerance_pct

    def match_price(self, desired: float, live: float) -> bool:
        """Check if live price matches desired within tolerance.

        Args:
            desired: Desired price
            live: Live order price

        Returns:
            True if prices match within tolerance, False otherwise

        """
        if live == 0:
            return False

        diff_bps = abs((desired - live) / live) * 10000
        return diff_bps <= self._price_tolerance_bps

    def match_qty(self, desired: float, live: float) -> bool:
        """Check if live quantity matches desired within tolerance.

        Args:
            desired: Desired quantity
            live: Live order quantity

        Returns:
            True if quantities match within tolerance, False otherwise

        """
        if live == 0:
            return False

        diff_pct = abs((desired - live) / live)
        return diff_pct <= self._qty_tolerance_pct

    def matches(self, desired: Rung, live: LiveOrder) -> bool:
        """Check if rung matches live order in all dimensions.

        Args:
            desired: Desired rung
            live: Live order

        Returns:
            True if side, price, and qty all match, False otherwise

        """
        if desired.side != live.side:
            return False
        if not self.match_price(desired.price, live.price):
            return False
        return self.match_qty(desired.qty, live.qty)


class OrderDiff:
    """
    Core diff engine for reconciling desired vs live order state.

    Generates minimal set of operations (add/cancel/replace) needed to
    transition from live state to desired state, applying precision guards
    and intelligent matching to minimize churn.
    """

    def __init__(
        self,
        strategy_name: str,
        precision_guard: PrecisionGuard,
        price_tolerance_bps: float = 1.0,
        qty_tolerance_pct: float = 0.01,
    ) -> None:
        """Initialize order diff engine.

        Args:
            strategy_name: Strategy identifier for client order IDs
            precision_guard: Precision guard for clamping rungs
            price_tolerance_bps: Price matching tolerance
            qty_tolerance_pct: Quantity matching tolerance

        """
        if not strategy_name:
            msg = "strategy_name is required"
            raise ValueError(msg)

        self._strategy_name = strategy_name
        self._precision_guard = precision_guard
        self._matcher = OrderMatcher(price_tolerance_bps, qty_tolerance_pct)

    def diff(self, desired_ladders: list[Ladder], live_orders: list[LiveOrder]) -> DiffResult:
        """Generate diff between desired ladders and live orders.

        Args:
            desired_ladders: Desired ladder state from strategy
            live_orders: Current live orders on exchange

        Returns:
            DiffResult with minimal set of operations needed

        Algorithm:
            1. Flatten ladders to rungs with assigned client_order_ids
            2. Apply precision guards (filter invalid rungs)
            3. Match desired rungs to live orders by level+side
            4. For each desired rung:
               - Find matching live order by level/side
               - If found and matches price/qty → skip
               - If found but mismatches → REPLACE
               - If not found → CREATE
            5. For each unmatched live order → CANCEL

        Notes:
            - Only OPEN orders are diffed (PENDING/FILLED/CANCELED ignored)
            - Precision-filtered rungs are excluded from adds
            - Matching uses tolerance to avoid unnecessary updates
            - Uses level+side to correlate desired with live (not strict client_order_id)

        """
        # Flatten and assign client order IDs to desired rungs
        desired_with_ids = self._assign_client_order_ids(desired_ladders)

        # Apply precision guards (filters out invalid rungs)
        valid_desired = self._apply_precision_guards(desired_with_ids)

        # Filter live orders to only OPEN ones
        open_live_orders = [order for order in live_orders if order.status == "OPEN"]

        # Extract level+side from live orders for matching
        live_by_level_side: dict[tuple[Side, int], LiveOrder] = {}
        for order in open_live_orders:
            try:
                parsed = parse_client_order_id(order.client_order_id)
                key = (parsed["side"], parsed["level"])  # type: ignore[arg-type, index]
                live_by_level_side[key] = order
            except (ValueError, KeyError):
                # Malformed client_order_id → treat as unmatched (will be canceled)
                pass

        # Track which live orders we've matched
        matched_live_ids: set[str] = set()

        # Collect operations
        adds: list[OrderIntent] = []
        replaces: list[OrderIntent] = []
        cancels: list[OrderIntent] = []

        # Process desired rungs
        for client_order_id, desired_rung in valid_desired:
            # Extract level from desired client_order_id
            parsed = parse_client_order_id(client_order_id)
            level = parsed["level"]  # type: ignore[assignment]
            side = desired_rung.side
            key = (side, level)

            if key in live_by_level_side:
                # Found matching live order by level+side
                live_order = live_by_level_side[key]
                matched_live_ids.add(live_order.client_order_id)

                if not self._matcher.matches(desired_rung, live_order):
                    # Mismatch → replace
                    replace_intent = self._create_replace_intent(
                        live_order.client_order_id, desired_rung
                    )
                    replaces.append(replace_intent)
            else:
                # No matching live order → create
                create_intent = self._create_order_intent(client_order_id, desired_rung)
                adds.append(create_intent)

        # Process unmatched live orders → cancel
        for live_order in open_live_orders:
            if live_order.client_order_id not in matched_live_ids:
                cancel_intent = OrderIntent.cancel(live_order.client_order_id)
                cancels.append(cancel_intent)

        return DiffResult.from_lists(adds, cancels, replaces)

    def _assign_client_order_ids(self, ladders: list[Ladder]) -> list[tuple[str, Rung]]:
        """Assign client order IDs to rungs.

        Args:
            ladders: List of ladders

        Returns:
            List of (client_order_id, rung) tuples

        Notes:
            Uses level index (position in ladder) to generate consistent IDs.
            Format: {strategy}-{side}-{level:02d}-{timestamp}

        """
        result: list[tuple[str, Rung]] = []

        for ladder in ladders:
            for level, rung in enumerate(ladder, start=1):
                client_order_id = format_client_order_id(self._strategy_name, rung.side, level)
                result.append((client_order_id, rung))

        return result

    def _apply_precision_guards(
        self, rungs_with_ids: list[tuple[str, Rung]]
    ) -> list[tuple[str, Rung]]:
        """Apply precision guards to filter out invalid rungs.

        Args:
            rungs_with_ids: List of (client_order_id, rung) tuples

        Returns:
            Filtered list with only valid rungs (precision-clamped)

        """
        valid: list[tuple[str, Rung]] = []

        for client_order_id, rung in rungs_with_ids:
            clamped = self._precision_guard.clamp_rung(rung)
            if clamped is not None:
                valid.append((client_order_id, clamped))
            # else: filtered out (e.g., below min notional)

        return valid

    def _create_order_intent(self, client_order_id: str, rung: Rung) -> OrderIntent:
        """Create OrderIntent for new order.

        Args:
            client_order_id: Client order ID
            rung: Rung with order parameters

        Returns:
            OrderIntent.create with rung parameters

        """
        return OrderIntent.create(
            client_order_id=client_order_id,
            side=rung.side,
            price=rung.price,
            qty=rung.qty,
            metadata={"tag": rung.tag} if rung.tag else {},
        )

    def _create_replace_intent(self, client_order_id: str, new_rung: Rung) -> OrderIntent:
        """Create OrderIntent for replacing an order.

        Args:
            client_order_id: Original client order ID to replace
            new_rung: New rung with updated parameters

        Returns:
            OrderIntent.replace with new parameters

        """
        # Generate new client_order_id for replacement
        parsed = parse_client_order_id(client_order_id)
        new_client_order_id = format_client_order_id(
            strategy=parsed["strategy"],  # type: ignore[arg-type]
            side=parsed["side"],  # type: ignore[arg-type]
            level=parsed["level"],  # type: ignore[arg-type]
        )

        return OrderIntent.replace(
            client_order_id=client_order_id,
            replace_with=new_client_order_id,
            side=new_rung.side,
            price=new_rung.price,
            qty=new_rung.qty,
            metadata={"tag": new_rung.tag} if new_rung.tag else {},
        )
