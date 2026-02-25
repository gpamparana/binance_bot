"""Operational control API mixin for HedgeGridV1 strategy.

Provides external control endpoints for kill switch, position flattening,
throttle adjustment, ladder/order snapshots, and position closure.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _kill_switch: Any
    _throttle: float
    _last_long_ladder: Ladder | None
    _last_short_ladder: Ladder | None
    _ladder_lock: threading.Lock
    _last_mid: float | None
    _instrument: Instrument | None
    _strategy_name: str
    _venue: Venue
    instrument_id: InstrumentId
    log: Logger (from Strategy base)
    cache: Cache (from Strategy base)
    clock: Clock (from Strategy base)
"""

from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.identifiers import PositionId

from naut_hedgegrid.domain.types import Ladder, Rung, Side, parse_client_order_id


class OpsControlMixin:
    """Mixin providing operational control endpoints for monitoring and management."""

    def attach_kill_switch(self, kill_switch) -> None:
        """Attach kill switch for monitoring."""
        self._kill_switch = kill_switch
        self.log.info("Kill switch attached to strategy")

    def flatten_side(self, side: str) -> dict:
        """Flatten positions for given side (called by kill switch or API).

        Thread-safe: acquires _ops_lock to prevent concurrent mutations
        from API thread pool racing with on_bar() strategy logic.

        Args:
            side: "long", "short", or "both"

        Returns:
            dict with cancelled orders and closing positions info
        """
        with self._ops_lock:
            result = {
                "cancelled_orders": 0,
                "closing_positions": [],
            }

            sides = ["long", "short"] if side == "both" else [side]

            for s in sides:
                cancelled = self._cancel_side_orders(s)
                result["cancelled_orders"] += cancelled

                position_info = self._close_side_position(s)
                if position_info:
                    result["closing_positions"].append(position_info)

            return result

    def set_throttle(self, throttle: float) -> None:
        """Adjust strategy aggressiveness (0.0 = passive, 1.0 = aggressive).

        Thread-safe: acquires _ops_lock to prevent concurrent mutations.
        """
        with self._ops_lock:
            if not 0.0 <= throttle <= 1.0:
                msg = f"Throttle must be between 0.0 and 1.0, got {throttle}"
                raise ValueError(msg)

            self._throttle = throttle
            self.log.info(f"Throttle set to {throttle:.2f}")

    def _apply_throttle(self, ladder: Ladder) -> Ladder:
        """Scale ladder quantities by throttle factor."""
        if self._throttle >= 1.0:
            return ladder

        scaled_rungs = []
        for rung in ladder.rungs:
            scaled_qty = rung.qty * self._throttle
            if scaled_qty > 0:
                scaled_rungs.append(
                    Rung(
                        price=rung.price,
                        qty=scaled_qty,
                        side=rung.side,
                        tp=rung.tp,
                        sl=rung.sl,
                        tag=rung.tag,
                    )
                )
        return Ladder.from_list(ladder.side, scaled_rungs)

    def get_ladders_snapshot(self) -> dict:
        """Return current grid ladder state for API."""
        with self._ladder_lock:
            if self._last_long_ladder is None and self._last_short_ladder is None:
                return {"long_ladder": [], "short_ladder": [], "mid_price": 0.0}

            return {
                "timestamp": self.clock.timestamp_ns(),
                "mid_price": self._last_mid or 0.0,
                "long_ladder": [
                    {"price": r.price, "qty": r.qty, "side": str(r.side)}
                    for r in (self._last_long_ladder.rungs if self._last_long_ladder else [])
                ],
                "short_ladder": [
                    {"price": r.price, "qty": r.qty, "side": str(r.side)}
                    for r in (self._last_short_ladder.rungs if self._last_short_ladder else [])
                ],
            }

    def get_orders_snapshot(self) -> list[dict]:
        """Return open grid orders for API consumption."""
        orders = self._get_live_grid_orders()
        return [
            {
                "client_order_id": str(o.client_order_id),
                "side": o.side.value,
                "price": o.price,
                "quantity": o.qty,
                "status": o.status,
            }
            for o in orders
        ]

    def _cancel_side_orders(self, side: str) -> int:
        """Cancel all orders for given side (grid + TP/SL).

        Uses typed parsing for grid orders and side-abbreviation matching
        for TP/SL orders (which use a different ID format).
        """
        cancelled = 0
        target_side = Side.LONG if side.upper() == "LONG" else Side.SHORT
        side_abbr = "L" if target_side == Side.LONG else "S"

        open_orders = self.cache.orders_open(venue=self._venue)

        for order in open_orders:
            order_id = order.client_order_id.value
            if not order_id.startswith(self._strategy_name):
                continue
            if not order.is_open:
                continue

            # Try typed parser first (works for grid orders: STRATEGY-SIDE-LEVEL-TS)
            try:
                parsed = parse_client_order_id(order_id)
                if parsed["side"] == target_side:
                    self.cancel_order(order)
                    cancelled += 1
                    continue
            except (ValueError, KeyError):
                pass

            # Fallback for TP/SL orders (format: STRATEGY-TP-L01-TS-COUNTER)
            if f"-TP-{side_abbr}" in order_id or f"-SL-{side_abbr}" in order_id:
                self.cancel_order(order)
                cancelled += 1

        self.log.warning(f"Cancelled {cancelled} {side} orders")
        return cancelled

    def _close_side_position(self, side: str) -> dict | None:
        """Submit market order to close position for given side."""
        if self._instrument is None:
            return None

        position_id_str = f"{self.instrument_id}-{side.upper()}"
        position_id = PositionId(position_id_str)
        position = self.cache.position(position_id)

        if position and position.quantity > 0:
            close_side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY

            # NOTE: reduce_only=False for same reason as TP/SL - see _create_tp_order
            order = self.order_factory.market(
                instrument_id=self._instrument.id,
                order_side=close_side,
                quantity=position.quantity,
                time_in_force=TimeInForce.IOC,
                reduce_only=False,
            )

            # Intentionally bypasses risk gates — flatten is an operator-initiated
            # emergency exit that must execute regardless of drawdown/circuit breaker state
            self.submit_order(order, position_id=position_id)
            self.log.warning(f"Closing {side} position: {position.quantity} @ market")

            return {
                "side": side,
                "size": float(position.quantity),
                "order_id": str(order.client_order_id),
            }

        return None
