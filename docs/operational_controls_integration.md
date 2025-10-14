# Operational Controls Integration

This document describes the integration of operational controls (metrics, kill switch, API) with the HedgeGridV1 trading strategy.

## Overview

The operational controls system provides production-grade monitoring and control capabilities for live trading strategies:

- **Prometheus Metrics**: Real-time metrics export for monitoring dashboards (Grafana)
- **FastAPI Control Endpoints**: REST API for strategy control and state inspection
- **Kill Switch**: Emergency position flattening and order cancellation
- **Operations Manager**: Unified coordination of all operational infrastructure

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     TradingNode                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              HedgeGridV1 Strategy                      │  │
│  │                                                        │  │
│  │  • get_operational_metrics()   [Metrics Exposure]    │  │
│  │  • attach_kill_switch()        [Safety Integration]   │  │
│  │  • flatten_side()              [Emergency Control]    │  │
│  │  • set_throttle()              [Runtime Control]      │  │
│  │  • get_ladders_snapshot()      [State Inspection]     │  │
│  │                                                        │  │
│  │  Thread-safe via _ops_lock                           │  │
│  └────────────────┬───────────────────────────────────────┘  │
└───────────────────┼───────────────────────────────────────────┘
                    │
                    ├─────────────────────────────────────────┐
                    │                                         │
          ┌─────────▼──────────┐                 ┌───────────▼────────┐
          │ OperationsManager  │                 │   Kill Switch       │
          │                    │                 │   (future work)     │
          │ • start()          │                 │                     │
          │ • stop()           │                 │ • monitor_risk()    │
          │ • update_metrics() │                 │ • trigger_flatten() │
          └────┬───────────┬───┘                 └─────────────────────┘
               │           │
       ┌───────▼──┐    ┌──▼─────────┐
       │Prometheus│    │  FastAPI   │
       │ :9090    │    │  :8080     │
       │ /metrics │    │  /docs     │
       └──────────┘    └────────────┘
```

## Strategy Integration

### 1. Metrics Exposure

The strategy implements `get_operational_metrics()` which returns a comprehensive dict of current operational state:

```python
def get_operational_metrics(self) -> dict:
    """Return current operational metrics for monitoring."""
    with self._ops_lock:
        return {
            # Position metrics
            "long_inventory_usdt": self._calculate_inventory("long"),
            "short_inventory_usdt": self._calculate_inventory("short"),
            "net_inventory_usdt": self._calculate_net_inventory(),

            # Grid metrics
            "active_rungs_long": len(self._get_active_rungs("long")),
            "active_rungs_short": len(self._get_active_rungs("short")),
            "open_orders_count": len(self._get_live_grid_orders()),

            # Risk metrics
            "margin_ratio": self._get_margin_ratio(),
            "maker_ratio": self._calculate_maker_ratio(),

            # Funding metrics
            "funding_rate_current": self._get_current_funding_rate(),
            "funding_cost_1h_projected_usdt": self._project_funding_cost_1h(),

            # PnL metrics
            "realized_pnl_usdt": self._get_realized_pnl(),
            "unrealized_pnl_usdt": self._get_unrealized_pnl(),
            "total_pnl_usdt": self._get_total_pnl(),

            # System health
            "uptime_seconds": self._get_uptime_seconds(),
            "last_bar_timestamp": (
                self._last_bar_time.timestamp() if self._last_bar_time else 0.0
            ),
        }
```

**Metrics Categories:**

1. **Position Metrics**: Inventory values in USDT for long/short/net positions
2. **Grid Metrics**: Active rungs and open order counts
3. **Risk Metrics**: Margin usage and maker fill ratios
4. **Funding Metrics**: Current funding rate and projected costs
5. **PnL Metrics**: Realized, unrealized, and total PnL
6. **System Health**: Uptime and last bar timestamp

### 2. State Tracking

The strategy tracks metrics-relevant state throughout its lifecycle:

**On Strategy Start (`on_start`):**
```python
# Initialize metrics tracking
self._start_time = self.clock.timestamp_ns()
self._last_bar_time = None
self._total_fills = 0
self._maker_fills = 0
```

**On Bar Processing (`on_bar`):**
```python
# Update last bar timestamp
self._last_bar_time = datetime.fromtimestamp(bar.ts_init / 1_000_000_000, tz=UTC)

