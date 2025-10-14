# Operational Infrastructure Guide

Production-grade monitoring and control infrastructure for the naut-hedgegrid trading system.

## Overview

The operational infrastructure provides real-time monitoring and control capabilities for live trading systems:

- **Prometheus Metrics**: Comprehensive metrics export for Grafana dashboards and alerting
- **FastAPI Control Endpoints**: REST API for operational control and status queries
- **Thread-Safe Operation**: All components designed for concurrent access from strategy callbacks

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Trading System                          │
│  ┌──────────────┐      ┌─────────────────────────────┐    │
│  │              │      │   HedgeGridV1 Strategy       │    │
│  │  TradingNode │─────▶│  - Grid management           │    │
│  │              │      │  - Order execution           │    │
│  └──────────────┘      │  - Position tracking         │    │
│                        └──────────┬──────────────────┘    │
│                                   │                         │
│                        ┌──────────▼──────────────────┐    │
│                        │  OperationsManager           │    │
│                        │  - Metrics coordination      │    │
│                        │  - API callback handling     │    │
│                        └──────┬───────────┬──────────┘    │
│                               │           │                 │
│                    ┌──────────▼──┐    ┌──▼──────────────┐ │
│                    │ Prometheus  │    │  FastAPI         │ │
│                    │ Exporter    │    │  Control API     │ │
│                    │ :9090       │    │  :8080           │ │
│                    └──────┬──────┘    └──┬───────────────┘ │
└───────────────────────────┼──────────────┼──────────────────┘
                            │              │
                    ┌───────▼──────┐  ┌───▼────────┐
                    │  Prometheus  │  │  Operator  │
                    │  Server      │  │  Dashboard │
                    └──────────────┘  └────────────┘
```

## Quick Start

### 1. Enable Operations Infrastructure

Add `--enable-ops` flag when running live or paper trading:

```bash
# Live trading with ops enabled
uv run python -m naut_hedgegrid.runners.run_live \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml \
    --enable-ops \
    --prometheus-port 9090 \
    --api-port 8080

# Paper trading with ops enabled
uv run python -m naut_hedgegrid.runners.run_paper \
    --strategy-config configs/strategies/hedge_grid_v1.yaml \
    --venue-config configs/venues/binance_futures.yaml \
    --enable-ops
```

### 2. Access Endpoints

Once started, you can access:

- **Prometheus metrics**: http://localhost:9090/metrics
- **FastAPI documentation**: http://localhost:8080/docs
- **Health check**: http://localhost:8080/health

## Prometheus Metrics

### Available Metrics

The system exports 15 comprehensive metrics:

#### Position Metrics
- `hedgegrid_long_inventory_usdt` - Long position inventory in USDT
- `hedgegrid_short_inventory_usdt` - Short position inventory in USDT
- `hedgegrid_net_inventory_usdt` - Net inventory (long - short) in USDT

#### Grid Metrics
- `hedgegrid_active_rungs_long` - Number of active long grid rungs
- `hedgegrid_active_rungs_short` - Number of active short grid rungs
- `hedgegrid_open_orders` - Total open orders

#### Risk Metrics
- `hedgegrid_margin_ratio` - Current margin ratio (used / available)
- `hedgegrid_maker_ratio` - Ratio of maker fills vs total fills

#### Funding Metrics
- `hedgegrid_funding_rate_current` - Current funding rate
- `hedgegrid_funding_cost_1h_projected_usdt` - Projected funding cost for next 1h in USDT

#### PnL Metrics
- `hedgegrid_realized_pnl_usdt` - Total realized PnL in USDT
- `hedgegrid_unrealized_pnl_usdt` - Total unrealized PnL in USDT
- `hedgegrid_total_pnl_usdt` - Total PnL (realized + unrealized) in USDT

#### System Health Metrics
- `hedgegrid_uptime_seconds` - Strategy uptime in seconds
- `hedgegrid_last_bar_timestamp` - Timestamp of last processed bar

### Prometheus Configuration

Add this job to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'hedgegrid'
    scrape_interval: 10s
    static_configs:
      - targets: ['localhost:9090']
        labels:
          strategy: 'hedge_grid_v1'
          environment: 'production'
```

### Example Prometheus Queries

```promql
# Current net inventory in USDT
hedgegrid_net_inventory_usdt{instrument="BTCUSDT-PERP.BINANCE"}

# Rate of PnL change (profit velocity)
rate(hedgegrid_total_pnl_usdt[5m])

# Maker fill ratio (should be > 0.95 for good execution)
hedgegrid_maker_ratio{instrument="BTCUSDT-PERP.BINANCE"}

# Alert if strategy stops receiving bars
(time() - hedgegrid_last_bar_timestamp) > 120
```

## FastAPI Control Endpoints

### Authentication

Optional API key authentication via `X-API-Key` header:

```bash
# Start with API key authentication
uv run python -m naut_hedgegrid.runners.run_live \
    --enable-ops \
    --api-key "your_secret_key_here"

# Make authenticated requests
curl -H "X-API-Key: your_secret_key_here" http://localhost:8080/status
```

