"""User interface components for HedgeGrid trading system.

This module provides UI and API components for interacting with the
HedgeGrid trading system:

- **FastAPI REST API**: Operational control and monitoring endpoints
- **Interactive dashboards**: (Future) Web-based monitoring dashboards

Components:
    - StrategyAPI: REST API for strategy control and monitoring

Example:
    >>> from naut_hedgegrid.ui import StrategyAPI
    >>>
    >>> api = StrategyAPI(strategy_callback=callback_func)
    >>> api.start_server(host="0.0.0.0", port=8080)
"""

from naut_hedgegrid.ui.api import StrategyAPI

__all__ = ["StrategyAPI"]
