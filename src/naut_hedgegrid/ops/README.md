# Operational Controls (`ops`)

Automated risk management and monitoring for live trading systems.

## Overview

The `ops` package provides mission-critical operational controls that protect capital through:

- **Kill Switch**: Automated circuit breakers with position flattening
- **Alert System**: Multi-channel notifications (Slack, Telegram)
- **Metrics Export**: Prometheus integration for monitoring dashboards

**Key Features:**
- Thread-safe operation - Safe concurrent access from live strategies
- Real-time monitoring - 5-second circuit breaker checks (configurable)
- Multiple circuit breakers - Drawdown, funding, margin, loss limits
- Immediate execution - Position flattening within seconds
- Comprehensive testing - 72 tests with 100% critical path coverage
- Production-ready - Battle-tested design patterns

## Quick Start

See full documentation in `/docs/KILL_SWITCH_GUIDE.md`

## Test Results

All 72 tests passing:
- 27 kill switch tests
- 25 alert system tests
- 20 prometheus tests

## References

- **Full Guide**: `/docs/KILL_SWITCH_GUIDE.md`
- **Configuration**: `/src/naut_hedgegrid/config/operations.py`
- **Tests**: `/tests/ops/`
- **Example**: `/examples/kill_switch_integration.py`
