"""Order execution mixin for HedgeGridV1 strategy.

Provides order creation and diff execution pipeline: creating limit orders,
TP/SL orders, executing cancels/replaces/adds, and order ID management.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _instrument: Instrument | None
    _strategy_name: str
    _pause_trading: bool
    _circuit_breaker_active: bool
    _order_id_counter: int
    _order_id_lock: threading.Lock
    _grid_orders_lock: threading.Lock
    _grid_orders_cache: dict[str, LiveOrder]
    _pending_retries: dict[str, OrderIntent]
    _retry_handler: PostOnlyRetryHandler | None
    _parsed_order_id_cache: dict[str, dict]
    _precision_guard: PrecisionGuard | None
    _last_mid: float | None
    _tp_sl_buffer_mult: float
    _hedge_grid_config: HedgeGridConfig | None
    _venue: Venue
    instrument_id: InstrumentId
    log: Logger (from Strategy base)
    cache: Cache (from Strategy base)
    clock: Clock (from Strategy base)
"""

from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import ClientOrderId, PositionId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, StopMarketOrder

from naut_hedgegrid.domain.types import OrderIntent, Side, parse_client_order_id
from naut_hedgegrid.strategy.order_sync import LiveOrder


class OrderExecutionMixin:
    """Mixin providing order creation and diff execution pipeline."""

    def _execute_diff(self, diff_result) -> None:
        """Execute diff operations to reconcile desired vs live state.

        Executes in order: cancels, replaces, adds.
        """
        if diff_result.is_empty:
            self.log.debug("No diff operations needed")
            return

        for cancel_intent in diff_result.cancels:
            self._execute_cancel(cancel_intent)

        for replace_intent in diff_result.replaces:
            self._execute_replace(replace_intent)

        for add_intent in diff_result.adds:
            self._execute_add(add_intent)

    def _execute_cancel(self, intent: OrderIntent) -> None:
        """Execute cancel operation."""
        order = self.cache.order(ClientOrderId(intent.client_order_id))
        if order is None:
            self.log.warning(f"Cannot cancel: order {intent.client_order_id} not in cache")
            with self._grid_orders_lock:
                self._grid_orders_cache.pop(intent.client_order_id, None)
            return

        if not order.is_open:
            self.log.debug(f"Order {intent.client_order_id} already closed, skipping cancel")
            with self._grid_orders_lock:
                self._grid_orders_cache.pop(intent.client_order_id, None)
            return

        self.cancel_order(order)
        self.log.debug(f"Canceled order: {intent.client_order_id}")

    def _execute_replace(self, intent: OrderIntent) -> None:
        """Execute replace operation (cancel old + create new)."""
        self._execute_cancel(OrderIntent.cancel(intent.client_order_id))

        if intent.replace_with is None or intent.side is None:
            self.log.warning(f"Invalid replace intent: {intent}")
            return

        new_intent = OrderIntent.create(
            client_order_id=intent.replace_with,
            side=intent.side,
            price=intent.price or 0.0,
            qty=intent.qty or 0.0,
            metadata=intent.metadata,
        )
        self._execute_add(new_intent)

    def _execute_add(self, intent: OrderIntent) -> None:
        """Execute add operation (create new limit order) with post-only retry tracking."""
        # Risk gate: block order submission if trading is paused or circuit breaker active
        if self._pause_trading or self._circuit_breaker_active:
            self.log.warning(
                f"Order {intent.client_order_id} blocked by risk gate "
                f"(paused={self._pause_trading}, cb={self._circuit_breaker_active})"
            )
            return

        if self._instrument is None or intent.side is None:
            self.log.warning("Cannot create order: instrument or side missing")
            return

        order = self._create_limit_order(intent, self._instrument)

        # Validate cumulative position exposure before submission
        if not self._validate_order_size(order, intent.side):
            self.log.warning(f"Order {intent.client_order_id} rejected by position size validation")
            return

        position_id = PositionId(f"{self.instrument_id}-{intent.side.value}")
        self.submit_order(order, position_id=position_id)

        actual_order_id = str(order.client_order_id.value)
        self.log.debug(f"Created order: {actual_order_id} @ {intent.price}")

        # Track order for potential retry using the ACTUAL order ID
        if self._retry_handler is not None and self._retry_handler.enabled:
            self._pending_retries[actual_order_id] = intent

    def _create_limit_order(self, intent: OrderIntent, instrument: Instrument) -> LimitOrder:
        """Create Nautilus LimitOrder from OrderIntent."""
        order_side = OrderSide.BUY if intent.side == Side.LONG else OrderSide.SELL

        with self._order_id_lock:
            self._order_id_counter += 1
            unique_client_order_id = f"{intent.client_order_id}-{self._order_id_counter}"

        order = self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=order_side,
            quantity=Quantity(intent.qty, precision=instrument.size_precision),
            price=Price(intent.price, precision=instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            post_only=True,
            client_order_id=ClientOrderId(unique_client_order_id),
        )

        return order

    def _create_tp_order(
        self,
        side: Side,
        quantity: float,
        tp_price: float,
        level: int,
        fill_event_ts: int,
    ) -> LimitOrder:
        """Create take-profit limit order (reduce-only)."""
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        with self._order_id_lock:
            self._order_id_counter += 1
            counter = self._order_id_counter

        timestamp_ms = fill_event_ts // 1_000_000
        side_abbr = "L" if side == Side.LONG else "S"
        client_order_id_str = f"{self._strategy_name}-TP-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"

        if len(client_order_id_str) > 36:
            self.log.error(f"TP order ID too long ({len(client_order_id_str)} chars): {client_order_id_str}")
            client_order_id_str = f"TP-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"

        # Round TP price to instrument tick size to prevent Binance -4014 error
        if self._precision_guard:
            tp_price = self._precision_guard.clamp_price(tp_price)

        # NOTE: reduce_only=False because Nautilus's internal RiskEngine
        # incorrectly evaluates reduce-only against the net position in hedge mode
        # (OMS_HEDGING), denying orders that would reduce one side when the other
        # side also exists. The position_id suffix ensures correct side targeting,
        # and Binance's own hedge mode enforces position-side scoping.
        order = self.order_factory.limit(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            price=Price(tp_price, precision=self._instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            client_order_id=ClientOrderId(client_order_id_str),
        )

        return order

    def _create_sl_order(
        self,
        side: Side,
        quantity: float,
        sl_price: float,
        level: int,
        fill_event_ts: int,
    ) -> StopMarketOrder:
        """Create stop-loss stop-market order (reduce-only)."""
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        with self._order_id_lock:
            self._order_id_counter += 1
            counter = self._order_id_counter

        timestamp_ms = fill_event_ts // 1_000_000
        side_abbr = "L" if side == Side.LONG else "S"
        client_order_id_str = f"{self._strategy_name}-SL-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"

        if len(client_order_id_str) > 36:
            self.log.error(f"SL order ID too long ({len(client_order_id_str)} chars): {client_order_id_str}")
            client_order_id_str = f"SL-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"

        # Round SL price to instrument tick size for precision
        if self._precision_guard:
            sl_price = self._precision_guard.clamp_price(sl_price)

        # Validate SL price against current market to prevent immediate trigger
        if self._last_mid is not None:
            current_mid = self._last_mid
            buffer = self._tp_sl_buffer_mult

            if order_side == OrderSide.BUY:  # Closing SHORT position
                if sl_price <= current_mid:
                    adjusted_price = current_mid * (1 + buffer)
                    self.log.warning(
                        f"[SL ADJUST] Stop-loss {sl_price:.2f} at/below market "
                        f"{current_mid:.2f}, adjusting to {adjusted_price:.2f} (+{buffer:.4%})"
                    )
                    sl_price = adjusted_price
                    if self._precision_guard:
                        sl_price = self._precision_guard.clamp_price(sl_price)

            elif order_side == OrderSide.SELL:  # Closing LONG position
                if sl_price >= current_mid:
                    adjusted_price = current_mid * (1 - buffer)
                    self.log.warning(
                        f"[SL ADJUST] Stop-loss {sl_price:.2f} at/above market "
                        f"{current_mid:.2f}, adjusting to {adjusted_price:.2f} (-{buffer:.4%})"
                    )
                    sl_price = adjusted_price
                    if self._precision_guard:
                        sl_price = self._precision_guard.clamp_price(sl_price)

        # NOTE: reduce_only=False for same reason as TP orders - see _create_tp_order
        order = self.order_factory.stop_market(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            trigger_price=Price(sl_price, precision=self._instrument.price_precision),
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            client_order_id=ClientOrderId(client_order_id_str),
        )

        return order

    def venue_order_id_to_client_order_id(self, client_order_id_str: str) -> ClientOrderId:
        """Helper to convert string client_order_id to ClientOrderId."""
        return ClientOrderId(client_order_id_str)

    def _parse_cached_order_id(self, client_order_id: str) -> dict:
        """Parse client order ID with instance-level caching to avoid repeated parsing."""
        if client_order_id in self._parsed_order_id_cache:
            return self._parsed_order_id_cache[client_order_id]

        result = parse_client_order_id(client_order_id)
        self._parsed_order_id_cache[client_order_id] = result

        # Prevent memory leak: limit cache size to 1000 entries
        if len(self._parsed_order_id_cache) > 1000:
            keys_to_remove = list(self._parsed_order_id_cache.keys())[:200]
            for key in keys_to_remove:
                del self._parsed_order_id_cache[key]

        return result

    def _get_live_grid_orders(self) -> list[LiveOrder]:
        """Get live grid orders with state verification against Nautilus cache.

        Cross-checks each cached order against Nautilus's authoritative order
        state. Evicts any orders that are no longer open.
        """
        stale_ids: list[str] = []
        with self._grid_orders_lock:
            for coid in self._grid_orders_cache:
                order = self.cache.order(ClientOrderId(coid))
                if order is None or order.is_closed:
                    stale_ids.append(coid)

            for coid in stale_ids:
                self._grid_orders_cache.pop(coid, None)

            if stale_ids:
                self.log.info(
                    f"Evicted {len(stale_ids)} stale orders from grid cache: "
                    f"{stale_ids[:3]}{'...' if len(stale_ids) > 3 else ''}"
                )

            return list(self._grid_orders_cache.values())
