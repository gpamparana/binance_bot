"""Configuration for HedgeGridV1 strategy."""

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import OmsType


class HedgeGridV1Config(StrategyConfig, frozen=True, kw_only=True):
    """
    Configuration for HedgeGridV1 futures trading strategy.

    This configuration links the Nautilus strategy to the hedge grid
    configuration file and specifies trading parameters.

    Attributes
    ----------
    instrument_id : str
        The instrument to trade (e.g., "BTCUSDT-PERP.BINANCE")
    bar_type : str
        The bar type for regime detection (e.g., "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST")
    hedge_grid_config_path : str
        Path to HedgeGridConfig YAML file containing strategy parameters
    oms_type : OmsType
        Order Management System type (defaults to HEDGING for Binance hedge mode)

    """

    instrument_id: str
    bar_type: str
    hedge_grid_config_path: str
    oms_type: OmsType = OmsType.HEDGING  # Required for Binance hedge mode
