# Operational Controls Module

Production-grade monitoring and control infrastructure for live trading systems.

## Key Components

- **Kill Switch**: Automated circuit breakers with position flattening
- **Alert System**: Multi-channel notifications (Slack, Telegram)
- **Metrics Export**: Prometheus integration for monitoring dashboards
- **API Control**: FastAPI endpoints for operational commands

## Quick Start

Enable operational controls with any runner:

```bash
# Paper trading with ops
uv run python -m naut_hedgegrid paper \
  --enable-ops \
  --prometheus-port 9090 \
  --api-port 8080

# Live trading with ops
uv run python -m naut_hedgegrid live \
  --enable-ops \
  --prometheus-port 9091 \
  --api-port 8081 \
  --api-key "$(openssl rand -hex 32)"
```

## Key Metrics (15 total)

- **Position**: long_inventory_usdt, short_inventory_usdt, net_inventory_usdt
- **Grid**: active_rungs_long, active_rungs_short, open_orders
- **Risk**: margin_ratio, maker_ratio
- **Funding**: funding_rate_current, funding_cost_1h_projected_usdt
- **PnL**: realized_pnl_usdt, unrealized_pnl_usdt, total_pnl_usdt
- **Health**: uptime_seconds, last_bar_timestamp

## API Endpoints (8 total)

- `GET /health` - Health check
- `GET /status` - Comprehensive status
- `POST /flatten` - Emergency position closure
- `POST /set-throttle` - Adjust aggressiveness
- `GET /ladders` - Grid ladder snapshot
- `GET /orders` - Open orders list
- `POST /start` - Start trading (stub)
- `POST /stop` - Stop trading (stub)

## Documentation

For comprehensive documentation see:
- [OPERATIONS.md](../../../docs/OPERATIONS.md) - Full operational guide
- [KILL_SWITCH_GUIDE.md](../../../docs/KILL_SWITCH_GUIDE.md) - Kill switch details
- [Main README](../../../README.md#trading-modes) - System overview

## Testing

72 tests validate operational controls:
- 27 kill switch tests
- 25 alert system tests
- 20 prometheus tests

See [tests/README.md](../../../tests/README.md#operational-controls-tests) for details.

## Thread Safety

All operations are thread-safe for concurrent access from:
- Strategy's main trading loop
- OperationsManager's metrics polling
- FastAPI request handlers
- Kill switch monitoring threads
