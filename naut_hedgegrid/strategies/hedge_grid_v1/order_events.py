"""Order event handling mixin for HedgeGridV1 strategy.

Provides callbacks for order lifecycle events: accepted, canceled, rejected,
denied, expired, and cancel-rejected. Manages retry logic for post-only
rejections, circuit breaker integration, and grid cache maintenance.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _strategy_name: str
    _pending_retries: dict[str, OrderIntent]
    _retry_handler: PostOnlyRetryHandler | None
    _processed_rejections: set[str]
    _rejections_lock: threading.Lock
    _fills_lock: threading.Lock
    _fills_with_exits: set[str]
    _grid_orders_lock: threading.Lock
    _grid_orders_cache: dict[str, LiveOrder]
    _hedge_grid_config: HedgeGridConfig | None
    _pause_trading: bool
    _circuit_breaker_active: bool
    instrument_id: InstrumentId
    log: Logger (from Strategy base)
    cache: Cache (from Strategy base)
    clock: Clock (from Strategy base)
"""

from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderCancelRejected,
    OrderExpired,
    OrderRejected,
)

from naut_hedgegrid.domain.types import Side
from naut_hedgegrid.strategy.order_sync import LiveOrder


class OrderEventsMixin:
    """Mixin providing order lifecycle event handlers."""

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """Handle order accepted event - remove from retry queue on success."""
        client_order_id = str(event.client_order_id.value)

        # Remove from pending retries (success!)
        if client_order_id in self._pending_retries:
            intent = self._pending_retries[client_order_id]
            if intent.retry_count > 0:
                self.log.info(
                    f"Order {client_order_id} accepted after {intent.retry_count} retries "
                    f"(original price: {intent.original_price}, final price: {intent.price})"
                )
            del self._pending_retries[client_order_id]
            if self._retry_handler is not None:
                self._retry_handler.clear_history(client_order_id)

        # Log order acceptance with detail for TP/SL orders
        order_id = event.client_order_id.value
        if order_id.startswith(self._strategy_name):
            if "-TP-" in order_id:
                self.log.info(f"[TP ACCEPTED] Take-profit order accepted: {event.client_order_id}")
            elif "-SL-" in order_id:
                self.log.info(f"[SL ACCEPTED] Stop-loss order accepted: {event.client_order_id}")
            else:
                self.log.debug(f"Grid order accepted: {event.client_order_id}")

                # Add grid order to internal cache for O(1) lookups
                order = self.cache.order(event.client_order_id)
                if order:
                    try:
                        parsed = self._parse_cached_order_id(client_order_id)
                        live_order = LiveOrder(
                            client_order_id=client_order_id,
                            side=parsed["side"],  # type: ignore[arg-type]
                            price=float(order.price) if hasattr(order, "price") else 0.0,
                            qty=float(order.quantity),
                            status="OPEN",
                        )
                        with self._grid_orders_lock:
                            self._grid_orders_cache[client_order_id] = live_order
                    except (ValueError, KeyError) as e:
                        self.log.warning(f"Could not parse order for caching: {e}")

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """Handle order canceled event."""
        client_order_id = str(event.client_order_id.value)
        if client_order_id.startswith(self._strategy_name):
            self.log.debug(f"Order canceled: {event.client_order_id}")

            # Remove grid order from internal cache
            if "-TP-" not in client_order_id and "-SL-" not in client_order_id:
                with self._grid_orders_lock:
                    self._grid_orders_cache.pop(client_order_id, None)

    def on_order_rejected(self, event: OrderRejected) -> None:
        """Handle order rejection with retry logic for post-only failures.

        When a post-only order is rejected because it would cross the spread,
        this handler:
        1. Adjusts price by one tick away from spread
        2. Retries up to N times (configured)
        3. Logs each attempt with reason
        4. Abandons order after max attempts exhausted
        """
        try:
            client_order_id = str(event.client_order_id.value)
            rejection_reason = str(event.reason) if hasattr(event, "reason") else "Unknown"

            # Idempotency check: prevent duplicate processing of same rejection event
            rejection_key = f"{client_order_id}_{event.ts_event}"
            with self._rejections_lock:
                if rejection_key in self._processed_rejections:
                    return
                self._processed_rejections.add(rejection_key)

                # Clean up old rejection keys (keep only last 100 to prevent memory leak)
                if len(self._processed_rejections) > 100:
                    to_remove = list(self._processed_rejections)[:-50]
                    for key in to_remove:
                        self._processed_rejections.discard(key)

            # Also clean up pending_retries if it gets too large
            if len(self._pending_retries) > 50:
                self.log.warning(
                    f"Pending retries queue too large ({len(self._pending_retries)}), cleaning up old entries"
                )
                keys_to_remove = list(self._pending_retries.keys())[:-25]
                for key in keys_to_remove:
                    del self._pending_retries[key]

            # Enhanced logging for TP/SL rejections and cleanup to allow retry
            if "-TP-" in client_order_id or "-SL-" in client_order_id:
                order_type = "TP" if "-TP-" in client_order_id else "SL"
                self.log.error(
                    f"[{order_type} REJECTED] {order_type} order rejected: "
                    f"{client_order_id}, reason: {rejection_reason}"
                )

                # Extract fill_key from order ID to allow retry
                # Order ID format: HG1-TP-L01-timestamp-counter or HG1-SL-S05-timestamp-counter
                try:
                    parts = client_order_id.split("-")
                    if len(parts) >= 3:
                        side_level_part = parts[2]  # e.g., "L01" or "S05"
                        if len(side_level_part) >= 2:
                            side_abbr = side_level_part[0]  # "L" or "S"
                            level_str = side_level_part[1:]  # "01" or "05"
                            side = "LONG" if side_abbr == "L" else "SHORT"
                            level = int(level_str)
                            fill_key = f"{side}-{level}"

                            # Remove from tracking to allow retry on next fill
                            with self._fills_lock:
                                if fill_key in self._fills_with_exits:
                                    self._fills_with_exits.discard(fill_key)
                                    self.log.info(
                                        f"[{order_type} RETRY] Removed {fill_key} from tracking to allow TP/SL retry"
                                    )
                except (ValueError, IndexError) as e:
                    self.log.warning(
                        f"Could not extract fill_key from rejected order ID: {client_order_id}, error: {e}"
                    )
            else:
                self.log.warning(f"Grid order rejected: {client_order_id}, reason: {rejection_reason}")

            # Check if retry handler is initialized
            if self._retry_handler is None or not self._retry_handler.enabled:
                return

            # Check if this order is in retry queue
            if client_order_id not in self._pending_retries:
                return

            intent = self._pending_retries[client_order_id]

            # Don't retry Binance -5022 errors (post-only would trade)
            if "-5022" in rejection_reason:
                self.log.debug(
                    f"Order {client_order_id} rejected with -5022 (post-only would trade), "
                    f"abandoning retry - will recalculate grid on next bar"
                )
                del self._pending_retries[client_order_id]
                if self._retry_handler:
                    self._retry_handler.clear_history(client_order_id)
                return

            # Check if retry is warranted for this rejection type
            if not self._retry_handler.should_retry(rejection_reason):
                self.log.warning(f"Order {client_order_id} rejected for non-retryable reason: {rejection_reason}")
                del self._pending_retries[client_order_id]
                self._retry_handler.clear_history(client_order_id)
                return

            # Check retry limit
            if intent.retry_count >= self._hedge_grid_config.execution.retry_attempts:  # type: ignore[union-attr]
                self.log.warning(f"Order {client_order_id} exhausted {intent.retry_count} retries, abandoning")
                del self._pending_retries[client_order_id]
                self._retry_handler.clear_history(client_order_id)
                return

            # Adjust price for retry
            new_attempt = intent.retry_count + 1
            adjusted_price = self._retry_handler.adjust_price_for_retry(
                original_price=intent.original_price or intent.price or 0.0,
                side=intent.side or Side.LONG,
                attempt=new_attempt,
            )

            # Record this retry attempt
            self._retry_handler.record_attempt(
                client_order_id=client_order_id,
                attempt=new_attempt,
                original_price=intent.original_price or intent.price or 0.0,
                adjusted_price=adjusted_price,
                reason=rejection_reason,
            )

            # Create new intent with adjusted price AND NEW CLIENT_ORDER_ID
            from dataclasses import replace

            # Generate new unique order ID for retry
            base_order_id = client_order_id.split("-retry")[0] if "-retry" in client_order_id else client_order_id
            base_order_id = base_order_id.split("-R")[0] if "-R" in base_order_id else base_order_id

            # Create compact retry ID that stays under 36 char limit
            new_client_order_id = f"{base_order_id}-R{new_attempt}"

            # Validate length to prevent Binance rejection
            if len(new_client_order_id) > 36:
                parts = base_order_id.split("-")
                if len(parts) >= 4:
                    parts[3] = parts[3][:10]
                    base_order_id = "-".join(parts)
                    new_client_order_id = f"{base_order_id}-R{new_attempt}"

            self.log.debug(f"Generated retry order ID: {new_client_order_id} (length: {len(new_client_order_id)})")

            new_intent = replace(
                intent,
                client_order_id=new_client_order_id,
                price=adjusted_price,
                retry_count=new_attempt,
                original_price=intent.original_price or intent.price,
                metadata={**intent.metadata, "retry_attempt": str(new_attempt)},
            )

            # Remove old order ID from pending retries, add new one
            del self._pending_retries[client_order_id]
            self._pending_retries[new_client_order_id] = new_intent

            self.log.info(
                f"Retrying order (attempt {new_attempt}/"
                f"{self._hedge_grid_config.execution.retry_attempts}): "  # type: ignore[union-attr]
                f"old_id={client_order_id}, new_id={new_client_order_id}, "
                f"adjusted price {intent.price} -> {adjusted_price}"
            )

            # Submit retry (with delay if configured)
            if self._hedge_grid_config.execution.retry_delay_ms > 0:  # type: ignore[union-attr]
                delay_ns = self._hedge_grid_config.execution.retry_delay_ms * 1_000_000  # type: ignore[union-attr]
                alert_time_ns = self.clock.timestamp_ns() + delay_ns

                def timer_callback(event) -> None:  # noqa: ARG001
                    """Execute retry attempt for order after delay."""
                    self._execute_add(new_intent)

                self.clock.set_time_alert_ns(
                    name=f"retry_{client_order_id}_{new_attempt}",
                    alert_time_ns=alert_time_ns,
                    callback=timer_callback,
                )
            else:
                # Immediate retry
                self._execute_add(new_intent)

        except Exception as e:
            self.log.error(f"Error in on_order_rejected: {e}")
        finally:
            # Track rejection for circuit breaker monitoring
            self._check_circuit_breaker()

    def on_order_denied(self, event) -> None:
        """Handle order denied event - clean up denied orders from retry tracking."""
        client_order_id = str(event.client_order_id.value)
        reason = str(event.reason) if hasattr(event, "reason") else "Unknown"

        # Enhanced logging for TP/SL denials
        if "-TP-" in client_order_id:
            self.log.debug(f"[TP DENIED] Take-profit order denied: {client_order_id}, reason: {reason}")
        elif "-SL-" in client_order_id:
            self.log.debug(f"[SL DENIED] Stop-loss order denied: {client_order_id}, reason: {reason}")
        else:
            self.log.error(f"Grid order denied: {client_order_id}, reason: {reason}")

        # Remove from pending retries (order ID is invalid, cannot retry)
        if client_order_id in self._pending_retries:
            del self._pending_retries[client_order_id]
            if self._retry_handler is not None:
                self._retry_handler.clear_history(client_order_id)
            self.log.debug(f"Cleaned up denied order {client_order_id} from retry tracking")

        # Track denial for circuit breaker monitoring
        self._check_circuit_breaker()

    def _on_order_expired(self, event: OrderExpired) -> None:
        """Remove expired orders from grid cache to prevent ghost orders."""
        client_order_id = str(event.client_order_id.value)
        if not client_order_id.startswith(self._strategy_name):
            return

        self.log.info(f"Order expired: {event.client_order_id}")

        # Remove grid order from internal cache
        if "-TP-" not in client_order_id and "-SL-" not in client_order_id:
            with self._grid_orders_lock:
                removed = self._grid_orders_cache.pop(client_order_id, None)
                if removed:
                    self.log.info(f"Evicted expired order from grid cache: {client_order_id}")

        # Clean up retry tracking
        if client_order_id in self._pending_retries:
            del self._pending_retries[client_order_id]
            if self._retry_handler is not None:
                self._retry_handler.clear_history(client_order_id)

    def _on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
        """Evict ghost orders when cancel is rejected (order already terminal)."""
        client_order_id = str(event.client_order_id.value)
        if not client_order_id.startswith(self._strategy_name):
            return

        reason = str(event.reason) if hasattr(event, "reason") else "unknown"
        self.log.warning(f"Cancel rejected for {client_order_id}: {reason}")

        # If order is closed/gone in Nautilus, remove from our cache
        order = self.cache.order(event.client_order_id)
        if order is None or order.is_closed:
            if "-TP-" not in client_order_id and "-SL-" not in client_order_id:
                with self._grid_orders_lock:
                    removed = self._grid_orders_cache.pop(client_order_id, None)
                    if removed:
                        self.log.info(f"Evicted ghost order after cancel rejection: {client_order_id}")
