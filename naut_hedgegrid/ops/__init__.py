"""Operational controls for risk management and monitoring.

This package provides emergency controls and monitoring systems for live trading:

- **KillSwitch**: Automated circuit breakers with position flattening
- **AlertManager**: Multi-channel alert notifications (Slack, Telegram)
- **Configuration**: Type-safe configuration models for operational controls

"""

from naut_hedgegrid.ops.alerts import AlertManager, AlertSeverity
from naut_hedgegrid.ops.kill_switch import CircuitBreakerTriggered, KillSwitch

__all__ = [
    "AlertManager",
    "AlertSeverity",
    "CircuitBreakerTriggered",
    "KillSwitch",
]
