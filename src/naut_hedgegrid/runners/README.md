# Runners Module

Trading system execution runners for backtest, paper, and live trading modes.

## Quick Reference

### Backtest
```bash
uv run python -m naut_hedgegrid backtest \
  --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \
  --strategy-config configs/strategies/hedge_grid_v1.yaml
```

### Paper Trading
```bash
uv run python -m naut_hedgegrid paper \
  --strategy-config configs/strategies/hedge_grid_v1.yaml \
  --venue-config configs/venues/binance_futures.yaml \
  --enable-ops
```

### Live Trading
```bash
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret

uv run python -m naut_hedgegrid live \
  --strategy-config configs/strategies/hedge_grid_v1.yaml \
  --venue-config configs/venues/binance_futures.yaml \
  --enable-ops
```

## Components

- `run_backtest.py` - Historical simulation with BacktestEngine
- `run_paper.py` - Simulated execution with live data
- `run_live.py` - Real execution with real money (USE WITH CAUTION)
- `base_runner.py` - Shared runner infrastructure

## Documentation

For comprehensive documentation see:
- [Main README](../../../README.md#trading-modes) - Trading modes overview
- [Docker README](../../../docker/README.md) - Containerized deployment
- [CLAUDE.md](../../../CLAUDE.md#build-commands) - Developer guide

## Integration

All runners integrate with:
- TradingNode lifecycle (build, run, stop, dispose)
- Operational controls (--enable-ops flag)
- Prometheus metrics export (--prometheus-port)
- FastAPI control API (--api-port)

## Testing

See [tests/README.md](../../../tests/README.md) for test suite documentation.
