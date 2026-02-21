"""Tests for FastAPI control endpoints."""

import pytest
from fastapi.testclient import TestClient

from naut_hedgegrid.ui.api import StrategyAPI


class TestStrategyAPI:
    """Test FastAPI control endpoints."""

    @pytest.fixture
    def mock_callback(self):
        """Create a mock strategy callback for testing."""

        def callback(operation: str, kwargs: dict) -> dict:
            """Mock callback that returns predefined responses."""
            if operation == "get_health":
                return {
                    "running": True,
                    "last_bar_timestamp": 1234567890.0,
                }
            if operation == "get_status":
                return {
                    "running": True,
                    "positions": {
                        "long": {
                            "inventory_usdt": 1500.0,
                            "quantity": 0.5,
                            "entry_price": 30000.0,
                            "unrealized_pnl": 50.0,
                        },
                        "short": {
                            "inventory_usdt": 800.0,
                            "quantity": 0.3,
                            "entry_price": 29000.0,
                            "unrealized_pnl": -20.0,
                        },
                    },
                    "margin_ratio": 0.35,
                    "open_orders": 8,
                    "pnl": {
                        "realized": 200.0,
                        "unrealized": 30.0,
                        "total": 230.0,
                    },
                }
            if operation == "start" or operation == "stop":
                return {"success": True}
            if operation == "flatten":
                return {
                    "success": True,
                    "cancelled_orders": 5,
                    "closing_positions": ["LONG", "SHORT"],
                }
            if operation == "set_throttle":
                return {"success": True, "throttle": kwargs.get("throttle", 1.0)}
            if operation == "get_ladders":
                return {
                    "mid_price": 30000.0,
                    "long_ladder": [
                        {"price": 29900.0, "qty": 0.1, "rung": 0},
                        {"price": 29800.0, "qty": 0.1, "rung": 1},
                    ],
                    "short_ladder": [
                        {"price": 30100.0, "qty": 0.1, "rung": 0},
                        {"price": 30200.0, "qty": 0.1, "rung": 1},
                    ],
                }
            if operation == "get_orders":
                return {
                    "orders": [
                        {
                            "client_order_id": "HG1-LONG-00-123",
                            "side": "BUY",
                            "price": 29900.0,
                            "quantity": 0.1,
                            "status": "OPEN",
                        },
                    ]
                }
            return {"success": False, "error": f"Unknown operation: {operation}"}

        return callback

    @pytest.fixture
    def api(self, mock_callback):
        """Create StrategyAPI instance with mock callback (auth disabled for unit tests)."""
        return StrategyAPI(strategy_callback=mock_callback, require_auth=False)

    @pytest.fixture
    def client(self, api):
        """Create TestClient for API testing."""
        return TestClient(api.app)

    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "uptime" in data
        assert data["status"] in ("healthy", "degraded", "down")

    def test_status_endpoint(self, client):
        """Test status endpoint."""
        response = client.get("/status")

        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "positions" in data
        assert "margin_ratio" in data
        assert "open_orders" in data
        assert "pnl" in data
        assert data["running"] is True
        assert data["open_orders"] == 8

    def test_start_endpoint(self, client):
        """Test start strategy endpoint."""
        response = client.post("/start")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "timestamp" in data

    def test_stop_endpoint(self, client):
        """Test stop strategy endpoint."""
        response = client.post("/stop")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "timestamp" in data

    def test_flatten_endpoint(self, client):
        """Test flatten positions endpoint."""
        response = client.post("/flatten", json={"side": "both"})

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "cancelled_orders" in data
        assert "closing_positions" in data
        assert data["cancelled_orders"] == 5

    def test_flatten_endpoint_invalid_side(self, client):
        """Test flatten with invalid side parameter."""
        response = client.post("/flatten", json={"side": "invalid"})

        assert response.status_code == 400

    def test_set_throttle_endpoint(self, client):
        """Test set throttle endpoint."""
        response = client.post("/set-throttle", json={"throttle": 0.75})

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "new_throttle" in data
        assert data["new_throttle"] == 0.75

    def test_set_throttle_invalid_value(self, client):
        """Test set throttle with invalid value."""
        response = client.post("/set-throttle", json={"throttle": 1.5})

        assert response.status_code == 422  # Validation error

    def test_get_ladders_endpoint(self, client):
        """Test get ladders endpoint."""
        response = client.get("/ladders")

        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        assert "mid_price" in data
        assert "long_ladder" in data
        assert "short_ladder" in data
        assert data["mid_price"] == 30000.0
        assert len(data["long_ladder"]) == 2

    def test_get_orders_endpoint(self, client):
        """Test get orders endpoint."""
        response = client.get("/orders")

        assert response.status_code == 200
        data = response.json()
        assert "orders" in data
        assert "count" in data
        assert "timestamp" in data
        assert len(data["orders"]) == 1
        assert data["count"] == 1

    def test_api_key_authentication(self):
        """Test API key authentication."""

        def auth_callback(operation: str, kwargs: dict) -> dict:
            return {"success": True}

        api = StrategyAPI(strategy_callback=auth_callback, api_key="secret123")
        client = TestClient(api.app)

        # Request without API key should fail
        response = client.get("/status")
        assert response.status_code == 401

        # Request with wrong API key should fail
        response = client.get("/status", headers={"X-API-Key": "wrong"})
        assert response.status_code == 401

        # Request with correct API key should succeed
        response = client.get("/status", headers={"X-API-Key": "secret123"})
        assert response.status_code == 200

    def test_health_endpoint_no_auth(self):
        """Test that health endpoint works without authentication."""

        def auth_callback(operation: str, kwargs: dict) -> dict:
            return {"running": True, "last_bar_timestamp": None}

        api = StrategyAPI(strategy_callback=auth_callback, api_key="secret123")
        client = TestClient(api.app)

        # Health endpoint should work without auth
        response = client.get("/health")
        assert response.status_code == 200