Note: The `/health` endpoint is always accessible without authentication for monitoring systems.

### Available Endpoints

#### GET /health

Health check endpoint (no authentication required).

```bash
curl http://localhost:8080/health
```

Response:
```json
{
  "status": "healthy",
  "uptime": 3600.5,
  "last_bar": 1697234567.0
}
```

#### GET /status

Comprehensive strategy status.

```bash
curl http://localhost:8080/status
```

Response:
```json
{
  "running": true,
  "positions": {
    "long": {
      "inventory_usdt": 1500.0,
      "quantity": 0.05,
      "entry_price": 30000.0,
      "unrealized_pnl": 50.0
    },
    "short": {
      "inventory_usdt": 800.0,
      "quantity": 0.03,
      "entry_price": 29000.0,
      "unrealized_pnl": -20.0
    }
  },
  "margin_ratio": 0.35,
  "open_orders": 8,
  "pnl": {
    "realized": 200.0,
    "unrealized": 30.0,
    "total": 230.0
  },
  "timestamp": 1697234567.0
}
```

#### POST /start

Start strategy trading (not currently implemented - use runner lifecycle).

```bash
curl -X POST http://localhost:8080/start
```

#### POST /stop

Stop strategy trading (not currently implemented - use runner lifecycle).

```bash
curl -X POST http://localhost:8080/stop
```

#### POST /flatten

Emergency flatten - cancel all orders and close positions.

```bash
# Flatten both sides
curl -X POST http://localhost:8080/flatten \
  -H "Content-Type: application/json" \
  -d '{"side": "both"}'

# Flatten only long side
curl -X POST http://localhost:8080/flatten \
  -H "Content-Type: application/json" \
  -d '{"side": "long"}'
```

Response:
```json
{
  "status": "flattening",
  "cancelled_orders": 5,
  "closing_positions": ["LONG", "SHORT"],
  "timestamp": 1697234567.0
}
```

#### POST /set-throttle

Adjust strategy aggressiveness.

```bash
curl -X POST http://localhost:8080/set-throttle \
  -H "Content-Type: application/json" \
  -d '{"throttle": 0.75}'
```

Response:
```json
{
  "status": "updated",
  "new_throttle": 0.75,
  "timestamp": 1697234567.0
}
```

Throttle values:
- `0.0` - Maximum passive (fewer orders, wider spreads)
- `1.0` - Maximum aggressive (more orders, tighter spreads)

#### GET /ladders

Get current grid ladder snapshot.

```bash
curl http://localhost:8080/ladders
```

Response:
```json
{
  "timestamp": 1697234567.0,
  "mid_price": 30000.0,
  "long_ladder": [
    {"price": 29900.0, "qty": 0.1, "rung": 0},
    {"price": 29800.0, "qty": 0.1, "rung": 1}
  ],
  "short_ladder": [
    {"price": 30100.0, "qty": 0.1, "rung": 0},
    {"price": 30200.0, "qty": 0.1, "rung": 1}
  ]
}
```

#### GET /orders

Get current open orders.

```bash
curl http://localhost:8080/orders
```

Response:
```json
{
  "orders": [
    {
      "client_order_id": "HG1-LONG-00-123456789",
      "side": "BUY",
      "price": 29900.0,
      "quantity": 0.1,
      "status": "OPEN"
    }
  ],
  "count": 1,
  "timestamp": 1697234567.0
}
```

## Grafana Dashboards

### Sample Dashboard Panels

#### 1. Position Inventory

```json
{
  "title": "Position Inventory (USDT)",
  "targets": [
    {
      "expr": "hedgegrid_long_inventory_usdt",
      "legendFormat": "Long"
    },
    {
      "expr": "hedgegrid_short_inventory_usdt",
      "legendFormat": "Short"
    },
    {
      "expr": "hedgegrid_net_inventory_usdt",
      "legendFormat": "Net"
    }
  ],
  "type": "graph"
}
```

#### 2. PnL Tracking

```json
{
  "title": "Profit & Loss (USDT)",
  "targets": [
    {
      "expr": "hedgegrid_total_pnl_usdt",
      "legendFormat": "Total PnL"
    }
  ],
  "type": "graph"
}
```

#### 3. Grid Activity

```json
{
  "title": "Active Grid Rungs",
  "targets": [
    {
      "expr": "hedgegrid_active_rungs_long",
      "legendFormat": "Long Rungs"
    },
    {
      "expr": "hedgegrid_active_rungs_short",
      "legendFormat": "Short Rungs"
    }
  ],
  "type": "graph"
}
```

## Alerting

### Recommended Alerts

#### 1. Strategy Down Alert

```yaml
- alert: StrategyDown
  expr: (time() - hedgegrid_last_bar_timestamp) > 300
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Strategy {{ $labels.instrument }} stopped receiving bars"
    description: "No bars processed in {{ $value }}s"
```

#### 2. High Margin Usage

```yaml
- alert: HighMarginUsage
  expr: hedgegrid_margin_ratio > 0.8
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High margin usage: {{ $value }}"
    description: "Margin ratio above 80% for 5 minutes"
```

#### 3. Poor Execution Quality

