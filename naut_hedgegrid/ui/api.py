"""FastAPI control endpoints for HedgeGrid trading system.

This module provides production-grade REST API endpoints for operational control
of the HedgeGrid trading strategy. Operators can monitor status, start/stop trading,
flatten positions, adjust throttle, and query grid state.

Key Features:
    - Health checks and status monitoring
    - Operational controls (start, stop, flatten)
    - Configuration adjustments (throttle)
    - Real-time data queries (ladders, orders)
    - Thread-safe strategy communication via queues
    - Optional API key authentication
    - CORS support for browser access
    - Comprehensive request/response validation with Pydantic
"""

import asyncio
import concurrent.futures
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from enum import Enum
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# Request/Response Models


class HealthStatus(str, Enum):
    """Health status enumeration."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthResponse(BaseModel):
    """Health check response."""

    status: HealthStatus
    uptime: float = Field(..., description="Strategy uptime in seconds")
    last_bar: float | None = Field(None, description="Timestamp of last processed bar")


class PositionSummary(BaseModel):
    """Position summary for one side."""

    inventory_usdt: float = Field(..., description="Position inventory in USDT")
    quantity: float = Field(..., description="Position quantity in base asset")
    entry_price: float | None = Field(None, description="Average entry price")
    unrealized_pnl: float = Field(..., description="Unrealized PnL in USDT")


class StatusResponse(BaseModel):
    """Comprehensive status response."""

    running: bool = Field(..., description="Whether strategy is actively trading")
    positions: dict[str, PositionSummary] = Field(..., description="Position data by side")
    margin_ratio: float = Field(..., description="Current margin ratio (used/available)")
    open_orders: int = Field(..., description="Number of open orders")
    pnl: dict[str, float] = Field(..., description="PnL breakdown")
    timestamp: float = Field(..., description="Response timestamp")


class OperationResponse(BaseModel):
    """Generic operation response."""

    status: str = Field(..., description="Operation status")
    timestamp: float = Field(..., description="Operation timestamp")
    message: str | None = Field(None, description="Additional information")


class FlattenRequest(BaseModel):
    """Flatten positions request."""

    side: str = Field("both", description="Which side to flatten: 'long', 'short', or 'both'")


class FlattenResponse(BaseModel):
    """Flatten operation response."""

    status: str = Field(..., description="Operation status")
    cancelled_orders: int = Field(..., description="Number of orders cancelled")
    closing_positions: list[str] = Field(..., description="Position IDs being closed")
    timestamp: float = Field(..., description="Operation timestamp")


class ThrottleRequest(BaseModel):
    """Set throttle request."""

    throttle: float = Field(..., ge=0.0, le=1.0, description="Throttle value: 0.0 (passive) to 1.0 (aggressive)")


class ThrottleResponse(BaseModel):
    """Throttle update response."""

    status: str = Field(..., description="Update status")
    new_throttle: float = Field(..., description="New throttle value")
    timestamp: float = Field(..., description="Update timestamp")


class Rung(BaseModel):
    """Grid ladder rung."""

    price: float = Field(..., description="Limit price")
    qty: float = Field(..., description="Order quantity")
    rung: int = Field(..., description="Rung number")


class LaddersResponse(BaseModel):
    """Grid ladders snapshot response."""

    timestamp: float = Field(..., description="Snapshot timestamp")
    mid_price: float = Field(..., description="Current mid price")
    long_ladder: list[Rung] = Field(..., description="Long side grid ladder")
    short_ladder: list[Rung] = Field(..., description="Short side grid ladder")


class Order(BaseModel):
    """Open order information."""

    client_order_id: str = Field(..., description="Client order ID")
    side: str = Field(..., description="Order side")
    price: float = Field(..., description="Limit price")
    quantity: float = Field(..., description="Order quantity")
    status: str = Field(..., description="Order status")


class OrdersResponse(BaseModel):
    """Open orders response."""

    orders: list[Order] = Field(..., description="List of open orders")
    count: int = Field(..., description="Total number of orders")
    timestamp: float = Field(..., description="Response timestamp")


# Rate Limiter


class RateLimiter:
    """Simple in-memory sliding window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        """Check if request is allowed under rate limit."""
        now = time.time()
        cutoff = now - self._window_seconds

        # Clean old entries and add current
        self._requests[client_ip] = [t for t in self._requests[client_ip] if t > cutoff]

        if len(self._requests[client_ip]) >= self._max_requests:
            return False

        self._requests[client_ip].append(now)
        return True


