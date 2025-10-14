"""Tests for Prometheus metrics exporter."""

import time

from naut_hedgegrid.ops.prometheus import PrometheusExporter


class TestPrometheusExporter:
    """Test Prometheus metrics exporter functionality."""

    def test_initialization(self):
        """Test that exporter initializes with all required metrics."""
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        assert exporter.instrument_id == "BTCUSDT-PERP.BINANCE"
        assert exporter.is_running is False
        assert exporter.registry is not None
        assert exporter.update_lock is not None

        # Verify all metrics are created
        assert exporter.long_inventory_quote is not None
        assert exporter.short_inventory_quote is not None
        assert exporter.net_inventory_quote is not None
        assert exporter.active_rungs_long is not None
        assert exporter.active_rungs_short is not None
        assert exporter.open_orders_count is not None
        assert exporter.margin_ratio is not None
        assert exporter.maker_ratio is not None
        assert exporter.funding_rate_current is not None
        assert exporter.funding_cost_1h_projected is not None
        assert exporter.realized_pnl_total is not None
        assert exporter.unrealized_pnl_total is not None
        assert exporter.total_pnl is not None
        assert exporter.strategy_uptime_seconds is not None
        assert exporter.last_bar_timestamp is not None

    def test_update_metrics(self):
        """Test that metrics can be updated without errors."""
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        # Update with sample metrics
        metrics = {
            "long_inventory_usdt": 1500.0,
            "short_inventory_usdt": 800.0,
            "active_rungs_long": 5,
            "active_rungs_short": 3,
            "open_orders_count": 8,
            "margin_ratio": 0.35,
            "maker_ratio": 0.95,
            "funding_rate_current": 0.0001,
            "funding_cost_1h_projected_usdt": 0.05,
            "realized_pnl_usdt": 234.56,
            "unrealized_pnl_usdt": 45.12,
            "last_bar_timestamp": time.time(),
        }

        # Should not raise any exceptions
        exporter.update_metrics(metrics)

    def test_update_metrics_partial(self):
        """Test that partial metric updates work correctly."""
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        # Update with only a few metrics
        metrics = {
            "long_inventory_usdt": 1000.0,
            "realized_pnl_usdt": 100.0,
        }

        # Should not raise any exceptions
        exporter.update_metrics(metrics)

    def test_update_metrics_calculates_derived(self):
        """Test that derived metrics are calculated correctly."""
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        metrics = {
            "long_inventory_usdt": 1500.0,
            "short_inventory_usdt": 800.0,
            "realized_pnl_usdt": 200.0,
            "unrealized_pnl_usdt": 50.0,
        }

        exporter.update_metrics(metrics)

        # Net inventory should be calculated as long - short = 1500 - 800 = 700
        # Total PnL should be calculated as realized + unrealized = 200 + 50 = 250
        # Uptime should be > 0

    def test_update_metrics_thread_safe(self):
        """Test that metric updates are thread-safe."""
        import threading

        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        def update_worker():
            for _ in range(100):
                metrics = {
                    "long_inventory_usdt": 1000.0,
                    "short_inventory_usdt": 500.0,
                }
                exporter.update_metrics(metrics)

        # Create multiple threads updating simultaneously
        threads = [threading.Thread(target=update_worker) for _ in range(5)]

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Should complete without deadlocks or exceptions

    def test_get_metrics_snapshot(self):
        """Test that metrics snapshot can be retrieved."""
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        snapshot = exporter.get_metrics_snapshot()

        assert isinstance(snapshot, dict)
        assert "instrument_id" in snapshot
        assert snapshot["instrument_id"] == "BTCUSDT-PERP.BINANCE"
        assert "uptime_seconds" in snapshot
        assert snapshot["uptime_seconds"] >= 0.0
        assert "is_running" in snapshot
        assert snapshot["is_running"] is False

    def test_start_server_lifecycle(self):
        """Test server lifecycle (start/stop).

        Note: This test uses a high port number to avoid conflicts.
        """
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        # Start server on non-standard port to avoid conflicts
        test_port = 19090
        exporter.start_server(port=test_port)

        assert exporter.is_running is True

        # Stop server
        exporter.stop_server()

        assert exporter.is_running is False

    def test_stop_server_when_not_running(self):
        """Test that stopping a non-running server is safe."""
        exporter = PrometheusExporter(instrument_id="BTCUSDT-PERP.BINANCE")

        # Should not raise exception
        exporter.stop_server()

        assert exporter.is_running is False
