"""HedgeGridV1 trading strategy implementation."""

from datetime import UTC, datetime

from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.events import OrderAccepted, OrderCanceled, OrderFilled
from nautilus_trader.model.identifiers import InstrumentId, PositionId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, StopMarketOrder
from nautilus_trader.trading.strategy import Strategy

from naut_hedgegrid.config.strategy import HedgeGridConfig, HedgeGridConfigLoader
from naut_hedgegrid.domain.types import (
    OrderIntent,
    Side,
    parse_client_order_id,
)
from naut_hedgegrid.exchange.precision import PrecisionGuard
from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
from naut_hedgegrid.strategy.detector import Bar as DetectorBar, RegimeDetector
from naut_hedgegrid.strategy.funding_guard import FundingGuard
from naut_hedgegrid.strategy.grid import GridEngine
from naut_hedgegrid.strategy.order_sync import LiveOrder, OrderDiff
from naut_hedgegrid.strategy.policy import PlacementPolicy


class HedgeGridV1(Strategy):
    """
    HedgeGridV1 futures trading strategy with adaptive grid and regime detection.

    This strategy orchestrates all hedge grid components to implement a dynamic
    grid trading system on futures markets with:

    - **Regime Detection**: Classifies market into UP/DOWN/SIDEWAYS using EMA/ADX/ATR
    - **Adaptive Grid**: Builds price ladders with regime-based directional bias
    - **Placement Policy**: Throttles counter-trend side based on regime
    - **Funding Guard**: Reduces exposure near funding time when costs are high
    - **Precision Guards**: Ensures all orders meet exchange requirements
    - **Order Synchronization**: Minimizes order churn via intelligent diffing

    Grid orders are placed as limit orders in Binance hedge mode (separate LONG/SHORT
    positions). When filled, take-profit and stop-loss orders are attached as
    reduce-only orders.

    Parameters
    ----------
    config : HedgeGridV1Config
        Strategy configuration with instrument, bar type, and config file path

    """

    def __init__(self, config: HedgeGridV1Config) -> None:
        """
        Initialize HedgeGridV1 strategy.

        Args:
            config: Strategy configuration

        """
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.config_path = config.hedge_grid_config_path

        # State tracking
        self._hedge_grid_config: HedgeGridConfig | None = None
        self._instrument: Instrument | None = None
        self._precision_guard: PrecisionGuard | None = None
        self._regime_detector: RegimeDetector | None = None
        self._funding_guard: FundingGuard | None = None
        self._order_diff: OrderDiff | None = None
        self._live_orders: dict[str, LiveOrder] = {}
        self._last_mid: float | None = None
        self._grid_center: float = 0.0

        # Strategy identifier for order IDs
        self._strategy_name = "HG1"

    def on_start(self) -> None:
        """
        Start the strategy.

        Lifecycle:
        1. Load HedgeGridConfig from YAML
        2. Get instrument and create PrecisionGuard
        3. Initialize all strategy components
        4. Subscribe to bar data
        5. Log initialization complete

        """
        self.log.info("Starting HedgeGridV1 strategy")

        # Load hedge grid configuration
        try:
            self._hedge_grid_config = HedgeGridConfigLoader.load(self.config_path)
            self.log.info(f"Loaded config from {self.config_path}")
        except Exception as e:
            self.log.error(f"Failed to load config from {self.config_path}: {e}")
            return

        # Get instrument from cache
        self._instrument = self.cache.instrument(self.instrument_id)
        if self._instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found in cache")
            return

        self.log.info(f"Trading instrument: {self._instrument.id}")

        # Create precision guard
        self._precision_guard = PrecisionGuard(instrument=self._instrument)
        self.log.info(
            f"Precision guard initialized: "
            f"tick={self._precision_guard.precision.price_tick}, "
            f"step={self._precision_guard.precision.qty_step}, "
            f"min_notional={self._precision_guard.precision.min_notional}"
        )

        # Initialize regime detector
        regime_cfg = self._hedge_grid_config.regime
        self._regime_detector = RegimeDetector(
            ema_fast=regime_cfg.ema_fast,
            ema_slow=regime_cfg.ema_slow,
            adx_len=regime_cfg.adx_len,
            atr_len=regime_cfg.atr_len,
            hysteresis_bps=regime_cfg.hysteresis_bps,
        )
        self.log.info(
            f"Regime detector initialized: "
            f"EMA({regime_cfg.ema_fast}/{regime_cfg.ema_slow}), "
            f"ADX({regime_cfg.adx_len}), "
            f"ATR({regime_cfg.atr_len})"
        )

        # Initialize funding guard
        funding_cfg = self._hedge_grid_config.funding
        self._funding_guard = FundingGuard(
            window_minutes=funding_cfg.funding_window_minutes,
            max_cost_bps=funding_cfg.funding_max_cost_bps,
        )
        self.log.info(
            f"Funding guard initialized: "
            f"window={funding_cfg.funding_window_minutes}min, "
            f"max_cost={funding_cfg.funding_max_cost_bps}bps"
        )

        # Initialize order diff engine
        self._order_diff = OrderDiff(
            strategy_name=self._strategy_name,
            precision_guard=self._precision_guard,
        )
        self.log.info("Order diff engine initialized")

        # Subscribe to bars
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Subscribed to bars: {self.bar_type}")

        self.log.info("HedgeGridV1 strategy started successfully")

    def on_stop(self) -> None:
        """
        Stop the strategy.

        Performs clean shutdown:
        1. Cancel all open grid orders
        2. Log final state
        3. Reset internal state

        Note: Positions are NOT automatically closed. Manual intervention
        required if positions should be closed on strategy stop.

        """
        self.log.info("Stopping HedgeGridV1 strategy")

        # Cancel all open orders
        open_order_count = len(self._live_orders)
        if open_order_count > 0:
            self.log.info(f"Canceling {open_order_count} open orders")
            for client_order_id in list(self._live_orders.keys()):
                order = self.cache.order(self.venue_order_id_to_client_order_id(client_order_id))
                if order is not None and order.is_open:
                    self.cancel_order(order)

        self.log.info("HedgeGridV1 strategy stopped")

    def on_bar(self, bar: Bar) -> None:
        """
        Handle new bar data.

        Main strategy logic:
        1. Calculate mid price from bar close
        2. Update regime detector with bar data
        3. Check if grid recentering needed
        4. Build ladders using GridEngine
        5. Apply PlacementPolicy for regime-based throttling
        6. Apply FundingGuard for funding cost management
        7. Generate order diff vs live orders
        8. Execute diff operations (cancels, replaces, adds)

        Args:
            bar: New bar data

        """
        if self._hedge_grid_config is None or self._regime_detector is None:
            self.log.warning("Strategy not fully initialized, skipping bar")
            return

        # Calculate mid price (using close as proxy)
        mid = float(bar.close)
        self._last_mid = mid

        # Convert Nautilus Bar to detector Bar
        detector_bar = DetectorBar(
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )

        # Update regime detector
        self._regime_detector.update_from_bar(detector_bar)
        regime = self._regime_detector.current()

        # Log regime and price
        self.log.info(
            f"Bar: close={mid:.2f}, regime={regime}, warm={self._regime_detector.is_warm}"
        )

        # Check if detector is warm enough for trading
        if not self._regime_detector.is_warm:
            self.log.info("Regime detector not warm yet, skipping trading")
            return

        # Check if grid recentering needed
        recenter_needed = GridEngine.recenter_needed(
            mid=mid,
            last_center=self._grid_center,
            cfg=self._hedge_grid_config,
        )

        if recenter_needed:
            self.log.info(f"Grid recentering triggered at mid={mid:.2f}")
            self._grid_center = mid

        # Build ladders
        ladders = GridEngine.build_ladders(
            mid=mid,
            cfg=self._hedge_grid_config,
            regime=regime,
        )

        self.log.info(
            f"Built {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Apply placement policy
        ladders = PlacementPolicy.shape_ladders(
            ladders=ladders,
            regime=regime,
            cfg=self._hedge_grid_config,
        )

        self.log.info(
            f"After policy: {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Apply funding guard
        now = datetime.now(tz=UTC)
        ladders = self._funding_guard.adjust_ladders(ladders=ladders, now=now)

        self.log.info(
            f"After funding: {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Generate diff
        live_orders_list = list(self._live_orders.values())
        diff_result = self._order_diff.diff(
            desired_ladders=ladders,
            live_orders=live_orders_list,
        )

        self.log.info(
            f"Diff result: {len(diff_result.adds)} adds, "
            f"{len(diff_result.cancels)} cancels, "
            f"{len(diff_result.replaces)} replaces"
        )

        # Execute diff operations
        self._execute_diff(diff_result)

    def on_event(self, event: Event) -> None:
        """
        Handle generic events.

        Routes events to specific handlers based on type.

        Args:
            event: Event to process

        """
        if isinstance(event, OrderFilled):
            self.on_order_filled(event)
        elif isinstance(event, OrderAccepted):
            self.on_order_accepted(event)
        elif isinstance(event, OrderCanceled):
            self.on_order_canceled(event)

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle order filled event.

        When a grid order fills:
        1. Parse client_order_id to get original rung metadata
        2. Retrieve rung TP/SL prices from grid calculation
        3. Create TP limit order (reduce-only) at tp_price
        4. Create SL stop-market order (reduce-only) at sl_price
        5. Submit both with correct position_id suffix

        Args:
            event: Order filled event

        """
        self.log.info(f"Order filled: {event.client_order_id} @ {event.last_px}")

        # Get the filled order
        order = self.cache.order(event.client_order_id)
        if order is None:
            self.log.warning(f"Could not find order {event.client_order_id} in cache")
            return

        # Only handle grid orders (not TP/SL orders)
        if not event.client_order_id.value.startswith(self._strategy_name):
            return

        # Parse client_order_id to get level and side
        try:
            parsed = parse_client_order_id(event.client_order_id.value)
            side = parsed["side"]
            level = parsed["level"]
        except (ValueError, KeyError) as e:
            self.log.warning(f"Could not parse client_order_id {event.client_order_id}: {e}")
            return

        # Calculate TP/SL prices based on grid configuration
        if self._hedge_grid_config is None or self._last_mid is None:
            self.log.warning("Cannot create TP/SL: config or mid price missing")
            return

        fill_price = float(event.last_px)
        fill_qty = float(event.last_qty)

        # Calculate price step
        price_step = self._last_mid * (self._hedge_grid_config.grid.grid_step_bps / 10000)

        # Calculate TP/SL based on side
        if side == Side.LONG:
            # LONG: TP above entry, SL below entry
            tp_price = fill_price + (self._hedge_grid_config.exit.tp_steps * price_step)
            sl_price = fill_price - (self._hedge_grid_config.exit.sl_steps * price_step)
            sl_price = max(sl_price, fill_price * 0.01)  # Ensure positive
        else:
            # SHORT: TP below entry, SL above entry
            tp_price = fill_price - (self._hedge_grid_config.exit.tp_steps * price_step)
            tp_price = max(tp_price, fill_price * 0.01)  # Ensure positive
            sl_price = fill_price + (self._hedge_grid_config.exit.sl_steps * price_step)

        self.log.info(
            f"Creating TP/SL for {side} fill @ {fill_price:.2f}: "
            f"TP={tp_price:.2f}, SL={sl_price:.2f}"
        )

        # Create position_id with side suffix
        position_id = PositionId(f"{self.instrument_id}-{side.value}")

        # Create TP limit order (reduce-only)
        tp_order = self._create_tp_order(
            side=side,
            quantity=fill_qty,
            tp_price=tp_price,
            position_id=position_id,
            level=level,  # type: ignore[arg-type]
        )

        # Create SL stop-market order (reduce-only)
        sl_order = self._create_sl_order(
            side=side,
            quantity=fill_qty,
            sl_price=sl_price,
            position_id=position_id,
            level=level,  # type: ignore[arg-type]
        )

        # Submit orders
        self.submit_order(tp_order, position_id=position_id)
        self.submit_order(sl_order, position_id=position_id)

        self.log.info(f"Submitted TP/SL orders for level {level}")

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """
        Handle order accepted event.

        Adds order to live orders tracking dict.

        Args:
            event: Order accepted event

        """
        # Only track grid orders (not TP/SL)
        if not event.client_order_id.value.startswith(self._strategy_name):
            return

        # Get order from cache
        order = self.cache.order(event.client_order_id)
        if order is None or not isinstance(order, LimitOrder):
            return

        # Parse side from client_order_id
        try:
            parsed = parse_client_order_id(event.client_order_id.value)
            side = parsed["side"]
        except (ValueError, KeyError):
            self.log.warning(f"Could not parse client_order_id {event.client_order_id}")
            return

        # Add to live orders
        live_order = LiveOrder(
            client_order_id=event.client_order_id.value,
            side=side,  # type: ignore[arg-type]
            price=float(order.price),
            qty=float(order.quantity),
            status="OPEN",
        )
        self._live_orders[event.client_order_id.value] = live_order

        self.log.debug(f"Order accepted and tracked: {event.client_order_id}")

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """
        Handle order canceled event.

        Removes order from live orders tracking dict.

        Args:
            event: Order canceled event

        """
        # Remove from live orders if present
        if event.client_order_id.value in self._live_orders:
            del self._live_orders[event.client_order_id.value]
            self.log.debug(f"Order canceled and removed: {event.client_order_id}")

    def _execute_diff(self, diff_result) -> None:
        """
        Execute diff operations to reconcile desired vs live state.

        Executes in order:
        1. Cancels (remove unwanted orders)
        2. Replaces (cancel + recreate modified orders)
        3. Adds (create new orders)

        Args:
            diff_result: DiffResult with operations to execute

        """
        if diff_result.is_empty:
            self.log.debug("No diff operations needed")
            return

        # Execute cancels
        for cancel_intent in diff_result.cancels:
            self._execute_cancel(cancel_intent)

        # Execute replaces (cancel old + create new)
        for replace_intent in diff_result.replaces:
            self._execute_replace(replace_intent)

        # Execute adds
        for add_intent in diff_result.adds:
            self._execute_add(add_intent)

    def _execute_cancel(self, intent: OrderIntent) -> None:
        """
        Execute cancel operation.

        Args:
            intent: Cancel intent with client_order_id

        """
        # Find order in cache
        order = self.cache.order(intent.client_order_id)
        if order is None:
            self.log.warning(f"Cannot cancel: order {intent.client_order_id} not in cache")
            return

        if not order.is_open:
            self.log.debug(f"Order {intent.client_order_id} already closed, skipping cancel")
            return

        self.cancel_order(order)
        self.log.debug(f"Canceled order: {intent.client_order_id}")

    def _execute_replace(self, intent: OrderIntent) -> None:
        """
        Execute replace operation (cancel old + create new).

        Args:
            intent: Replace intent with old and new order params

        """
        # Cancel old order
        self._execute_cancel(OrderIntent.cancel(intent.client_order_id))

        # Create new order with updated params
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
        """
        Execute add operation (create new limit order).

        Args:
            intent: Create intent with order parameters

        """
        if self._instrument is None or intent.side is None:
            self.log.warning("Cannot create order: instrument or side missing")
            return

        # Create limit order
        order = self._create_limit_order(intent, self._instrument)

        # Create position_id with side suffix
        position_id = PositionId(f"{self.instrument_id}-{intent.side.value}")

        # Submit order
        self.submit_order(order, position_id=position_id)
        self.log.debug(f"Created order: {intent.client_order_id} @ {intent.price}")

    def _create_limit_order(self, intent: OrderIntent, instrument: Instrument) -> LimitOrder:
        """
        Create Nautilus LimitOrder from OrderIntent.

        Args:
            intent: Order intent with parameters
            instrument: Trading instrument

        Returns:
            LimitOrder ready to submit

        """
        # Convert side
        order_side = OrderSide.BUY if intent.side == Side.LONG else OrderSide.SELL

        # Create order
        order = self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=order_side,
            quantity=Quantity(intent.qty, precision=instrument.size_precision),
            price=Price(intent.price, precision=instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            post_only=True,  # Maker-only orders
            client_order_id=self.clock.generate_client_order_id(intent.client_order_id),
        )

        return order

    def _create_tp_order(
        self,
        side: Side,
        quantity: float,
        tp_price: float,
        position_id: PositionId,
        level: int,
    ) -> LimitOrder:
        """
        Create take-profit limit order (reduce-only).

        Args:
            side: Original fill side (LONG or SHORT)
            quantity: Position quantity to close
            tp_price: Take profit price
            position_id: Position ID with side suffix
            level: Grid level for order ID

        Returns:
            Reduce-only limit order at TP price

        """
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        # TP order is opposite side (close position)
        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        # Generate client_order_id for TP
        client_order_id_str = (
            f"{self._strategy_name}-TP-{side.value}-{level:02d}-{self.clock.timestamp_ns()}"
        )

        order = self.order_factory.limit(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            price=Price(tp_price, precision=self._instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=self.clock.generate_client_order_id(client_order_id_str),
        )

        return order

    def _create_sl_order(
        self,
        side: Side,
        quantity: float,
        sl_price: float,
        position_id: PositionId,
        level: int,
    ) -> StopMarketOrder:
        """
        Create stop-loss stop-market order (reduce-only).

        Args:
            side: Original fill side (LONG or SHORT)
            quantity: Position quantity to close
            sl_price: Stop loss trigger price
            position_id: Position ID with side suffix
            level: Grid level for order ID

        Returns:
            Reduce-only stop-market order at SL price

        """
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        # SL order is opposite side (close position)
        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        # Generate client_order_id for SL
        client_order_id_str = (
            f"{self._strategy_name}-SL-{side.value}-{level:02d}-{self.clock.timestamp_ns()}"
        )

        order = self.order_factory.stop_market(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            trigger_price=Price(sl_price, precision=self._instrument.price_precision),
            trigger_type=TriggerType.LAST_TRADE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=self.clock.generate_client_order_id(client_order_id_str),
        )

        return order

    def venue_order_id_to_client_order_id(self, client_order_id_str: str):
        """
        Helper to convert string client_order_id to ClientOrderId.

        This is a placeholder - actual implementation depends on Nautilus version.

        Args:
            client_order_id_str: Client order ID as string

        Returns:
            ClientOrderId object or string (depending on Nautilus API)

        """
        # In newer Nautilus versions, may need to use ClientOrderId class
        # For now, returning string for compatibility
        return client_order_id_str
