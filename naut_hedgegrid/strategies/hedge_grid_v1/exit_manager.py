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
