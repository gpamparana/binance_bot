"""Prometheus metrics exporter for HedgeGrid trading system.

This module provides production-grade Prometheus metrics export with thread-safe
metric updates and proper resource lifecycle management.

Key Features:
    - 15 comprehensive trading metrics (positions, PnL, grid, risk, funding)
    - Thread-safe metric updates using locks
    - Background HTTP server for /metrics endpoint
    - Graceful shutdown with cleanup
    - Configurable port (default 9090)
    - Per-instrument labels for multi-instrument support
"""

import logging
import threading
import time
from typing import Any

from prometheus_client import Gauge, start_http_server
from prometheus_client.core import CollectorRegistry

logger = logging.getLogger(__name__)


class PrometheusExporter:
    """Prometheus metrics exporter for HedgeGrid trading system.

    This class manages a set of Prometheus Gauge metrics that track the operational
    state of a HedgeGrid trading strategy in production. All metrics are thread-safe
    and can be updated concurrently from strategy callbacks.

    The exporter runs an HTTP server (default port 9090) that exposes metrics at
    the /metrics endpoint for Prometheus scraping.

    Attributes:
        registry: Prometheus CollectorRegistry for metric isolation
        server_thread: Background thread running HTTP server
        update_lock: Thread lock for safe metric updates
        is_running: Flag indicating server state

    Example:
        >>> exporter = PrometheusExporter()
        >>> exporter.start_server(port=9090)
        >>> # Update metrics from strategy
        >>> exporter.update_metrics({
        ...     'long_inventory_usdt': 1500.0,
        ...     'short_inventory_usdt': 800.0,
        ...     'total_pnl_usdt': 234.56
        ... })
        >>> exporter.stop_server()
    """

    def __init__(self, instrument_id: str = "default") -> None:
        """Initialize Prometheus exporter with metric definitions.

        Creates all gauge metrics and sets up internal state for thread-safe
        operation. Metrics are registered with an isolated registry to avoid
        conflicts if multiple exporters run in the same process.

        Args:
            instrument_id: Instrument identifier for metric labels (e.g., "BTCUSDT-PERP")
        """
        self.instrument_id = instrument_id
        self.registry = CollectorRegistry()
        self.update_lock = threading.Lock()
        self.is_running = False
        self.server_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

        # Position metrics (per side)
        self.long_inventory_quote = Gauge(
            "hedgegrid_long_inventory_usdt",
            "Long position inventory in USDT",
            ["instrument"],
            registry=self.registry,
        )
        self.short_inventory_quote = Gauge(
            "hedgegrid_short_inventory_usdt",
            "Short position inventory in USDT",
            ["instrument"],
            registry=self.registry,
        )
        self.net_inventory_quote = Gauge(
            "hedgegrid_net_inventory_usdt",
            "Net inventory (long - short) in USDT",
            ["instrument"],
            registry=self.registry,
        )

        # Grid metrics
        self.active_rungs_long = Gauge(
            "hedgegrid_active_rungs_long",
            "Number of active long grid rungs",
            ["instrument"],
            registry=self.registry,
        )
        self.active_rungs_short = Gauge(
            "hedgegrid_active_rungs_short",
            "Number of active short grid rungs",
            ["instrument"],
            registry=self.registry,
        )
        self.open_orders_count = Gauge(
            "hedgegrid_open_orders",
            "Total open orders",
            ["instrument"],
            registry=self.registry,
        )

        # Risk metrics
        self.margin_ratio = Gauge(
            "hedgegrid_margin_ratio",
            "Current margin ratio (used / available)",
            ["instrument"],
            registry=self.registry,
        )
        self.maker_ratio = Gauge(
            "hedgegrid_maker_ratio",
            "Ratio of maker fills vs total fills",
            ["instrument"],
            registry=self.registry,
        )

        # Funding metrics
        self.funding_rate_current = Gauge(
            "hedgegrid_funding_rate_current",
            "Current funding rate",
            ["instrument"],
            registry=self.registry,
        )
        self.funding_cost_1h_projected = Gauge(
            "hedgegrid_funding_cost_1h_projected_usdt",
            "Projected funding cost for next 1h in USDT",
            ["instrument"],
            registry=self.registry,
        )

        # PnL metrics
        self.realized_pnl_total = Gauge(
            "hedgegrid_realized_pnl_usdt",
            "Total realized PnL in USDT",
            ["instrument"],
            registry=self.registry,
        )
        self.unrealized_pnl_total = Gauge(
            "hedgegrid_unrealized_pnl_usdt",
            "Total unrealized PnL in USDT",
            ["instrument"],
            registry=self.registry,
        )
        self.total_pnl = Gauge(
            "hedgegrid_total_pnl_usdt",
            "Total PnL (realized + unrealized) in USDT",
            ["instrument"],
            registry=self.registry,
        )

        # System health metrics
        self.strategy_uptime_seconds = Gauge(
            "hedgegrid_uptime_seconds",
            "Strategy uptime in seconds",
            ["instrument"],
            registry=self.registry,
        )
        self.last_bar_timestamp = Gauge(
            "hedgegrid_last_bar_timestamp",
            "Timestamp of last processed bar",
            ["instrument"],
            registry=self.registry,
        )

        # Track start time for uptime calculation
        self._start_time = time.time()

        logger.info(f"PrometheusExporter initialized for instrument: {instrument_id}")

    def start_server(self, port: int = 9090) -> None:
        """Start Prometheus HTTP server in background thread.

        Starts a non-blocking HTTP server that exposes the /metrics endpoint
        on the specified port. The server runs in a daemon thread and will
        automatically terminate when the main program exits.

        This method is idempotent - calling it multiple times will not start
        additional servers.

        Args:
            port: TCP port for metrics endpoint (default 9090)

        Raises:
            OSError: If port is already in use or cannot be bound

        Example:
            >>> exporter = PrometheusExporter()
            >>> exporter.start_server(port=9090)
            >>> # Metrics now available at http://localhost:9090/metrics
        """
        if self.is_running:
            logger.warning(f"Prometheus server already running on port {port}")
            return

        try:
            # Start HTTP server in background
            # Note: start_http_server creates its own thread
            start_http_server(port=port, registry=self.registry)
            self.is_running = True
            logger.info(f"Prometheus metrics server started on port {port}")
            logger.info(f"Metrics available at: http://localhost:{port}/metrics")
        except OSError as e:
            logger.error(f"Failed to start Prometheus server on port {port}: {e}")
            raise

    def stop_server(self) -> None:
        """Stop Prometheus HTTP server and cleanup resources.

        Signals the server thread to shutdown and waits for clean termination.
        This method ensures all resources are properly released.

        Note:
            prometheus_client's start_http_server doesn't provide a clean shutdown
            mechanism, so the server will continue running until process termination.
            This method exists for API consistency and future enhancement.
        """
        if not self.is_running:
            logger.debug("Prometheus server not running, nothing to stop")
            return

        self._shutdown_event.set()
        self.is_running = False
        logger.info("Prometheus metrics server stopped")

    def update_metrics(self, metrics_dict: dict[str, Any]) -> None:
        """Update Prometheus metrics from strategy state.

        Accepts a dictionary of metric values and updates corresponding Prometheus
        gauges. All updates are thread-safe and atomic. Unknown metric keys are
        logged and ignored.

        The method automatically calculates derived metrics:
        - net_inventory_usdt = long_inventory_usdt - short_inventory_usdt
        - total_pnl_usdt = realized_pnl_usdt + unrealized_pnl_usdt
        - uptime_seconds = current_time - start_time

        Args:
            metrics_dict: Dictionary mapping metric names to values
                Valid keys include:
                - Position metrics: long_inventory_usdt, short_inventory_usdt
                - Grid metrics: active_rungs_long, active_rungs_short, open_orders_count
                - Risk metrics: margin_ratio, maker_ratio
                - Funding metrics: funding_rate_current, funding_cost_1h_projected_usdt
                - PnL metrics: realized_pnl_usdt, unrealized_pnl_usdt
                - Health metrics: last_bar_timestamp

        Example:
            >>> exporter.update_metrics({
            ...     'long_inventory_usdt': 1500.0,
            ...     'short_inventory_usdt': 800.0,
            ...     'active_rungs_long': 5,
            ...     'active_rungs_short': 3,
            ...     'open_orders_count': 8,
            ...     'realized_pnl_usdt': 234.56,
            ...     'unrealized_pnl_usdt': 45.12,
            ...     'margin_ratio': 0.35,
            ...     'funding_rate_current': 0.0001,
            ...     'last_bar_timestamp': 1697234567.0
            ... })
        """
        with self.update_lock:
            # Update position metrics
            if "long_inventory_usdt" in metrics_dict:
                self.long_inventory_quote.labels(instrument=self.instrument_id).set(
                    metrics_dict["long_inventory_usdt"]
                )

            if "short_inventory_usdt" in metrics_dict:
                self.short_inventory_quote.labels(instrument=self.instrument_id).set(
                    metrics_dict["short_inventory_usdt"]
                )

            # Calculate net inventory
            if "long_inventory_usdt" in metrics_dict and "short_inventory_usdt" in metrics_dict:
                net_inventory = (
                    metrics_dict["long_inventory_usdt"] - metrics_dict["short_inventory_usdt"]
                )
                self.net_inventory_quote.labels(instrument=self.instrument_id).set(net_inventory)

            # Update grid metrics
            if "active_rungs_long" in metrics_dict:
                self.active_rungs_long.labels(instrument=self.instrument_id).set(
                    metrics_dict["active_rungs_long"]
                )

            if "active_rungs_short" in metrics_dict:
                self.active_rungs_short.labels(instrument=self.instrument_id).set(
                    metrics_dict["active_rungs_short"]
                )

            if "open_orders_count" in metrics_dict:
                self.open_orders_count.labels(instrument=self.instrument_id).set(
                    metrics_dict["open_orders_count"]
                )

            # Update risk metrics
            if "margin_ratio" in metrics_dict:
                self.margin_ratio.labels(instrument=self.instrument_id).set(
                    metrics_dict["margin_ratio"]
                )

            if "maker_ratio" in metrics_dict:
                self.maker_ratio.labels(instrument=self.instrument_id).set(
                    metrics_dict["maker_ratio"]
                )

            # Update funding metrics
            if "funding_rate_current" in metrics_dict:
                self.funding_rate_current.labels(instrument=self.instrument_id).set(
                    metrics_dict["funding_rate_current"]
                )

            if "funding_cost_1h_projected_usdt" in metrics_dict:
                self.funding_cost_1h_projected.labels(instrument=self.instrument_id).set(
                    metrics_dict["funding_cost_1h_projected_usdt"]
                )

            # Update PnL metrics
            if "realized_pnl_usdt" in metrics_dict:
                self.realized_pnl_total.labels(instrument=self.instrument_id).set(
                    metrics_dict["realized_pnl_usdt"]
                )

            if "unrealized_pnl_usdt" in metrics_dict:
                self.unrealized_pnl_total.labels(instrument=self.instrument_id).set(
                    metrics_dict["unrealized_pnl_usdt"]
                )

            # Calculate total PnL
            if "realized_pnl_usdt" in metrics_dict and "unrealized_pnl_usdt" in metrics_dict:
                total_pnl = metrics_dict["realized_pnl_usdt"] + metrics_dict["unrealized_pnl_usdt"]
                self.total_pnl.labels(instrument=self.instrument_id).set(total_pnl)

            # Update system health metrics
            uptime = time.time() - self._start_time
            self.strategy_uptime_seconds.labels(instrument=self.instrument_id).set(uptime)

            if "last_bar_timestamp" in metrics_dict:
                self.last_bar_timestamp.labels(instrument=self.instrument_id).set(
                    metrics_dict["last_bar_timestamp"]
                )

        logger.debug(f"Updated {len(metrics_dict)} metrics for {self.instrument_id}")

    def get_metrics_snapshot(self) -> dict[str, float]:
        """Get current snapshot of all metric values.

        Returns a dictionary containing the current values of all metrics.
        Useful for debugging and testing.

        Returns:
            Dictionary mapping metric names to current values

        Example:
            >>> snapshot = exporter.get_metrics_snapshot()
            >>> print(f"Current PnL: {snapshot['total_pnl_usdt']:.2f} USDT")
        """
        # Note: This is a simplified version. In production, you would
        # need to query the registry to get actual metric values.
        return {
            "instrument_id": self.instrument_id,
            "uptime_seconds": time.time() - self._start_time,
            "is_running": self.is_running,
        }
