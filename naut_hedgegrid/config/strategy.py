"""Strategy configuration models."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    grid_levels_long: int = Field(ge=1, le=100, description="Number of long grid levels below mid price")
    grid_levels_short: int = Field(ge=1, le=100, description="Number of short grid levels above mid price")
    base_qty: float = Field(gt=0, description="Base order quantity in base currency (e.g., BTC)")
    qty_scale: float = Field(
        gt=0,
        le=10,
        description="Quantity multiplier per level (geometric scaling)",
    )

    @field_validator("grid_step_bps")
    @classmethod
    def validate_grid_step(cls, v: float) -> float:
        """Ensure grid step is reasonable (not too small to avoid excessive orders)."""
        if v < 5.0:  # Less than 0.05%
            raise ValueError(f"Grid step {v} bps is too small, minimum is 5 bps (0.05%)")
        return v

    @field_validator("qty_scale")
    @classmethod
    def validate_qty_scale(cls, v: float) -> float:
        """Ensure qty_scale doesn't lead to extreme position sizes."""
        if v > 3.0:
            raise ValueError(f"Quantity scale {v} is too aggressive, maximum is 3.0")
        return v


class ExitConfig(BaseModel):
    """Take profit and stop loss parameters."""

    tp_steps: int = Field(ge=1, le=100, description="Take profit after N grid steps")
    sl_steps: int = Field(ge=1, le=100, description="Stop loss after N grid steps")

    @model_validator(mode="after")
    def validate_tp_sl_relationship(self) -> "ExitConfig":
        """Ensure TP and SL make sense relative to each other."""
        if self.tp_steps > self.sl_steps * 3:
            raise ValueError(
                f"TP steps ({self.tp_steps}) shouldn't be more than 3x SL steps ({self.sl_steps}) "
                "to maintain reasonable risk/reward"
            )
        return self


class RebalanceConfig(BaseModel):
    """Grid re-centering and inventory limits."""

    recenter_trigger_bps: float = Field(
        gt=0,
        le=10000,
        description="Re-center grid if price moves N bps from mid",
    )
    max_inventory_quote: float = Field(gt=0, description="Maximum inventory in quote currency (e.g., USDT)")


class ExecutionConfig(BaseModel):
    """Order execution parameters."""

    maker_only: bool = Field(default=True, description="Only use maker orders (no taker)")
    use_post_only_retries: bool = Field(default=True, description="Retry with POST_ONLY flag on rejection")
    retry_attempts: int = Field(default=3, ge=0, le=10, description="Number of retry attempts for failed orders")
    retry_delay_ms: int = Field(
        default=100,
        ge=0,
        le=5000,
        description="Delay between retries in milliseconds",
    )
    optimization_mode: bool = Field(
        default=False,
        description="Run in optimization mode with reduced logging and no retries",
    )
    retry_max_price_deviation_bps: float = Field(
        default=100,
        ge=0,
        le=1000,
        description="Maximum price deviation from market to attempt retries (100 = 1%)",
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

    @model_validator(mode="after")
    def validate_ema_relationship(self) -> "RegimeConfig":
        """Ensure fast EMA is actually faster than slow EMA."""
        if self.ema_fast >= self.ema_slow:
            raise ValueError(f"Fast EMA period ({self.ema_fast}) must be less than slow EMA period ({self.ema_slow})")
        return self


class PositionConfig(BaseModel):
    """Position sizing and leverage limits."""

    max_position_size: float = Field(gt=0, description="Maximum position size in base currency")
    max_leverage_used: float = Field(gt=0, le=125, description="Maximum effective leverage to use")
    emergency_liquidation_buffer: float = Field(
        ge=0,
        le=1,
        description="Safety buffer from liquidation price (0.15 = 15%)",
    )
    max_position_pct: float = Field(
        default=0.95,
        ge=0.1,
        le=1.0,
        description="Maximum position as percentage of available balance (0.95 = 95%)",
    )

    @field_validator("max_leverage_used")
    @classmethod
    def validate_leverage(cls, v: float) -> float:
        """Warn about high leverage usage."""
        if v > 20:
            # Note: This is a warning, not an error. High leverage is risky but allowed.
            pass
        return v

    @field_validator("emergency_liquidation_buffer")
    @classmethod
    def validate_buffer(cls, v: float) -> float:
        """Ensure liquidation buffer is reasonable."""
        if v < 0.05:  # Less than 5%
            raise ValueError(f"Emergency liquidation buffer {v:.1%} is too small, minimum is 5%")
        return v


class PolicyConfig(BaseModel):
    """Placement policy for inventory biasing by regime."""

    strategy: Literal["core-and-scalp", "throttled-counter"] = Field(
        description=("Placement strategy: core-and-scalp (thin on both) or throttled-counter (full with trend)")
    )
    counter_levels: int = Field(
        ge=0,
        le=20,
        description="Number of levels to maintain on counter-trend side (0 = disable counter-side)",
    )
    counter_qty_scale: float = Field(
        ge=0.0,
        le=1.0,
        description="Quantity scaling factor for counter-trend side (0.0-1.0, 1.0 = no reduction)",
    )


class RiskManagementConfig(BaseModel):
    """Advanced risk management and circuit breaker configuration."""

    max_errors_per_minute: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum errors allowed per minute before circuit breaker activates",
    )
    circuit_breaker_cooldown_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Cooldown period in seconds after circuit breaker activation",
    )
    max_drawdown_pct: float = Field(
        default=20.0,
        ge=1.0,
        le=50.0,
        description="Maximum drawdown percentage before emergency position flattening",
    )
    enable_position_validation: bool = Field(
        default=True,
        description="Validate order size against account balance before submission",
    )
    enable_circuit_breaker: bool = Field(
        default=True,
        description="Enable circuit breaker for error rate monitoring",
    )
    enable_drawdown_protection: bool = Field(
        default=True,
        description="Enable automatic position flattening on max drawdown",
    )


class HedgeGridConfig(BaseModel):
    """
    Complete hedge grid strategy configuration.

    This model contains all parameters needed to run the hedge grid
    trading strategy, including grid setup, exits, funding filters,
    regime detection, position sizing, and comprehensive risk management.
    """

    strategy: StrategyDetails = Field(description="Strategy identification")
    grid: GridConfig = Field(description="Grid parameters")
    exit: ExitConfig = Field(description="Take profit / stop loss")
    rebalance: RebalanceConfig = Field(description="Grid re-centering")
    execution: ExecutionConfig = Field(description="Order execution")
    funding: FundingConfig = Field(description="Funding rate filter")
    regime: RegimeConfig = Field(description="Regime detection")
    position: PositionConfig = Field(description="Position sizing")
    policy: PolicyConfig = Field(description="Placement policy for inventory biasing")
    risk_management: RiskManagementConfig | None = Field(
        default_factory=RiskManagementConfig,
        description="Advanced risk management and circuit breaker settings",
    )


class HedgeGridConfigLoader(BaseYamlConfigLoader):
    """Loader for hedge grid strategy configurations."""

    model_class = HedgeGridConfig
