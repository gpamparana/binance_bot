"""Configuration for HedgeGridV1 strategy."""

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import OmsType


class HedgeGridV1Config(StrategyConfig, frozen=True, kw_only=True):
    """
    Configuration for HedgeGridV1 futures trading strategy.

    This configuration provides Nautilus with basic strategy parameters.
    Detailed strategy parameters are loaded from the hedge_grid_config_path YAML file.

    Attributes
    ----------
    instrument_id : str
        The instrument to trade (e.g., "BTCUSDT-PERP.BINANCE")
    hedge_grid_config_path : str
        Path to HedgeGridConfig YAML file containing strategy parameters
    oms_type : OmsType
        Order Management System type (defaults to HEDGING for Binance hedge mode)

    Notes
    -----
    bar_type is not included in config - it's constructed programmatically
    in the strategy to avoid Nautilus 1.220.0 BarType parsing issues with PERP instruments.

    """

    # Strategy-specific parameters
    instrument_id: str
    hedge_grid_config_path: str
    oms_type: OmsType = OmsType.HEDGING  # Required for Binance hedge mode