# Store ladder state for snapshot access
with self._ops_lock:
    for ladder in ladders:
        if ladder.side == Side.LONG:
            self._last_long_ladder = ladder
        elif ladder.side == Side.SHORT:
            self._last_short_ladder = ladder
```

**On Order Fill (`on_order_filled`):**
```python
# Track fill statistics
with self._ops_lock:
    self._total_fills += 1
    if event.liquidity_side == LiquiditySide.MAKER:
        self._maker_fills += 1
```

### 3. Kill Switch Integration

The strategy implements `flatten_side()` for emergency position flattening:

```python
def flatten_side(self, side: str) -> dict:
    """
    Flatten positions for given side (called by kill switch).

    Args:
        side: "long", "short", or "both"

    Returns:
        dict with cancelled orders and closing positions info
    """
    result = {
        "cancelled_orders": 0,
        "closing_positions": [],
    }

    sides = ["long", "short"] if side == "both" else [side]

    with self._ops_lock:
        for s in sides:
            # Cancel orders
            cancelled = self._cancel_side_orders(s)
            result["cancelled_orders"] += cancelled

            # Close position
            position_info = self._close_side_position(s)
            if position_info:
                result["closing_positions"].append(position_info)

    return result
```

**Emergency Flatten Process:**
1. Cancel all open grid orders for the specified side(s)
2. Submit market orders to close open positions
3. Return summary of cancelled orders and closing positions

### 4. API Control Methods

**Throttle Control:**
```python
def set_throttle(self, throttle: float) -> None:
    """
    Adjust strategy aggressiveness (0.0 = passive, 1.0 = aggressive).

    Args:
        throttle: Value between 0.0 and 1.0
    """
    if not 0.0 <= throttle <= 1.0:
        raise ValueError(f"Throttle must be between 0.0 and 1.0, got {throttle}")

    with self._ops_lock:
        self._throttle = throttle

    self.log.info(f"Throttle set to {throttle:.2f}")
```

**Ladder Snapshot:**
```python
def get_ladders_snapshot(self) -> dict:
    """Return current grid ladder state for API."""
    with self._ops_lock:
        if self._last_long_ladder is None and self._last_short_ladder is None:
            return {"long_ladder": [], "short_ladder": [], "mid_price": 0.0}

        return {
            "timestamp": self.clock.timestamp_ns(),
            "mid_price": self._last_mid or 0.0,
            "long_ladder": [
                {"price": r.price, "qty": r.qty, "side": str(r.side)}
                for r in (self._last_long_ladder.rungs if self._last_long_ladder else [])
            ],
            "short_ladder": [
                {"price": r.price, "qty": r.qty, "side": str(r.side)}
                for r in (self._last_short_ladder.rungs if self._last_short_ladder else [])
            ],
        }
```

## Thread Safety

All operational control methods are thread-safe via `_ops_lock`:

```python
# In __init__
self._ops_lock = threading.Lock()  # Thread-safe access to operational metrics

# In methods
with self._ops_lock:
    # Access or modify shared state
    self._throttle = throttle
```

This ensures safe concurrent access from:
- Strategy's main trading loop
- OperationsManager's metrics polling
- FastAPI request handlers
- Kill switch monitoring threads

## Operations Manager

The `OperationsManager` orchestrates all operational infrastructure:

```python
from naut_hedgegrid.ops import OperationsManager

# Initialize with strategy instance
ops_manager = OperationsManager(
    strategy=strategy_instance,
    instrument_id="BTCUSDT-PERP.BINANCE",
    prometheus_port=9090,
    api_port=8080,
)