```yaml
- alert: LowMakerRatio
  expr: hedgegrid_maker_ratio < 0.9
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: "Low maker fill ratio: {{ $value }}"
    description: "Maker ratio below 90% - check spread configuration"
```

#### 4. Large Unrealized Loss

```yaml
- alert: LargeUnrealizedLoss
  expr: hedgegrid_unrealized_pnl_usdt < -500
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Large unrealized loss: {{ $value }} USDT"
    description: "Unrealized PnL below -500 USDT"
```

## Operational Runbooks

### Emergency Procedures

#### Flatten All Positions

```bash
# Emergency stop - cancel orders and close positions
curl -X POST http://localhost:8080/flatten \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_key" \
  -d '{"side": "both"}'
```

#### Reduce Aggressiveness

```bash
# Scale back to 50% aggressiveness
curl -X POST http://localhost:8080/set-throttle \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_key" \
  -d '{"throttle": 0.5}'
```

### Health Check Script

```bash
#!/bin/bash
# health_check.sh - Monitor strategy health

HEALTH_URL="http://localhost:8080/health"
MAX_BAR_AGE=120  # 2 minutes

response=$(curl -s "$HEALTH_URL")
status=$(echo "$response" | jq -r '.status')
last_bar=$(echo "$response" | jq -r '.last_bar')

if [ "$status" != "healthy" ]; then
    echo "CRITICAL: Strategy status is $status"
    exit 2
fi

if [ "$last_bar" != "null" ]; then
    age=$(($(date +%s) - ${last_bar%.*}))
    if [ "$age" -gt "$MAX_BAR_AGE" ]; then
        echo "CRITICAL: No bars in ${age}s (max: ${MAX_BAR_AGE}s)"
        exit 2
    fi
fi

echo "OK: Strategy healthy, last bar ${age}s ago"
exit 0
```

## Troubleshooting

### Port Already in Use

```bash
# Check what's using the port
lsof -i :9090
lsof -i :8080

# Use different ports
uv run python -m naut_hedgegrid.runners.run_live \
    --enable-ops \
    --prometheus-port 9091 \
    --api-port 8081
```

### Metrics Not Updating

Check that the strategy implements `get_operational_metrics()`:

```python
# In HedgeGridV1 strategy
def get_operational_metrics(self) -> dict:
    """Return current operational metrics."""
    return {
        "long_inventory_usdt": self._calculate_inventory("long"),
        "short_inventory_usdt": self._calculate_inventory("short"),
        # ... other metrics
    }
```

### API Endpoint Returns 500

Check strategy logs for errors in callback execution:

```bash
# Check logs for API callback errors
tail -f logs/trading.log | grep "Strategy callback error"
```

### Authentication Failures

Verify API key is set correctly:

```bash
# Start with explicit API key
uv run python -m naut_hedgegrid.runners.run_live \
    --enable-ops \
    --api-key "correct_key_here"

# Use key in requests
curl -H "X-API-Key: correct_key_here" http://localhost:8080/status
```

## Performance Considerations

### Metrics Update Frequency

Metrics are updated after each bar is processed. For 1-minute bars, this means:
- Update frequency: ~60 seconds
- Prometheus scrape interval: 10-15 seconds (recommended)
- No significant CPU overhead

### API Latency

All API endpoints are designed for sub-10ms latency:
- Health check: < 1ms
- Status query: < 5ms (depends on cache access)
- Control operations: < 10ms

### Thread Safety

All operations are thread-safe via:
- `threading.Lock` for strategy state access
- Nautilus cache queries (inherently thread-safe)
- Atomic metric updates in Prometheus client

## Security Best Practices

1. **Use API Key Authentication** in production:
   ```bash
   --api-key "$(openssl rand -hex 32)"
   ```

2. **Restrict Network Access**:
   ```bash
   # Bind to localhost only for local access
   # Or use firewall rules to restrict access
   ```

3. **Monitor Access Logs**:
   ```bash
   # Check FastAPI access logs
   tail -f logs/api_access.log
   ```

4. **Rotate API Keys Regularly**:
   ```bash
   # Generate new key monthly
   NEW_KEY=$(openssl rand -hex 32)
   # Update in deployment configuration
   ```

## Integration Examples

### Python Client

```python
import requests

class HedgeGridClient:
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url
        self.headers = {}
        if api_key:
            self.headers["X-API-Key"] = api_key

    def get_status(self) -> dict:
        response = requests.get(
            f"{self.base_url}/status",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def flatten(self, side: str = "both") -> dict:
        response = requests.post(
            f"{self.base_url}/flatten",
            json={"side": side},
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

# Usage
client = HedgeGridClient("http://localhost:8080", api_key="secret")
status = client.get_status()
print(f"Total PnL: {status['pnl']['total']} USDT")
```

### Monitoring Dashboard

See `examples/grafana/hedgegrid_dashboard.json` for a complete Grafana dashboard template.

## Support

For issues or questions:
- Check logs in `logs/` directory
- Review Prometheus metrics for anomalies
- Consult strategy implementation in `src/naut_hedgegrid/strategies/`
- File issues on GitHub repository
