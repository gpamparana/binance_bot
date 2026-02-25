"""Metrics and diagnostics mixin for HedgeGridV1 strategy.

Provides operational metrics aggregation, inventory calculations, PnL tracking,
and periodic diagnostic logging for Prometheus export and monitoring.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _total_fills: int
    _maker_fills: int
    _start_time: int | None
    _last_bar_time: datetime | None
    _last_diagnostic_log: int
    _last_mid: float | None
    _last_funding_rate: float
    _realized_pnl: float
    _instrument: Instrument | None
    _venue: Venue
    _fills_with_exits: set[str]
    _position_retry_counts: dict[str, int]
    instrument_id: InstrumentId
    log: Logger (from Strategy base)
    cache: Cache (from Strategy base)
    clock: Clock (from Strategy base)
    portfolio: Portfolio (from Strategy base)
"""

from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.objects import Price


class MetricsMixin:
    """Mixin providing operational metrics and diagnostic logging."""

    def get_operational_metrics(self) -> dict:
        """Return current operational metrics for monitoring.

        Called periodically by OperationsManager to update Prometheus gauges.
        """
        return {
            "account_balance_usdt": self._get_account_balance(),
            "long_inventory_usdt": self._calculate_inventory("long"),
            "short_inventory_usdt": self._calculate_inventory("short"),
            "net_inventory_usdt": self._calculate_net_inventory(),
            "active_rungs_long": len(self._get_active_rungs("long")),
            "active_rungs_short": len(self._get_active_rungs("short")),
            "open_orders_count": len(self._get_live_grid_orders()),
            "margin_ratio": self._get_margin_ratio(),
            "maker_ratio": self._calculate_maker_ratio(),
            "funding_rate_current": self._get_current_funding_rate(),
            "funding_cost_1h_projected_usdt": self._project_funding_cost_1h(),
            "realized_pnl_usdt": self._get_realized_pnl(),
            "unrealized_pnl_usdt": self._get_unrealized_pnl(),
            "total_pnl_usdt": self._get_total_pnl(),
            "uptime_seconds": self._get_uptime_seconds(),
            "last_bar_timestamp": (self._last_bar_time.timestamp() if self._last_bar_time else 0.0),
        }

    def _calculate_inventory(self, side: str) -> float:
        """Calculate inventory in quote currency for given side."""
        position_id_str = f"{self.instrument_id}-{side.upper()}"
        position_id = PositionId(position_id_str)
        position = self.cache.position(position_id)

        if position and position.quantity > 0:
            return abs(float(position.quantity) * float(position.avg_px_open))
        return 0.0

    def _calculate_net_inventory(self) -> float:
        """Net inventory = long - short."""
        return self._calculate_inventory("long") - self._calculate_inventory("short")

    def _get_active_rungs(self, side: str) -> list:
        """Get list of active grid rungs for given side using typed parsing."""
        from naut_hedgegrid.domain.types import Side

        open_orders = self._get_live_grid_orders()
        target_side = Side.LONG if side.upper() == "LONG" else Side.SHORT
        active_rungs = []

        for order in open_orders:
            try:
                parsed = self._parse_cached_order_id(order.client_order_id)
                if parsed.get("side") == target_side:
                    level = parsed.get("level")
                    if level is not None:
                        active_rungs.append(level)
            except (ValueError, KeyError):
                continue

        return active_rungs

    def _get_account_balance(self) -> float:
        """Get current total account balance in USDT."""
        try:
            account = self.portfolio.account(self._venue)
            if account is None:
                return 0.0
            from nautilus_trader.model.objects import Currency

            base_currency = Currency.from_str("USDT")
            return float(account.balance_total(base_currency))
        except Exception:
            return 0.0

    def _get_margin_ratio(self) -> float:
        """Get current margin ratio from account (margin_used / total_balance)."""
        try:
            account = self.portfolio.account(self._venue)
            if account is None:
                return 0.0

            from nautilus_trader.model.objects import Currency

            base_currency = Currency.from_str("USDT")
            total_balance = float(account.balance_total(base_currency))
            if total_balance <= 0:
                return 0.0

            free_balance = float(account.balance_free(base_currency))
            margin_used = total_balance - free_balance
            return margin_used / total_balance
        except Exception:
            return 0.0

    def _calculate_maker_ratio(self) -> float:
        """Calculate ratio of maker fills vs total fills."""
        if self._total_fills == 0:
            return 1.0
        return self._maker_fills / self._total_fills

    def _get_current_funding_rate(self) -> float:
        """Get current funding rate from mark price stream."""
        return self._last_funding_rate

    def _project_funding_cost_1h(self) -> float:
        """Project funding cost for next 1 hour based on current positions."""
        funding_rate = self._get_current_funding_rate()
        long_inventory = self._calculate_inventory("long")
        short_inventory = self._calculate_inventory("short")

        long_cost = funding_rate * (1 / 8) * long_inventory
        short_cost = -funding_rate * (1 / 8) * short_inventory
        return long_cost + short_cost

    def _get_realized_pnl(self) -> float:
        """Get total realized PnL accumulated from TP/SL fill events."""
        return self._realized_pnl

    def _get_unrealized_pnl(self) -> float:
        """Get total unrealized PnL from open positions."""
        if self._instrument is None or self._last_mid is None:
            return 0.0

        total_unrealized = 0.0
        for side in ["long", "short"]:
            position_id_str = f"{self.instrument_id}-{side.upper()}"
            position_id = PositionId(position_id_str)
            position = self.cache.position(position_id)

            if position and position.quantity > 0:
                current_price = Price(self._last_mid, precision=self._instrument.price_precision)
                unrealized = float(position.unrealized_pnl(current_price))
                total_unrealized += unrealized

        return total_unrealized

    def _get_total_pnl(self) -> float:
        """Total PnL = realized + unrealized."""
        return self._get_realized_pnl() + self._get_unrealized_pnl()

    def _get_uptime_seconds(self) -> float:
        """Get strategy uptime in seconds."""
        if self._start_time is None:
            return 0.0
        return (self.clock.timestamp_ns() - self._start_time) / 1e9

    def _log_diagnostic_status(self) -> None:
        """Log diagnostic information about fills and TP/SL attachments."""
        long_position_id = PositionId(f"{self.instrument_id}-LONG")
        short_position_id = PositionId(f"{self.instrument_id}-SHORT")
        long_pos = self.cache.position(long_position_id)
        short_pos = self.cache.position(short_position_id)

        long_qty = float(long_pos.quantity) if long_pos and long_pos.quantity > 0 else 0.0
        short_qty = float(short_pos.quantity) if short_pos and short_pos.quantity > 0 else 0.0

        tp_orders = 0
        sl_orders = 0
        for order in self.cache.orders_open(venue=self._venue):
            order_id = order.client_order_id.value
            if "-TP-" in order_id:
                tp_orders += 1
            elif "-SL-" in order_id:
                sl_orders += 1

        # Periodic cleanup of stale retry counts
        if len(self._position_retry_counts) > 50:
            self._position_retry_counts.clear()

        self.log.info(
            f"[DIAGNOSTIC] Fills: {self._total_fills} total ({len(self._fills_with_exits)} with TP/SL), "
            f"Positions: LONG={long_qty:.3f} BTC, SHORT={short_qty:.3f} BTC, "
            f"Exit Orders: {tp_orders} TPs, {sl_orders} SLs, "
            f"Grid Orders: {len(self._get_live_grid_orders())} active, "
            f"Last Mid: {self._last_mid:.2f}"
        )
