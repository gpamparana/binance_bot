"""OperationsManager: Glue layer that wires Prometheus, FastAPI, and KillSwitch to the strategy.

Starts background services for monitoring and control when --enable-ops is passed.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from naut_hedgegrid.ops.alerts import AlertManager
from naut_hedgegrid.ops.prometheus import PrometheusExporter

if TYPE_CHECKING:
    from naut_hedgegrid.ops.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class OperationsManager:
    """Orchestrates operational infrastructure for a running strategy.

    Manages the lifecycle of:
    - PrometheusExporter: Exposes metrics at /metrics
    - StrategyAPI (FastAPI): REST control endpoints
    - KillSwitch: Automated circuit breakers (optional)

    Parameters
    ----------
    strategy : Any
        Strategy instance with get_operational_metrics(), flatten_side(), attach_kill_switch()
    instrument_id : str
        Instrument identifier (e.g., "BTCUSDT-PERP.BINANCE")
    prometheus_port : int
        Port for Prometheus metrics (default 9090)
    api_port : int
        Port for FastAPI endpoints (default 8080)
    api_key : str | None
        Optional API key for FastAPI authentication
    """

    def __init__(
        self,
        strategy: Any,
        instrument_id: str,
        prometheus_port: int = 9090,
        api_port: int = 8080,
        api_key: str | None = None,
        ops_config: Any | None = None,
        require_live_safety: bool = False,
    ) -> None:
        self.strategy = strategy
        self.instrument_id = instrument_id
        self.prometheus_port = prometheus_port
        self.api_port = api_port
        self.api_key = api_key
        self._ops_config = ops_config  # OperationsConfig or None
        self._require_live_safety = require_live_safety
        self.is_running = False

        self._prometheus: PrometheusExporter | None = None
        self._api: Any | None = None  # StrategyAPI
        self._kill_switch: KillSwitch | None = None
        self._metrics_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

    def start(self) -> None:
        """Start all operational services."""
        if self.is_running:
            logger.warning("OperationsManager already running")
            return

        # 1. Start Prometheus exporter
        self._prometheus = PrometheusExporter(instrument_id=self.instrument_id)
        self._prometheus.start_server(port=self.prometheus_port)
        logger.info(f"Prometheus metrics started on port {self.prometheus_port}")

        # 2. Start FastAPI control endpoints
        try:
            from naut_hedgegrid.ui.api import StrategyAPI

            self._api = StrategyAPI(
                strategy_callback=self._strategy_callback,
                api_key=self.api_key,
            )
            self._api.start_server(host="127.0.0.1", port=self.api_port)
            logger.info(f"FastAPI control endpoints started on port {self.api_port}")
        except Exception as e:
            logger.warning(f"FastAPI failed to start: {e}. Continuing without API.")
            self._api = None

        # 3. Start metrics polling thread
        self._shutdown_event.clear()
        self._metrics_thread = threading.Thread(
            target=self._metrics_poll_loop,
            name="ops-metrics-poller",
            daemon=True,
        )
        self._metrics_thread.start()
        logger.info("Metrics polling thread started (5s interval)")

        # 4. Start KillSwitch monitoring
        try:
            from naut_hedgegrid.config.operations import KillSwitchConfig, OperationsConfig
            from naut_hedgegrid.ops.kill_switch import KillSwitch

            # Use ops config if provided, otherwise fall back to defaults
            if self._ops_config is not None and isinstance(self._ops_config, OperationsConfig):
                kill_switch_config = self._ops_config.kill_switch
            else:
                kill_switch_config = KillSwitchConfig()
                logger.warning("No ops config provided — using default KillSwitch thresholds")

            logger.info(
                f"KillSwitch thresholds: max_drawdown={kill_switch_config.max_drawdown_pct}%, "
                f"max_loss={kill_switch_config.max_loss_amount_usdt} USDT, "
                f"max_margin={kill_switch_config.max_margin_ratio:.0%}"
            )

            # Build AlertManager from ops config if available and enabled
            alert_manager = None
            if (
                self._ops_config is not None
                and isinstance(self._ops_config, OperationsConfig)
                and self._ops_config.alerts.enabled
            ):
                alert_manager = AlertManager(config=self._ops_config.alerts)
                channels = []
                if self._ops_config.alerts.has_slack_configured():
                    channels.append("Slack")
                if self._ops_config.alerts.has_telegram_configured():
                    channels.append("Telegram")
                if channels:
                    logger.info(f"AlertManager configured with channels: {', '.join(channels)}")
                else:
                    logger.warning("AlertManager enabled but no channels configured")

            self._kill_switch = KillSwitch(
                strategy=self.strategy,
                config=kill_switch_config,
                alert_manager=alert_manager,
            )
            self._kill_switch.start_monitoring()
            if hasattr(self.strategy, "attach_kill_switch"):
                self.strategy.attach_kill_switch(self._kill_switch)
            logger.info("Kill switch monitoring started")
        except Exception as e:
            if self._require_live_safety:
                logger.critical(f"Kill switch failed to start in live mode: {e}")
                raise RuntimeError(f"Kill switch required for live trading but failed to start: {e}") from e
            logger.warning(f"Kill switch failed to start: {e}. Continuing without kill switch.")
            self._kill_switch = None

        self.is_running = True
        logger.info("OperationsManager started successfully")

    def stop(self) -> None:
        """Stop all operational services."""
        if not self.is_running:
            return

        logger.info("Stopping OperationsManager...")
        self._shutdown_event.set()

        # Stop metrics polling
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=5)

        # Stop kill switch
        if self._kill_switch:
            try:
                self._kill_switch.stop_monitoring()
            except Exception as e:
                logger.warning(f"Error stopping kill switch: {e}")

        # Stop FastAPI
        if self._api:
            try:
                self._api.stop_server()
            except Exception as e:
                logger.warning(f"Error stopping FastAPI: {e}")

        # Stop Prometheus
        if self._prometheus:
            try:
                self._prometheus.stop_server()
            except Exception as e:
                logger.warning(f"Error stopping Prometheus: {e}")

        self.is_running = False
        logger.info("OperationsManager stopped")

    def update_metrics(self) -> None:
        """Poll strategy for metrics and push to Prometheus."""
        if not self._prometheus or not self.strategy:
            return

        try:
            metrics = self.strategy.get_operational_metrics()
            self._prometheus.update_metrics(metrics)
        except Exception as e:
            logger.warning(f"Failed to update metrics: {e}")

    def _metrics_poll_loop(self) -> None:
        """Background loop that polls strategy metrics every 5 seconds."""
        while not self._shutdown_event.is_set():
            self.update_metrics()
            self._shutdown_event.wait(timeout=5.0)

    def _strategy_callback(self, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Callback for FastAPI to communicate with strategy.

        Accesses strategy internals (_throttle, _ladder_lock, etc.) as the
        ops manager is a privileged component with full strategy access.

        Parameters
        ----------
        operation : str
            Operation name (get_status, flatten, set_throttle, etc.)
        params : dict | None
            Operation parameters

        Returns
        -------
        dict
            Operation result
        """
        if params is None:
            params = {}

        try:
            if operation == "get_health":
                metrics = self.strategy.get_operational_metrics()
                return {
                    "running": True,
                    "last_bar_timestamp": metrics.get("last_bar_timestamp"),
                }

            if operation == "get_status":
                metrics = self.strategy.get_operational_metrics()
                return {
                    "running": True,
                    "positions": {
                        "long": {
                            "inventory_usdt": metrics["long_inventory_usdt"],
                            "quantity": 0.0,
                            "unrealized_pnl": 0.0,
                        },
                        "short": {
                            "inventory_usdt": metrics["short_inventory_usdt"],
                            "quantity": 0.0,
                            "unrealized_pnl": 0.0,
                        },
                    },
                    "margin_ratio": metrics["margin_ratio"],
                    "open_orders": metrics["open_orders_count"],
                    "pnl": {
                        "realized": metrics["realized_pnl_usdt"],
                        "unrealized": metrics["unrealized_pnl_usdt"],
                        "total": metrics["total_pnl_usdt"],
                    },
                }

            if operation == "flatten":
                side = params.get("side", "both")
                # Route through KillSwitch for consistent logging/alerting/verification
                if self._kill_switch is not None:
                    result = self._kill_switch.flatten_now(side, reason="API flatten request")
                else:
                    result = self.strategy.flatten_side(side)
                return {"success": True, **result}

            if operation == "set_throttle":
                throttle = params.get("throttle", 1.0)
                self.strategy.set_throttle(float(throttle))
                return {"success": True, "throttle": throttle}

            if operation == "get_ladders":
                snapshot = self.strategy.get_ladders_snapshot()
                return {
                    "mid_price": snapshot.get("mid_price", 0.0),
                    "long_ladder": [
                        {"price": r["price"], "qty": r["qty"], "rung": i}
                        for i, r in enumerate(snapshot.get("long_ladder", []))
                    ],
                    "short_ladder": [
                        {"price": r["price"], "qty": r["qty"], "rung": i}
                        for i, r in enumerate(snapshot.get("short_ladder", []))
                    ],
                }

            if operation == "get_orders":
                return {"orders": self.strategy.get_orders_snapshot()}

            return {"error": f"Unknown operation: {operation}"}

        except Exception as e:
            logger.error(f"Strategy callback error for '{operation}': {e}")
            return {"error": str(e)}
