"""Strategy configuration models."""

from pydantic import BaseModel, Field

from naut_hedgegrid.config.base import BaseYamlConfigLoader


class StrategyDetails(BaseModel):
    """Basic strategy identification."""

    name: str = Field(description="Strategy name")
    instrument_id: str = Field(description="Instrument ID (e.g., BTCUSDT-PERP.BINANCE)")


class GridConfig(BaseModel):
    """Grid trading parameters."""

    grid_step_bps: float = Field(
        gt=0,
        le=1000,
        description="Grid spacing in basis points (e.g., 25.0 = 0.25%)",
    )
    grid_levels_long: int = Field(
        ge=1, le=100, description="Number of long grid levels below mid price"
    )
    grid_levels_short: int = Field(
        ge=1, le=100, description="Number of short grid levels above mid price"
    )
    base_qty: float = Field(gt=0, description="Base order quantity in base currency (e.g., BTC)")
    qty_scale: float = Field(
        gt=0,
        le=10,
        description="Quantity multiplier per level (geometric scaling)",
    )


class ExitConfig(BaseModel):
    """Take profit and stop loss parameters."""

    tp_steps: int = Field(ge=1, le=100, description="Take profit after N grid steps")
    sl_steps: int = Field(ge=1, le=100, description="Stop loss after N grid steps")


class RebalanceConfig(BaseModel):
    """Grid re-centering and inventory limits."""

    recenter_trigger_bps: float = Field(
        gt=0,
        le=10000,
        description="Re-center grid if price moves N bps from mid",
    )
    max_inventory_quote: float = Field(
        gt=0, description="Maximum inventory in quote currency (e.g., USDT)"
    )


class ExecutionConfig(BaseModel):
    """Order execution parameters."""

    maker_only: bool = Field(default=True, description="Only use maker orders (no taker)")
    use_post_only_retries: bool = Field(
        default=True, description="Retry with POST_ONLY flag on rejection"
    )
    retry_attempts: int = Field(
        default=3, ge=0, le=10, description="Number of retry attempts for failed orders"
    )
    retry_delay_ms: int = Field(
        default=100,
        ge=0,
        le=5000,
        description="Delay between retries in milliseconds",
    )


class FundingConfig(BaseModel):
    """Funding rate filtering parameters."""

    funding_window_minutes: int = Field(
        ge=60,
        le=1440,
        description="Time window for funding rate evaluation (e.g., 480 = 8 hours)",
    )
    funding_max_cost_bps: float = Field(
        ge=0,
        le=100,
        description="Max acceptable funding cost in bps per window",
    )


class RegimeConfig(BaseModel):
    """Market regime detection parameters."""

    adx_len: int = Field(ge=5, le=100, description="ADX period for trend strength detection")
    ema_fast: int = Field(ge=5, le=200, description="Fast EMA period")
    ema_slow: int = Field(ge=10, le=500, description="Slow EMA period")
    atr_len: int = Field(ge=5, le=100, description="ATR period for volatility")
    hysteresis_bps: float = Field(
        ge=0,
        le=1000,
        description="Hysteresis band for regime switching in bps",
    )


class PositionConfig(BaseModel):
    """Position sizing and leverage limits."""

    max_position_size: float = Field(gt=0, description="Maximum position size in base currency")
    max_leverage_used: float = Field(gt=0, le=125, description="Maximum effective leverage to use")
    emergency_liquidation_buffer: float = Field(
        ge=0,
        le=1,
        description="Safety buffer from liquidation price (0.15 = 15%)",
    )


class HedgeGridConfig(BaseModel):
    """
    Complete hedge grid strategy configuration.

    This model contains all parameters needed to run the hedge grid
    trading strategy, including grid setup, exits, funding filters,
    regime detection, and position sizing.
    """

    strategy: StrategyDetails = Field(description="Strategy identification")
    grid: GridConfig = Field(description="Grid parameters")
    exit: ExitConfig = Field(description="Take profit / stop loss")
    rebalance: RebalanceConfig = Field(description="Grid re-centering")
    execution: ExecutionConfig = Field(description="Order execution")
    funding: FundingConfig = Field(description="Funding rate filter")
    regime: RegimeConfig = Field(description="Regime detection")
    position: PositionConfig = Field(description="Position sizing")


class HedgeGridConfigLoader(BaseYamlConfigLoader):
    """Loader for hedge grid strategy configurations."""

    model_class = HedgeGridConfig
