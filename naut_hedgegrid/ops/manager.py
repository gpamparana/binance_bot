"""OperationsManager: Glue layer that wires Prometheus, FastAPI, and KillSwitch to the strategy.

Starts background services for monitoring and control when --enable-ops is passed.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

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
    ) -> None:
        self.strategy = strategy
        self.instrument_id = instrument_id
        self.prometheus_port = prometheus_port
        self.api_port = api_port
        self.api_key = api_key
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
            self._api.start_server(host="0.0.0.0", port=self.api_port)
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
                result = self.strategy.flatten_side(side)
                return {"success": True, **result}

            if operation == "set_throttle":
                throttle = params.get("throttle", 1.0)
                self.strategy._throttle = float(throttle)  # noqa: SLF001
                return {"success": True, "throttle": throttle}

            if operation == "get_ladders":
                long_ladder: list[dict[str, Any]] = []
                short_ladder: list[dict[str, Any]] = []
                mid_price = self.strategy._grid_center  # noqa: SLF001

                with self.strategy._ladder_lock:  # noqa: SLF001
                    if self.strategy._last_long_ladder:  # noqa: SLF001
                        long_ladder = [
                            {"price": r.price, "qty": r.qty, "rung": i}
                            for i, r in enumerate(
                                self.strategy._last_long_ladder.rungs  # noqa: SLF001
                            )
                        ]
                    if self.strategy._last_short_ladder:  # noqa: SLF001
                        short_ladder = [
                            {"price": r.price, "qty": r.qty, "rung": i}
                            for i, r in enumerate(
                                self.strategy._last_short_ladder.rungs  # noqa: SLF001
                            )
                        ]

                return {
                    "mid_price": mid_price,
                    "long_ladder": long_ladder,
                    "short_ladder": short_ladder,
                }

            if operation == "get_orders":
                orders = self.strategy._get_live_grid_orders()  # noqa: SLF001
                return {
                    "orders": [
                        {
                            "client_order_id": str(o.client_order_id),
                            "side": o.side.value,
                            "price": o.price,
                            "quantity": o.qty,
                            "status": o.status,
                        }
                        for o in orders
                    ]
                }

            return {"error": f"Unknown operation: {operation}"}

        except Exception as e:
            logger.error(f"Strategy callback error for '{operation}': {e}")
            return {"error": str(e)}