# Start both Prometheus and FastAPI servers
ops_manager.start()

# Update metrics (automatically fetches from strategy)
ops_manager.update_metrics()

# Shutdown on strategy stop
ops_manager.stop()
```

**Features:**
- Automatic metrics fetching from `strategy.get_operational_metrics()`
- Unified start/stop of Prometheus and FastAPI services
- Strategy callback routing for API operations
- Error handling and resource cleanup

## Runner Integration

The runners (`run_live.py`, `run_paper.py`) support optional operational controls:

```bash
# Run live trading with ops enabled
uv run python -m naut_hedgegrid.runners.run_live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml \
    --enable-ops \
    --prometheus-port 9090 \
    --api-port 8080

# Run paper trading with ops enabled
uv run python -m naut_hedgegrid.runners.run_paper \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml \
    --enable-ops \
    --prometheus-port 9090 \
    --api-port 8080
```

**CLI Options:**
- `--enable-ops`: Enable operational infrastructure (default: False)
- `--prometheus-port`: Prometheus metrics port (default: 9090)
- `--api-port`: FastAPI control port (default: 8080)
- `--api-key`: Optional API key for authentication

**Runner Lifecycle:**

1. **Startup:**
   - Validate environment and load configs
   - Create TradingNode with strategy
   - Start node (triggers `strategy.on_start()`)
   - If `--enable-ops`: Initialize OperationsManager and start services
   - Display status panel with access URLs

2. **Runtime:**
   - Strategy executes trading logic
   - OperationsManager polls metrics periodically
   - FastAPI serves control endpoints
   - Prometheus exports metrics for scraping

3. **Shutdown:**
   - Stop OperationsManager (closes servers)
   - Stop TradingNode (triggers `strategy.on_stop()`, cancels orders)
   - Dispose resources and exit

## Prometheus Metrics

**Endpoint:** `http://localhost:9090/metrics`

**Available Metrics:**

| Metric | Type | Description |
|--------|------|-------------|
| `hedgegrid_long_inventory_usdt` | Gauge | Long position inventory in USDT |
| `hedgegrid_short_inventory_usdt` | Gauge | Short position inventory in USDT |
| `hedgegrid_net_inventory_usdt` | Gauge | Net inventory (long - short) |
| `hedgegrid_active_rungs_long` | Gauge | Number of active long grid rungs |
| `hedgegrid_active_rungs_short` | Gauge | Number of active short grid rungs |
| `hedgegrid_open_orders` | Gauge | Total open orders |
| `hedgegrid_margin_ratio` | Gauge | Current margin ratio (used/available) |
| `hedgegrid_maker_ratio` | Gauge | Ratio of maker fills vs total fills |
| `hedgegrid_funding_rate_current` | Gauge | Current funding rate |
| `hedgegrid_funding_cost_1h_projected_usdt` | Gauge | Projected funding cost for next 1h |
| `hedgegrid_realized_pnl_usdt` | Gauge | Total realized PnL |
| `hedgegrid_unrealized_pnl_usdt` | Gauge | Total unrealized PnL |
| `hedgegrid_total_pnl_usdt` | Gauge | Total PnL (realized + unrealized) |
| `hedgegrid_uptime_seconds` | Gauge | Strategy uptime in seconds |
| `hedgegrid_last_bar_timestamp` | Gauge | Timestamp of last processed bar |

All metrics include `instrument` label for multi-instrument support.

## FastAPI Control Endpoints

**Endpoint:** `http://localhost:8080/docs` (Swagger UI)

**Available Operations:**

1. **GET `/health`** - Basic health check
   ```json
   {
       "running": true,
       "last_bar_timestamp": 1697234567.0
   }
   ```

