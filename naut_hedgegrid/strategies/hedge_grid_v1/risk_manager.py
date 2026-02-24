"""Risk management mixin for HedgeGridV1 strategy.

Provides drawdown protection, circuit breaker, position size validation,
emergency position flattening, and critical error handling.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _peak_balance: float
    _initial_balance: float | None
    _drawdown_protection_triggered: bool
    _error_window: deque
    _circuit_breaker_active: bool
    _circuit_breaker_reset_time: float | None
    _circuit_breaker_window_ns: int
    _pause_trading: bool
    _critical_error: bool
    _last_balance_check: float
    _balance_check_interval: int
    _hedge_grid_config: HedgeGridConfig | None
    _grid_orders_cache: dict[str, LiveOrder]
    _grid_orders_lock: threading.Lock
    _venue: Venue
    instrument_id: InstrumentId
    log: Logger (from Strategy base)
    cache: Cache (from Strategy base)
    clock: Clock (from Strategy base)
    portfolio: Portfolio (from Strategy base)
"""

from nautilus_trader.model.identifiers import PositionId

from naut_hedgegrid.domain.types import Side


class RiskManagementMixin:
    """Mixin providing risk management and circuit breaker functionality."""

    def _handle_critical_error(self) -> None:
        """Handle critical errors by entering safe mode."""
        self.log.error("CRITICAL ERROR - Entering safe mode")

        self._critical_error = True
        self._pause_trading = True

        try:
            for order in self.cache.orders_open(instrument_id=self.instrument_id):
                self.cancel_order(order)
                self.log.info(f"Cancelled order {order.client_order_id} due to critical error")
        except Exception as e:
            self.log.error(f"Failed to cancel orders during critical error handling: {e}")

        self.log.error("Critical error handler complete. Trading paused. Manual intervention required.")

    def _validate_order_size(self, order, side: Side) -> bool:
        """Validate cumulative position exposure against account balance.

        Checks that (existing_position + pending_grid_orders + new_order) notional
        does not exceed max_position_pct of total balance for the given side.
        """
        if self._hedge_grid_config and self._hedge_grid_config.risk_management:
            if not self._hedge_grid_config.risk_management.enable_position_validation:
                return True

        try:
            account = self.portfolio.account(self._venue)
            if not account:
                self.log.error("No account found for position validation")
                return False

            from nautilus_trader.model.objects import Currency

            base_currency = Currency.from_str("USDT")
            total_balance = float(account.balance_total(base_currency))
            if total_balance <= 0:
                self.log.error("Total balance is zero or negative, rejecting order")
                return False

            # 1. Existing position exposure
            position_id = PositionId(f"{self.instrument_id}-{side.value}")
            position = self.cache.position(position_id)
            existing_exposure = 0.0
            if position and position.quantity > 0:
                existing_exposure = float(position.quantity) * float(position.avg_px_open)

            # 2. Pending grid orders on this side
            pending_notional = 0.0
            with self._grid_orders_lock:
                for lo in self._grid_orders_cache.values():
                    if lo.side == side:
                        pending_notional += lo.price * lo.qty

            # 3. New order notional
            new_notional = float(order.quantity) * float(order.price) if hasattr(order, "price") else 0.0

            # Check cumulative exposure
            max_position_pct = self._hedge_grid_config.position.max_position_pct if self._hedge_grid_config else 0.95
            max_allowed = total_balance * max_position_pct
            combined = existing_exposure + pending_notional + new_notional

            if combined > max_allowed:
                self.log.warning(
                    f"Position limit breach on {side.value}: "
                    f"existing={existing_exposure:.2f} + pending={pending_notional:.2f} + "
                    f"new={new_notional:.2f} = {combined:.2f} > "
                    f"limit={max_allowed:.2f} ({max_position_pct:.0%} of {total_balance:.2f})"
                )
                return False

            return True

        except Exception as e:
            self.log.error(f"Position validation error (order rejected as fail-safe): {e}")
            return False

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker should activate based on error rate."""
        rm_cfg = self._hedge_grid_config.risk_management if self._hedge_grid_config else None
        if rm_cfg and not rm_cfg.enable_circuit_breaker:
            return

        if self._circuit_breaker_active:
            if self._circuit_breaker_reset_time and self.clock.timestamp_ns() >= self._circuit_breaker_reset_time:
                self._circuit_breaker_active = False
                self._circuit_breaker_reset_time = None
                self.log.info("Circuit breaker reset - resuming normal operation")
            return

        now = self.clock.timestamp_ns()
        self._error_window.append(now)

        window_start = now - self._circuit_breaker_window_ns
        while self._error_window and self._error_window[0] < window_start:
            self._error_window.popleft()

        max_errors = rm_cfg.max_errors_per_minute if rm_cfg else 10

        if len(self._error_window) >= max_errors:
            self.log.error(f"Circuit breaker activated - {len(self._error_window)} errors in last minute")
            self._circuit_breaker_active = True

            for order in self.cache.orders_open(instrument_id=self.instrument_id):
                self.cancel_order(order)

            cooldown_seconds = rm_cfg.circuit_breaker_cooldown_seconds if rm_cfg else 300
            self._circuit_breaker_reset_time = now + (cooldown_seconds * 1_000_000_000)
            self.log.info(f"Circuit breaker will reset in {cooldown_seconds} seconds")

    def _check_drawdown_limit(self) -> None:
        """Check and enforce maximum drawdown limit."""
        rm_cfg = self._hedge_grid_config.risk_management if self._hedge_grid_config else None
        if rm_cfg and not rm_cfg.enable_drawdown_protection:
            return

        try:
            account = self.portfolio.account(self._venue)
            if not account:
                return

            from nautilus_trader.model.objects import Currency

            base_currency = Currency.from_str("USDT")
            current_balance = float(account.balance_total(base_currency))

            if self._initial_balance is None:
                self._initial_balance = current_balance
                self._peak_balance = current_balance
                return

            self._peak_balance = max(current_balance, self._peak_balance)

            if self._peak_balance > 0:
                drawdown_pct = ((self._peak_balance - current_balance) / self._peak_balance) * 100
                max_drawdown_pct = rm_cfg.max_drawdown_pct if rm_cfg else 20.0

                if drawdown_pct > max_drawdown_pct and not self._drawdown_protection_triggered:
                    self.log.error(
                        f"Max drawdown exceeded: {drawdown_pct:.2f}% > {max_drawdown_pct:.2f}% "
                        f"(peak: {self._peak_balance:.2f}, current: {current_balance:.2f})"
                    )
                    self._flatten_all_positions()
                    self._drawdown_protection_triggered = True
                    self._pause_trading = True

        except Exception as e:
            self.log.error(f"Drawdown check FAILED - pausing trading as safety precaution: {e}")
            self._pause_trading = True

    def _flatten_all_positions(self) -> None:
        """Emergency close all positions at market."""
        self.log.warning("EMERGENCY: Flattening all positions")

        for order in self.cache.orders_open(instrument_id=self.instrument_id):
            try:
                self.cancel_order(order)
                self.log.info(f"Cancelled order {order.client_order_id}")
            except Exception as e:
                self.log.error(f"Failed to cancel order {order.client_order_id}: {e}")

        long_position_info = self._close_side_position("long")
        if long_position_info:
            self.log.info(f"Closing LONG position: {long_position_info}")

        short_position_info = self._close_side_position("short")
        if short_position_info:
            self.log.info(f"Closing SHORT position: {short_position_info}")

        self.log.warning("All positions flattened")
