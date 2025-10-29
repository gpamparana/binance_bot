"""Order synchronization and diffing for grid trading.

This module provides order reconciliation between desired ladder state and
live exchange orders, minimizing unnecessary order operations through
intelligent matching and tolerance-based comparisons.
"""

import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime
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

logger = logging.getLogger(__name__)


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

        # Add caching to avoid unnecessary recalculations
        self._last_desired_hash: int | None = None
        self._last_live_hash: int | None = None
        self._last_result: DiffResult | None = None

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
        # Check cache first - avoid recalculation if inputs haven't changed
        desired_hash = hash(str(desired_ladders))
        live_hash = hash(str(live_orders))

        if (self._last_desired_hash == desired_hash and
            self._last_live_hash == live_hash and
            self._last_result is not None):
            # Return cached result if inputs haven't changed
            return self._last_result

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

        # Create result
        result = DiffResult.from_lists(adds, cancels, replaces)

        # Cache the result for next call
        self._last_desired_hash = desired_hash
        self._last_live_hash = live_hash
        self._last_result = result

        return result

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


@dataclass(frozen=True)
class RetryAttempt:
    """
    Record of a single post-only retry attempt.

    Tracks each retry attempt for debugging and monitoring purposes.
    """

    attempt_number: int
    original_price: float
    adjusted_price: float
    reason: str
    timestamp_ms: int


class PostOnlyRetryHandler:
    """
    Handles post-only order retry logic with bounded attempts.

    When a post-only order is rejected because it would cross the spread
    (execute as taker), this handler:
    1. Detects if rejection is retry-able
    2. Adjusts price by N ticks AWAY from spread
    3. Tracks retry attempts
    4. Enforces maximum retry limit

    Price Adjustment Logic:
    - LONG (buy) orders: Decrease price by N ticks (move down, away from ask)
    - SHORT (sell) orders: Increase price by N ticks (move up, away from bid)
    - This makes the order more passive and increases chance of being maker
    """

    def __init__(
        self,
        precision_guard: PrecisionGuard,
        max_attempts: int = 3,
        enabled: bool = True,
    ) -> None:
        """
        Initialize retry handler.

        Args:
            precision_guard: Precision guard for price clamping
            max_attempts: Maximum number of retry attempts (default 3)
            enabled: Whether retry logic is enabled (default True)
        """
        if max_attempts < 0:
            msg = f"max_attempts must be non-negative, got {max_attempts}"
            raise ValueError(msg)

        self._precision_guard = precision_guard
        self._max_attempts = max_attempts
        self._enabled = enabled
        self._retry_history: dict[str, list[RetryAttempt]] = {}

    @property
    def enabled(self) -> bool:
        """Check if retry logic is enabled."""
        return self._enabled and self._max_attempts > 0

    def should_retry(self, rejection_reason: str) -> bool:
        """
        Check if rejection is retry-able (post-only would trade).

        Detects common rejection messages indicating the order would
        execute immediately as a taker order instead of maker.

        Args:
            rejection_reason: Rejection reason from exchange

        Returns:
            True if rejection indicates post-only crossing spread
        """
        if not self._enabled:
            return False

        reason_lower = rejection_reason.lower()

        # Common post-only rejection patterns from various exchanges
        # Note: NautilusTrader backtest uses "POST_ONLY" (underscore) and "TAKER"
        post_only_patterns = [
            "post-only",                    # Binance format (hyphen)
            "post only",                    # Generic format (space)
            "post_only",                    # NautilusTrader format (underscore)
            "would be filled immediately",  # Common pattern
            "would immediately match",      # Common pattern
            "would execute as taker",       # Common pattern
            "would have been a taker",      # NautilusTrader backtest format
            "would take liquidity",         # Common pattern
            "would cross",                  # Common pattern
            "taker",                        # Generic catch-all for taker rejections
        ]

        return any(pattern in reason_lower for pattern in post_only_patterns)

    def adjust_price_for_retry(
        self,
        original_price: float,
        side: Side,
        attempt: int,
    ) -> float:
        """
        Adjust price by N ticks away from spread for retry.

        Makes the order more passive to avoid crossing spread.

        Args:
            original_price: Original order price that was rejected
            side: Order side (LONG for buy, SHORT for sell)
            attempt: Retry attempt number (1, 2, 3, ...)

        Returns:
            Adjusted price clamped to tick boundaries

        Logic:
            - LONG (buy): price -= (tick_size × attempt)
            - SHORT (sell): price += (tick_size × attempt)
        """
        tick_size = self._precision_guard.precision.price_tick

        # Calculate adjustment (negative for LONG, positive for SHORT)
        adjustment = tick_size * attempt

        if side == Side.LONG:
            # Buy orders: move DOWN (away from ask)
            adjusted = original_price - adjustment
        else:
            # Sell orders: move UP (away from bid)
            adjusted = original_price + adjustment

        # Clamp to valid tick boundary
        return self._precision_guard.clamp_price(adjusted)

    def record_attempt(
        self,
        client_order_id: str,
        attempt: int,
        original_price: float,
        adjusted_price: float,
        reason: str,
    ) -> None:
        """
        Record retry attempt for logging and debugging.

        Args:
            client_order_id: Order client ID
            attempt: Attempt number
            original_price: Original price before adjustment
            adjusted_price: Price after adjustment
            reason: Rejection reason
        """
        from datetime import UTC

        timestamp_ms = int(datetime.now(tz=UTC).timestamp() * 1000)

        retry_attempt = RetryAttempt(
            attempt_number=attempt,
            original_price=original_price,
            adjusted_price=adjusted_price,
            reason=reason,
            timestamp_ms=timestamp_ms,
        )

        if client_order_id not in self._retry_history:
            self._retry_history[client_order_id] = []

        self._retry_history[client_order_id].append(retry_attempt)

        logger.info(
            f"Retry attempt {attempt} for {client_order_id}: "
            f"{original_price} -> {adjusted_price} (reason: {reason})"
        )

    def get_retry_history(self, client_order_id: str) -> list[RetryAttempt]:
        """
        Get all retry attempts for a given order.

        Args:
            client_order_id: Order client ID

        Returns:
            List of RetryAttempt records (empty if none)
        """
        return self._retry_history.get(client_order_id, [])

    def clear_history(self, client_order_id: str) -> None:
        """
        Clear retry history for an order (after success or abandonment).

        Args:
            client_order_id: Order client ID
        """
        self._retry_history.pop(client_order_id, None)
