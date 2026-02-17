"""Kill switch and circuit breaker system for emergency risk management.

This module provides automated emergency controls that monitor risk metrics and
automatically flatten positions when safety thresholds are breached. Designed for
real-time protection of capital with thread-safe operation.
"""

import logging
import threading
from datetime import UTC, datetime
from typing import Any, Literal

from naut_hedgegrid.config.operations import KillSwitchConfig
from naut_hedgegrid.ops.alerts import AlertManager, AlertSeverity


class CircuitBreakerTriggered(Exception):
    """Exception raised when a circuit breaker triggers."""


class KillSwitch:
    """
    Emergency position flattening and safety circuit breaker system.

    Monitors critical risk metrics in background thread and automatically flattens
    positions when safety thresholds are breached. Thread-safe operation allows
    integration with live trading strategies.

    Circuit Breakers:
        1. **Drawdown Circuit**: Triggers when total PnL drawdown exceeds threshold
        2. **Funding Cost Circuit**: Triggers when projected funding cost is excessive
        3. **Margin Ratio Circuit**: Triggers when margin utilization approaches liquidation
        4. **Loss Limit Circuit**: Triggers when absolute loss exceeds threshold
        5. **Daily Loss Circuit**: Optional daily loss limit with UTC midnight reset

    Parameters
    ----------
    strategy : Any
        Strategy instance (must have get_operational_metrics and flatten_side methods)
    config : KillSwitchConfig
        Kill switch configuration with thresholds
    alert_manager : Optional[AlertManager]
        Alert manager for notifications (default: None)

    Attributes
    ----------
    logger : logging.Logger
        Logger for kill switch events

    Examples
    --------
    >>> from naut_hedgegrid.ops.kill_switch import KillSwitch
    >>> from naut_hedgegrid.config.operations import KillSwitchConfig
    >>> config = KillSwitchConfig(
    ...     enabled=True,
    ...     max_drawdown_pct=5.0,
    ...     max_funding_cost_bps=20.0,
    ...     check_interval_seconds=5,
    ... )
    >>> kill_switch = KillSwitch(strategy, config)
    >>> kill_switch.start_monitoring()  # Start background monitoring

    """

    def __init__(
        self,
        strategy: Any,
        config: KillSwitchConfig,
        alert_manager: AlertManager | None = None,
    ) -> None:
        """
        Initialize kill switch with strategy and configuration.

        Args:
            strategy: Strategy instance to monitor and control
            config: Kill switch configuration
            alert_manager: Optional alert manager for notifications

        """
        self.strategy = strategy
        self.config = config
        self.alert_manager = alert_manager
        self.logger = logging.getLogger(__name__)

        # Monitoring state
        self._monitoring = False
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Session tracking
        self._session_start_time = datetime.now(tz=UTC)
        self._session_start_pnl = 0.0
        self._session_peak_pnl = 0.0

        # Daily tracking
        self._daily_reset_time = self._get_next_daily_reset()
        self._daily_start_pnl = 0.0
        self._daily_peak_pnl = 0.0

        # Circuit breaker state
        self._circuit_breakers_triggered: set[str] = set()
        self._flatten_in_progress = False
        self._lock = threading.Lock()

        self.logger.info(
            f"Kill switch initialized with thresholds: "
            f"drawdown={config.max_drawdown_pct}%, "
            f"funding={config.max_funding_cost_bps}bps, "
            f"margin={config.max_margin_ratio:.1%}, "
            f"loss=${config.max_loss_amount_usdt:.2f}"
        )

    def start_monitoring(self) -> None:
        """
        Start background monitoring thread for circuit breakers.

        Thread checks circuit breakers at configured interval and triggers
        position flattening if thresholds are breached.

        """
        if not self.config.enabled:
            self.logger.info("Kill switch monitoring disabled by configuration")
            return

        if self._monitoring:
            self.logger.warning("Kill switch monitoring already running")
            return

        self._monitoring = True
        self._stop_event.clear()

        # Initialize session tracking
        try:
            metrics = self.strategy.get_operational_metrics()
            self._session_start_pnl = metrics.get("total_pnl_usdt", 0.0)
            self._session_peak_pnl = self._session_start_pnl
            self._daily_start_pnl = self._session_start_pnl
            self._daily_peak_pnl = self._session_start_pnl
        except Exception as e:
            self.logger.warning(f"Failed to initialize PnL tracking: {e}")

        # Start monitoring thread
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="KillSwitchMonitor",
            daemon=True,
        )
        self._monitor_thread.start()

        self.logger.info(
            f"Kill switch monitoring started (check interval: {self.config.check_interval_seconds}s)"
        )

    def stop_monitoring(self) -> None:
        """
        Stop background monitoring thread.

        Gracefully shuts down monitoring thread and waits for completion.

        """
        if not self._monitoring:
            return

        self.logger.info("Stopping kill switch monitoring...")
        self._monitoring = False
        self._stop_event.set()

        # Wait for thread to finish (with timeout)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)

        self.logger.info("Kill switch monitoring stopped")

    def flatten_now(
        self,
        side: Literal["long", "short", "both"] = "both",
        reason: str = "manual",
    ) -> dict:
        """
        Immediately flatten positions for specified side(s).

        This is the core emergency action that:
        1. Cancels all pending orders for the side(s)
        2. Submits market orders to close positions
        3. Logs the action with detailed reason
        4. Sends alerts via configured channels

        Thread-safe and idempotent - safe to call multiple times.

        Args:
            side: Which side to flatten ("long", "short", or "both")
            reason: Reason for flattening (for logging and alerts)

        Returns:
            dict: Flattening result with cancelled orders and closing positions

        Examples:
            >>> result = kill_switch.flatten_now("both", "max drawdown exceeded")
            >>> print(f"Cancelled {result['cancelled_orders']} orders")
            >>> print(f"Closing {len(result['closing_positions'])} positions")

        """
        with self._lock:
            if self._flatten_in_progress:
                self.logger.warning("Flatten already in progress, skipping duplicate request")
                return {
                    "cancelled_orders": 0,
                    "closing_positions": [],
                    "timestamp": datetime.now(tz=UTC),
                    "reason": reason,
                    "status": "already_in_progress",
                }

            self._flatten_in_progress = True

        try:
            self.logger.warning(f"FLATTEN NOW triggered: {reason} (side={side})")

            timestamp = datetime.now(tz=UTC)

            # Call strategy's flatten_side method
            result = self.strategy.flatten_side(side)

            # Add metadata
            result["timestamp"] = timestamp
            result["reason"] = reason
            result["status"] = "completed"

            # Determine sides flattened
            sides_flattened = ["long", "short"] if side == "both" else [side]

            self.logger.warning(
                f"Flatten completed: cancelled {result['cancelled_orders']} orders, "
                f"closing {len(result['closing_positions'])} positions"
            )

            # Send alert
            if self.alert_manager:
                self.alert_manager.send_flatten_alert(
                    reason=reason,
                    sides_flattened=sides_flattened,
                    cancelled_orders=result["cancelled_orders"],
                    positions_closed=result["closing_positions"],
                )

            return result

        except Exception as e:
            self.logger.error(f"Error during flatten: {e}", exc_info=True)
            return {
                "cancelled_orders": 0,
                "closing_positions": [],
                "timestamp": datetime.now(tz=UTC),
                "reason": reason,
                "status": "error",
                "error": str(e),
            }

        finally:
            with self._lock:
                self._flatten_in_progress = False

    def _monitoring_loop(self) -> None:
        """
        Main monitoring loop that runs in background thread.

        Periodically checks all circuit breakers and triggers flattening if needed.

        """
        while self._monitoring and not self._stop_event.is_set():
            try:
                # Check if daily reset is needed
                self._check_daily_reset()

                # Check all circuit breakers
                self._check_safety_circuits()

            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}", exc_info=True)

            # Sleep until next check (or stop event)
            self._stop_event.wait(timeout=self.config.check_interval_seconds)

    def _check_safety_circuits(self) -> None:
        """
        Check all circuit breakers and trigger flatten if any breach.

        Circuit breakers checked:
        1. Drawdown circuit (session and daily)
        2. Funding cost circuit
        3. Margin ratio circuit
        4. Absolute loss limit

        """
        try:
            # Get current metrics from strategy
            metrics = self.strategy.get_operational_metrics()

            # Check each circuit breaker
            self._check_drawdown_circuit(metrics)
            self._check_funding_cost_circuit(metrics)
            self._check_margin_ratio_circuit(metrics)
            self._check_loss_limit_circuit(metrics)

        except Exception as e:
            self.logger.error(f"Error checking safety circuits: {e}", exc_info=True)

    def _check_drawdown_circuit(self, metrics: dict) -> None:
        """
        Check if drawdown exceeds threshold.

        Monitors both session drawdown and optional daily drawdown.

        Args:
            metrics: Current operational metrics from strategy

        """
        current_pnl = metrics.get("total_pnl_usdt", 0.0)

        # Update peak tracking
        self._session_peak_pnl = max(current_pnl, self._session_peak_pnl)

        self._daily_peak_pnl = max(current_pnl, self._daily_peak_pnl)

        # Calculate session drawdown
        session_drawdown_amount = self._session_peak_pnl - current_pnl
        session_drawdown_pct = (
            (session_drawdown_amount / abs(self._session_peak_pnl)) * 100
            if abs(self._session_peak_pnl) > 0
            else 0.0
        )

        # Check session drawdown threshold
        if session_drawdown_pct > self.config.max_drawdown_pct:
            self._trigger_circuit_breaker(
                breaker_type="Drawdown (Session)",
                current_value=session_drawdown_pct,
                threshold=self.config.max_drawdown_pct,
                unit="%",
            )
            return

        # Calculate daily drawdown if enabled
        if self.config.daily_loss_limit_usdt is not None:
            daily_drawdown_amount = self._daily_peak_pnl - current_pnl
            daily_drawdown_pct = (
                (daily_drawdown_amount / abs(self._daily_peak_pnl)) * 100
                if abs(self._daily_peak_pnl) > 0
                else 0.0
            )

            if daily_drawdown_pct > self.config.max_drawdown_pct:
                self._trigger_circuit_breaker(
                    breaker_type="Drawdown (Daily)",
                    current_value=daily_drawdown_pct,
                    threshold=self.config.max_drawdown_pct,
                    unit="%",
                )

    def _check_funding_cost_circuit(self, metrics: dict) -> None:
        """
        Check if projected funding cost exceeds threshold.

        Args:
            metrics: Current operational metrics from strategy

        """
        # Get projected 1h funding cost and scale to 8h
        funding_cost_1h = metrics.get("funding_cost_1h_projected_usdt", 0.0)
        funding_cost_8h = funding_cost_1h * 8

        # Get total inventory for basis point calculation
        long_inventory = metrics.get("long_inventory_usdt", 0.0)
        short_inventory = metrics.get("short_inventory_usdt", 0.0)
        total_inventory = long_inventory + short_inventory

        if total_inventory == 0:
            return  # No positions, no funding cost

        # Calculate funding cost in basis points
        funding_cost_bps = abs(funding_cost_8h / total_inventory) * 10000

        # Check threshold
        if funding_cost_bps > self.config.max_funding_cost_bps:
            self._trigger_circuit_breaker(
                breaker_type="Funding Cost",
                current_value=funding_cost_bps,
                threshold=self.config.max_funding_cost_bps,
                unit="bps",
            )

    def _check_margin_ratio_circuit(self, metrics: dict) -> None:
        """
        Check if margin ratio exceeds threshold.

        Args:
            metrics: Current operational metrics from strategy

        """
        margin_ratio = metrics.get("margin_ratio", 0.0)

        if margin_ratio > self.config.max_margin_ratio:
            self._trigger_circuit_breaker(
                breaker_type="Margin Ratio",
                current_value=margin_ratio * 100,
                threshold=self.config.max_margin_ratio * 100,
                unit="%",
            )

    def _check_loss_limit_circuit(self, metrics: dict) -> None:
        """
        Check if absolute loss exceeds threshold.

        Args:
            metrics: Current operational metrics from strategy

        """
        current_pnl = metrics.get("total_pnl_usdt", 0.0)

        # Check session loss limit
        session_loss = current_pnl - self._session_start_pnl
        if session_loss < -self.config.max_loss_amount_usdt:
            self._trigger_circuit_breaker(
                breaker_type="Session Loss Limit",
                current_value=abs(session_loss),
                threshold=self.config.max_loss_amount_usdt,
                unit="USDT",
            )
            return

        # Check daily loss limit if enabled
        if self.config.daily_loss_limit_usdt is not None:
            daily_loss = current_pnl - self._daily_start_pnl
            if daily_loss < -self.config.daily_loss_limit_usdt:
                self._trigger_circuit_breaker(
                    breaker_type="Daily Loss Limit",
                    current_value=abs(daily_loss),
                    threshold=self.config.daily_loss_limit_usdt,
                    unit="USDT",
                )

    def _trigger_circuit_breaker(
        self,
        breaker_type: str,
        current_value: float,
        threshold: float,
        unit: str = "",
    ) -> None:
        """
        Trigger circuit breaker and flatten positions.

        Args:
            breaker_type: Type of circuit breaker triggered
            current_value: Current metric value
            threshold: Configured threshold value
            unit: Unit of measurement (%, bps, USDT, etc.)

        """
        # Check if already triggered (prevent duplicate triggers)
        breaker_key = f"{breaker_type}_{datetime.now(tz=UTC).date()}"
        with self._lock:
            if breaker_key in self._circuit_breakers_triggered:
                self.logger.debug(f"Circuit breaker {breaker_type} already triggered today")
                return

        # Log circuit breaker trigger
        self.logger.critical(
            f"CIRCUIT BREAKER TRIGGERED: {breaker_type} - "
            f"current={current_value:.2f}{unit}, threshold={threshold:.2f}{unit}"
        )

        # Send alert
        if self.alert_manager:
            self.alert_manager.send_circuit_breaker_alert(
                breaker_type=breaker_type,
                current_value=current_value,
                threshold=threshold,
                action="flatten positions",
            )

        # Flatten positions
        reason = f"{breaker_type} circuit breaker triggered (current={current_value:.2f}{unit}, threshold={threshold:.2f}{unit})"
        result = self.flatten_now("both", reason)

        # Only mark as triggered after successful flatten (allows retry on failure)
        if result.get("status") == "completed":
            with self._lock:
                self._circuit_breakers_triggered.add(breaker_key)
        else:
            self.logger.warning(
                f"Flatten failed for {breaker_type} circuit breaker "
                f"(status={result.get('status')}), will retry on next check"
            )

    def _check_daily_reset(self) -> None:
        """Check if daily reset is needed and reset daily tracking.

        Thread-safe: acquires lock before checking and modifying state.
        """
        with self._lock:
            now = datetime.now(tz=UTC)

            if now >= self._daily_reset_time:
                self.logger.info("Daily reset triggered at UTC midnight")

                # Reset daily tracking
                try:
                    metrics = self.strategy.get_operational_metrics()
                    self._daily_start_pnl = metrics.get("total_pnl_usdt", 0.0)
                    self._daily_peak_pnl = self._daily_start_pnl
                except Exception as e:
                    self.logger.warning(f"Failed to reset daily PnL tracking: {e}")

                # Reset circuit breaker triggers for new day
                self._circuit_breakers_triggered.clear()

                # Calculate next reset time
                self._daily_reset_time = self._get_next_daily_reset()

    @staticmethod
    def _get_next_daily_reset() -> datetime:
        """
        Calculate next UTC midnight reset time.

        Returns:
            datetime: Next UTC midnight

        """
        now = datetime.now(tz=UTC)
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # If it's already past midnight today, use tomorrow
        if next_midnight <= now:
            from datetime import timedelta

            next_midnight += timedelta(days=1)

        return next_midnight

    def get_status(self) -> dict:
        """
        Get current kill switch status and metrics.

        Returns:
            dict: Status information including monitoring state and circuit breaker history

        """
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "monitoring": self._monitoring,
                "flatten_in_progress": self._flatten_in_progress,
                "session_start_time": self._session_start_time.isoformat(),
                "session_start_pnl": self._session_start_pnl,
                "session_peak_pnl": self._session_peak_pnl,
                "daily_reset_time": self._daily_reset_time.isoformat(),
                "daily_start_pnl": self._daily_start_pnl,
                "daily_peak_pnl": self._daily_peak_pnl,
                "circuit_breakers_triggered": list(self._circuit_breakers_triggered),
                "config": {
                    "max_drawdown_pct": self.config.max_drawdown_pct,
                    "max_funding_cost_bps": self.config.max_funding_cost_bps,
                    "max_margin_ratio": self.config.max_margin_ratio,
                    "max_loss_amount_usdt": self.config.max_loss_amount_usdt,
                    "daily_loss_limit_usdt": self.config.daily_loss_limit_usdt,
                    "check_interval_seconds": self.config.check_interval_seconds,
                },
            }

    def reset_circuit_breakers(self) -> None:
        """
        Manually reset circuit breaker triggers.

        Use with caution - only reset after addressing underlying issues.

        """
        with self._lock:
            count = len(self._circuit_breakers_triggered)
            self._circuit_breakers_triggered.clear()

        self.logger.warning(f"Manually reset {count} circuit breaker triggers")

        if self.alert_manager:
            self.alert_manager.send_alert(
                message="Circuit Breakers Manually Reset",
                severity=AlertSeverity.WARNING,
                extra_data={"reset_count": count},
            )
