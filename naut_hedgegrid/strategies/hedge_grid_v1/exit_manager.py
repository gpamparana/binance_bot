"""Exit order management mixin for HedgeGridV1 strategy.

Provides OCO-like TP/SL cancellation and bulk exit order cleanup
for grid recentering operations.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _tp_sl_pairs: dict[str, tuple[str, str]]
    _tp_sl_pairs_lock: threading.Lock
    _strategy_name: str
    _venue: Venue
    log: Logger (from Strategy base)
    cache: Cache (from Strategy base)
"""

from nautilus_trader.model.identifiers import ClientOrderId


class ExitManagerMixin:
    """Mixin providing TP/SL exit order lifecycle management."""

    def _cancel_counterpart_exit(self, fill_key: str, filled_type: str) -> None:
        """Cancel the counterpart TP/SL order after one side fills (OCO-like behavior)."""
        counterpart_type = "SL" if filled_type == "TP" else "TP"
        with self._tp_sl_pairs_lock:
            pair = self._tp_sl_pairs.pop(fill_key, None)

        if pair is None:
            self.log.debug(f"[OCO] No pair found for {fill_key}, may have been cleared by recenter")
            return

        tp_id, sl_id = pair
        counterpart_id_str = sl_id if filled_type == "TP" else tp_id

        try:
            order = self.cache.order(ClientOrderId(counterpart_id_str))
            if order is not None and order.is_open:
                self.cancel_order(order)
                self.log.info(
                    f"[OCO CANCEL] Cancelled orphaned {counterpart_type} order "
                    f"{counterpart_id_str} after {filled_type} filled for {fill_key}"
                )
            else:
                self.log.debug(f"[OCO] {counterpart_type} order {counterpart_id_str} already closed, no cancel needed")
        except Exception as e:
            self.log.warning(f"[OCO CANCEL] Failed to cancel {counterpart_type} order {counterpart_id_str}: {e}")

    def _cancel_exit_orders_for_side(self, side_abbr: str) -> int:
        """Cancel all live TP/SL exit orders for a specific side.

        Called after an exit fill closes a position to prevent orphaned exit
        orders from opening unwanted positions. For example, if a SHORT TP fills
        and closes the SHORT position, any remaining SHORT SL orders must be
        cancelled to avoid them triggering and opening a new position.

        Parameters
        ----------
        side_abbr : str
            "L" for LONG side or "S" for SHORT side.

        Returns
        -------
        int
            Number of orders cancelled.

        """
        cancelled = 0
        try:
            for order in self.cache.orders_open(venue=self._venue):
                order_id = order.client_order_id.value
                if not order_id.startswith(self._strategy_name):
                    continue
                if f"-TP-{side_abbr}" in order_id or f"-SL-{side_abbr}" in order_id:
                    if order.is_open:
                        self.cancel_order(order)
                        cancelled += 1
                        self.log.info(f"[ORPHAN CLEANUP] Cancelled exit order for flat position: {order_id}")
        except Exception as e:
            self.log.error(f"[ORPHAN CLEANUP] Error cancelling exit orders for side {side_abbr}: {e}")

        if cancelled > 0:
            # Also clear any TP/SL pair tracking for this side
            side_name = "LONG" if side_abbr == "L" else "SHORT"
            with self._tp_sl_pairs_lock:
                keys_to_remove = [k for k in self._tp_sl_pairs if k.startswith(side_name)]
                for k in keys_to_remove:
                    self._tp_sl_pairs.pop(k, None)

        return cancelled

    def _cancel_all_exit_orders(self) -> int:
        """Cancel all live TP/SL exit orders on the exchange.

        Called during grid recenter to prevent orphaned exit orders at stale prices.

        Returns
        -------
        int
            Number of orders cancelled.

        """
        cancelled = 0
        try:
            for order in self.cache.orders_open(venue=self._venue):
                order_id = order.client_order_id.value
                if not order_id.startswith(self._strategy_name):
                    continue
                if "-TP-" in order_id or "-SL-" in order_id:
                    if order.is_open:
                        self.cancel_order(order)
                        cancelled += 1
                        self.log.debug(f"[RECENTER] Cancelled exit order: {order_id}")
        except Exception as e:
            self.log.error(f"[RECENTER] Error cancelling exit orders: {e}")
        return cancelled