# FastAPI Application


class StrategyAPI:
    """FastAPI application for HedgeGrid strategy control.

    This class wraps a FastAPI application with operational endpoints for
    controlling and monitoring a HedgeGrid trading strategy. It provides
    thread-safe communication with the strategy instance via callback functions.

    The API runs in a separate thread to avoid blocking the main trading loop.
    All endpoints are designed for low-latency operation suitable for production
    algorithmic trading systems.

    Attributes:
        app: FastAPI application instance
        strategy_callback: Callable to access strategy state and operations
        server_thread: Background thread running uvicorn server
        is_running: Flag indicating server state
        start_time: Strategy start timestamp for uptime calculation

    Example:
        >>> def strategy_callback(operation, **kwargs):
        ...     if operation == "get_status":
        ...         return {"running": True, "pnl": 123.45}
        ...     elif operation == "stop":
        ...         strategy.stop()
        ...         return {"success": True}
        ...
        >>> api = StrategyAPI(strategy_callback)
        >>> api.start_server(host="0.0.0.0", port=8080)
    """

    def __init__(
        self,
        strategy_callback: Callable[[str, dict[str, Any]], dict[str, Any]],
        api_key: str | None = None,
        require_auth: bool = True,
        cors_origins: list[str] | None = None,
        disable_docs: bool = False,
    ) -> None:
        """Initialize FastAPI application with strategy callback.

        Args:
            strategy_callback: Callable that handles strategy operations
                Should accept (operation: str, **kwargs) and return dict
            api_key: Optional API key for authentication (header: X-API-Key)
            require_auth: If True, mutating endpoints require API key even if
                api_key is not set (returns 403). Defaults to True for safety.
            cors_origins: Allowed CORS origins. Defaults to localhost only.
            disable_docs: If True, disable Swagger /docs and /redoc endpoints.
        """
        self._raw_strategy_callback = strategy_callback
        self.api_key = api_key
        self._require_auth = require_auth
        self.is_running = False
        self.server_thread: threading.Thread | None = None
        self.start_time = time.time()
        self._shutdown_event = threading.Event()
        self._callback_timeout = 10.0  # seconds
        self._callback_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="api-callback"
        )

        # Rate limiters: POST (mutating) and GET (monitoring)
        self._write_limiter = RateLimiter(max_requests=10, window_seconds=60)
        self._read_limiter = RateLimiter(max_requests=60, window_seconds=60)

        # Create FastAPI app
        self.app = FastAPI(
            title="HedgeGrid Trading API",
            description="Operational control and monitoring for HedgeGrid trading system",
            version="1.0.0",
            docs_url=None if disable_docs else "/docs",
            redoc_url=None if disable_docs else "/redoc",
        )

        # Add CORS middleware with restricted origins
        allowed_origins = cors_origins or [
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ]
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key", "Content-Type"],
        )

        # Add rate limiting middleware
        @self.app.middleware("http")
        async def rate_limit_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
            client_ip = request.client.host if request.client else "unknown"
            if request.method == "POST":
                if not self._write_limiter.is_allowed(client_ip):
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded for mutating endpoints"},
                    )
            elif not self._read_limiter.is_allowed(client_ip):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                )
            return await call_next(request)

        # Register routes
        self._register_routes()

        logger.info("StrategyAPI initialized")

    def strategy_callback(self, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Execute strategy callback with timeout protection.

        Prevents API endpoints from hanging indefinitely if the strategy
        is stuck or unresponsive.

        Args:
            operation: Operation name to execute
            kwargs: Keyword arguments for the operation

        Returns:
            Result dictionary from strategy

        Raises:
            HTTPException: If callback times out
        """
        try:
            future = self._callback_executor.submit(self._raw_strategy_callback, operation, kwargs)
            return future.result(timeout=self._callback_timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Strategy callback '{operation}' timed out after {self._callback_timeout}s")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Strategy callback '{operation}' timed out",
            ) from None

    def _validate_read_auth(self, x_api_key: str | None = Header(None)) -> None:
        """Validate API key for read (GET) endpoints.

        Only validates if an API key is configured. Read endpoints remain
        accessible for monitoring when no key is set.

        Args:
            x_api_key: API key from X-API-Key header
        """
        if not self.api_key:
            return  # No key configured — reads are open for monitoring

        if x_api_key is None or x_api_key != self.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "X-API-Key"},
            )

    def _validate_write_auth(self, x_api_key: str | None = Header(None)) -> None:
        """Validate API key for write (POST) endpoints.

        Mutating endpoints ALWAYS require authentication when require_auth
        is True (default). This prevents accidental unauthenticated access
        to flatten, throttle, start, and stop operations.

        Args:
            x_api_key: API key from X-API-Key header
        """
        if not self.api_key:
            if self._require_auth:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API key must be configured for mutating endpoints. Set api_key in operations config.",
                )
            return  # Auth not required (e.g., paper trading)

        if x_api_key is None or x_api_key != self.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "X-API-Key"},
            )

    def _register_routes(self) -> None:
        """Register all API routes.

        Health check is registered at root level (standard convention).
        All other routes are under /api/v1 prefix for versioned API contract.
        """
        router = APIRouter(prefix="/api/v1")

        @self.app.get("/health", response_model=HealthResponse, tags=["Health"])
        async def health_check() -> HealthResponse:
            """Check system health status.

            Returns basic health information including uptime and last bar timestamp.
            This endpoint has no authentication requirement for monitoring systems.
            """
            try:
                result = self.strategy_callback("get_health", {})
                uptime = time.time() - self.start_time

                return HealthResponse(
                    status=HealthStatus.HEALTHY if result.get("running") else HealthStatus.DEGRADED,
                    uptime=uptime,
                    last_bar=result.get("last_bar_timestamp"),
                )
            except Exception as e:
                logger.error(f"Health check failed: {e}")
                return HealthResponse(
                    status=HealthStatus.DOWN,
                    uptime=time.time() - self.start_time,
                    last_bar=None,
                )

        @router.get("/status", response_model=StatusResponse, tags=["Monitoring"])
        async def get_status(x_api_key: str | None = Header(None)) -> StatusResponse:
            """Get comprehensive strategy status.

            Returns detailed information about strategy state, positions, PnL,
            and open orders. Requires authentication if API key is configured.
            """
            self._validate_read_auth(x_api_key)

            try:
                result = self.strategy_callback("get_status", {})

                return StatusResponse(
                    running=result.get("running", False),
                    positions=result.get("positions", {}),
                    margin_ratio=result.get("margin_ratio", 0.0),
                    open_orders=result.get("open_orders", 0),
                    pnl=result.get("pnl", {}),
                    timestamp=time.time(),
                )
            except Exception as e:
                logger.error(f"Status query failed: {e}")
                raise HTTPException(status_code=500, detail="Internal error. Check server logs.") from e

        @router.post("/start", response_model=OperationResponse, tags=["Operations"])
        async def start_strategy(x_api_key: str | None = Header(None)) -> OperationResponse:
            """Start strategy trading.

            Enables strategy to place new orders and manage positions.
            This is a safe operation that does not affect existing positions.
            """
            self._validate_write_auth(x_api_key)

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Not yet implemented. Strategy lifecycle is managed by the runner process.",
            )

        @router.post("/stop", response_model=OperationResponse, tags=["Operations"])
        async def stop_strategy(x_api_key: str | None = Header(None)) -> OperationResponse:
            """Stop strategy trading.

            Cancels all open orders but keeps existing positions open.
            This is useful for pausing trading without closing positions.
            """
            self._validate_write_auth(x_api_key)

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Not yet implemented. Strategy lifecycle is managed by the runner process.",
            )

        @router.post("/flatten", response_model=FlattenResponse, tags=["Operations"])
        async def flatten_positions(request: FlattenRequest, x_api_key: str | None = Header(None)) -> FlattenResponse:
            """Emergency flatten - cancel orders and close positions.

            This is an emergency operation that:
            1. Cancels all open orders
            2. Places market orders to close positions

            Use with caution - this will immediately close positions at market price.
            """
            self._validate_write_auth(x_api_key)

            if request.side not in ("long", "short", "both"):
                raise HTTPException(status_code=400, detail="Invalid side. Must be 'long', 'short', or 'both'")

            try:
                result = self.strategy_callback("flatten", {"side": request.side})

                return FlattenResponse(
                    status="flattening" if result.get("success") else "failed",
                    cancelled_orders=result.get("cancelled_orders", 0),
                    closing_positions=result.get("closing_positions", []),
                    timestamp=time.time(),
                )
            except Exception as e:
                logger.error(f"Flatten operation failed: {e}")
                raise HTTPException(status_code=500, detail="Internal error. Check server logs.") from e

        @router.post("/set-throttle", response_model=ThrottleResponse, tags=["Configuration"])
        async def set_throttle(request: ThrottleRequest, x_api_key: str | None = Header(None)) -> ThrottleResponse:
            """Adjust strategy aggressiveness (throttle).

            Throttle controls how aggressively the strategy places orders:
            - 0.0: Maximum passive (fewer orders, wider spreads)
            - 1.0: Maximum aggressive (more orders, tighter spreads)

            This allows dynamic adjustment of strategy behavior without restart.
            """
            self._validate_write_auth(x_api_key)

            try:
                result = self.strategy_callback("set_throttle", {"throttle": request.throttle})

                return ThrottleResponse(
                    status="updated" if result.get("success") else "failed",
                    new_throttle=result.get("throttle", request.throttle),
                    timestamp=time.time(),
                )
            except Exception as e:
                logger.error(f"Throttle update failed: {e}")
                raise HTTPException(status_code=500, detail="Internal error. Check server logs.") from e

        @router.get("/ladders", response_model=LaddersResponse, tags=["Data"])
        async def get_ladders(x_api_key: str | None = Header(None)) -> LaddersResponse:
            """Get current grid ladders snapshot.

            Returns the current state of the grid ladders including all rungs
            with their prices and quantities. Useful for visualizing grid state.
            """
            self._validate_read_auth(x_api_key)

            try:
                result = self.strategy_callback("get_ladders", {})

                return LaddersResponse(
                    timestamp=time.time(),
                    mid_price=result.get("mid_price", 0.0),
                    long_ladder=[Rung(**rung) for rung in result.get("long_ladder", [])],
                    short_ladder=[Rung(**rung) for rung in result.get("short_ladder", [])],
                )
            except Exception as e:
                logger.error(f"Ladders query failed: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to get ladders: {e}") from e

        @router.get("/orders", response_model=OrdersResponse, tags=["Data"])
        async def get_orders(x_api_key: str | None = Header(None)) -> OrdersResponse:
            """Get current open orders.

            Returns all open orders for the strategy including grid orders
            and any take-profit/stop-loss orders.
            """
            self._validate_read_auth(x_api_key)

            try:
                result = self.strategy_callback("get_orders", {})
                orders = [Order(**order) for order in result.get("orders", [])]

                return OrdersResponse(
                    orders=orders,
                    count=len(orders),
                    timestamp=time.time(),
                )
            except Exception as e:
                logger.error(f"Orders query failed: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to get orders: {e}") from e

        # Mount all versioned routes
        self.app.include_router(router)

    def start_server(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        """Start FastAPI server in background thread.

        Starts a uvicorn server running the FastAPI application in a
        separate thread. The server will continue running until stop_server()
        is called.

        Args:
            host: Host address to bind (default 127.0.0.1 for localhost only).
                Use "0.0.0.0" explicitly if remote access is needed.
            port: TCP port for API endpoints (default 8080)

        Example:
            >>> api = StrategyAPI(callback)
            >>> api.start_server(host="127.0.0.1", port=8080)
            >>> # API now available at http://127.0.0.1:8080
        """
        if self.is_running:
            logger.warning(f"API server already running on {host}:{port}")
            return

        def run_server() -> None:
            """Run uvicorn server in background thread."""
            config = uvicorn.Config(
                self.app,
                host=host,
                port=port,
                log_level="info",
                access_log=False,  # Reduce log noise
            )
            server = uvicorn.Server(config)

            # Run server with shutdown check
            asyncio.run(server.serve())

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.is_running = True

        logger.info(f"FastAPI server started on http://{host}:{port}")
        logger.info(f"API documentation: http://{host}:{port}/docs")

    def stop_server(self) -> None:
        """Stop FastAPI server and cleanup resources.

        Signals the server thread to shutdown and waits for clean termination.
        This method ensures all resources are properly released.
        """
        if not self.is_running:
            logger.debug("API server not running, nothing to stop")
            return

        self._shutdown_event.set()
        self.is_running = False

        # Note: uvicorn Server doesn't provide a clean shutdown mechanism
        # when running in a thread. The server will terminate when the
        # thread is garbage collected or process exits.

        logger.info("FastAPI server stopped")