2. **GET `/status`** - Comprehensive strategy status
   ```json
   {
       "running": true,
       "positions": {
           "long": {"inventory_usdt": 1500.0, "quantity": 0.05, ...},
           "short": {"inventory_usdt": 800.0, "quantity": 0.03, ...}
       },
       "margin_ratio": 0.35,
       "open_orders": 8,
       "pnl": {
           "realized": 234.56,
           "unrealized": 45.12,
           "total": 279.68
       }
   }
   ```

3. **POST `/flatten`** - Emergency position flattening
   ```bash
   curl -X POST "http://localhost:8080/flatten" \
        -H "Content-Type: application/json" \
        -d '{"side": "both"}'
   ```

4. **POST `/throttle`** - Adjust strategy aggressiveness
   ```bash
   curl -X POST "http://localhost:8080/throttle" \
        -H "Content-Type: application/json" \
        -d '{"throttle": 0.5}'
   ```

5. **GET `/ladders`** - Get current grid ladder snapshot
   ```json
   {
       "mid_price": 42000.0,
       "long_ladder": [
           {"price": 41900.0, "qty": 0.01, "side": "LONG"},
           {"price": 41800.0, "qty": 0.01, "side": "LONG"}
       ],
       "short_ladder": [
           {"price": 42100.0, "qty": 0.01, "side": "SHORT"},
           {"price": 42200.0, "qty": 0.01, "side": "SHORT"}
       ]
   }
   ```

6. **GET `/orders`** - Get current open orders
   ```json
   {
       "orders": [
           {
               "client_order_id": "HG1-LONG-01-1697234567",
               "side": "BUY",
               "price": 41900.0,
               "quantity": 0.01,
               "status": "OPEN"
           }
       ]
   }
   ```

## Testing

Run the integration tests:

```bash
uv run pytest tests/test_ops_integration.py -v
```

**Test Coverage:**
- Strategy has all required operational methods
- Thread-safe ops lock exists
- Metrics tracking state initialized correctly
- Ladder state tracking works
- Set throttle validation
- Get ladders snapshot returns proper structure
- Attach kill switch works
- Operational metrics returns complete dict
- Flatten side validation
- OperationsManager initialization
- Update metrics calls strategy method

## Future Enhancements

1. **Kill Switch Implementation**
   - Automatic risk monitoring
   - Configurable trigger conditions
   - Alert notifications on trigger

2. **Enhanced Metrics**
   - Implement actual funding rate subscription
   - Track realized PnL properly
   - Add account balance metrics
   - Implement margin ratio calculation

3. **API Authentication**
   - API key validation
   - JWT token support
   - Role-based access control

4. **Metrics Persistence**
   - Historical metrics storage
   - Time-series database integration
   - Metric aggregation and rollup

5. **Alert System**
   - Prometheus Alertmanager integration
   - Custom alert rules
   - Email/Slack notifications

## Security Considerations

1. **API Access Control**
   - Use `--api-key` for authentication
   - Bind to `localhost` only for local access
   - Use reverse proxy (nginx) with HTTPS for production

2. **Metrics Exposure**
   - Prometheus metrics contain sensitive trading data
   - Restrict access via firewall rules
   - Use Prometheus authentication in production

3. **Kill Switch**
   - Emergency flatten operations cannot be undone
   - Require confirmation for production use
   - Log all emergency operations

## File Paths

All file paths mentioned in this document are relative to the repository root:

```
/Users/giovanni/Library/Mobile Documents/com~apple~CloudDocs/binance_bot/
├── src/naut_hedgegrid/
│   ├── strategies/hedge_grid_v1/
│   │   └── strategy.py              # Strategy with ops integration
│   ├── ops/
│   │   ├── __init__.py               # OperationsManager
│   │   └── prometheus.py             # PrometheusExporter
│   └── runners/
│       ├── base_runner.py            # BaseRunner with ops support
│       ├── run_live.py               # Live runner with ops CLI
│       └── run_paper.py              # Paper runner with ops CLI
├── tests/
│   └── test_ops_integration.py       # Integration tests
└── docs/
    └── operational_controls_integration.md  # This document
```
