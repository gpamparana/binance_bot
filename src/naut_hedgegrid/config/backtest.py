"""Backtest configuration models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from naut_hedgegrid.config.base import BaseYamlConfigLoader


class BacktestDetails(BaseModel):
    """Backtest identification."""

    name: str = Field(description="Backtest name")
    description: str = Field(default="", description="Backtest description")


class TimeRangeConfig(BaseModel):
    """Backtest time range."""

    start_time: datetime = Field(description="Backtest start time (ISO format)")
    end_time: datetime = Field(description="Backtest end time (ISO format)")
    timezone: str = Field(default="UTC", description="Timezone for timestamps")


class DataTypeConfig(BaseModel):
    """Data type configuration."""

    type: Literal[
        "OrderBookDelta",
        "TradeTick",
        "QuoteTick",
        "MarkPrice",
        "FundingRate",
        "Bar",
    ] = Field(description="Data type name")
    depth: int | None = Field(
        default=None, ge=1, le=100, description="Order book depth (if applicable)"
    )


class InstrumentDataConfig(BaseModel):
    """Instrument data configuration."""

    instrument_id: str = Field(description="Instrument ID")
    data_types: list[DataTypeConfig] = Field(description="Data types to load for this instrument")


class DataSourceConfig(BaseModel):
    """Data source configuration."""

    type: Literal["parquet", "csv", "feather", "arrow"] = Field(description="Data source type")
    path: str = Field(description="Path to data files")
    glob_pattern: str = Field(default="*.parquet", description="File glob pattern")


class BalanceConfig(BaseModel):
    """Starting balance configuration."""

    currency: str = Field(description="Currency code (e.g., USDT, BTC)")
    total: float = Field(gt=0, description="Total balance")
    locked: float = Field(default=0.0, ge=0, description="Locked balance")


class VenueBacktestConfig(BaseModel):
    """Venue configuration for backtest."""

    config_path: str = Field(description="Path to venue config YAML")
    starting_balances: list[BalanceConfig] = Field(description="Starting balances for this venue")


class StrategyBacktestConfig(BaseModel):
    """Strategy configuration for backtest."""

    config_path: str = Field(description="Path to strategy config YAML")
    enabled: bool = Field(default=True, description="Whether strategy is enabled")


class LatencyConfig(BaseModel):
    """Latency modeling configuration."""

    order_submit_ms: int = Field(default=50, ge=0, description="Order submission latency")
    order_cancel_ms: int = Field(default=30, ge=0, description="Order cancel latency")
    fill_mean_ms: int = Field(default=100, ge=0, description="Mean fill latency")
    fill_std_ms: int = Field(default=20, ge=0, description="Fill latency standard deviation")


class FillModelConfig(BaseModel):
    """Fill simulation configuration."""

    type: Literal["naive", "realistic", "probabilistic"] = Field(
        default="realistic", description="Fill model type"
    )
    maker_fill_prob: float = Field(
        default=0.9,
        ge=0,
        le=1,
        description="Probability maker orders fill",
    )
    aggressive_fill_prob: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Probability aggressive orders fill",
    )
    slippage_bps: float = Field(default=1.0, ge=0, le=100, description="Expected slippage in bps")


class FeeConfig(BaseModel):
    """Fee configuration."""

    maker_bps: float = Field(default=2.0, ge=0, le=100, description="Maker fee in bps")
    taker_bps: float = Field(default=5.0, ge=0, le=100, description="Taker fee in bps")
    funding_apply: bool = Field(default=True, description="Whether to apply funding rate costs")


class ExecutionSimConfig(BaseModel):
    """Execution simulation configuration."""

    latency: LatencyConfig = Field(description="Latency modeling")
    fill_model: FillModelConfig = Field(description="Fill simulation")
    fees: FeeConfig = Field(description="Fee configuration")


class RiskControlConfig(BaseModel):
    """Risk control configuration."""

    max_drawdown_pct: float = Field(
        default=20.0, gt=0, le=100, description="Max drawdown percentage"
    )
    max_daily_loss_pct: float = Field(
        default=5.0, gt=0, le=100, description="Max daily loss percentage"
    )
    stop_on_liquidation: bool = Field(default=True, description="Stop backtest on liquidation")


class OutputConfig(BaseModel):
    """Output configuration."""

    report_dir: str = Field(default="./reports", description="Report output directory")
    save_trades: bool = Field(default=True, description="Save trade records")
    save_positions: bool = Field(default=True, description="Save position records")
    save_account_state: bool = Field(default=True, description="Save account state snapshots")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", description="Logging level"
    )


class MetricsConfig(BaseModel):
    """Performance metrics configuration."""

    calculate: list[str] = Field(
        default=[
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "calmar_ratio",
            "win_rate",
            "profit_factor",
            "avg_trade_pnl",
            "total_trades",
            "funding_pnl",
        ],
        description="Metrics to calculate",
    )
    risk_free_rate: float = Field(default=0.04, ge=0, le=1, description="Annual risk-free rate")
    periods_per_year: int = Field(
        default=365, ge=1, description="Periods per year for annualization"
    )


class DataConfig(BaseModel):
    """Data loading configuration."""

    catalog_path: str = Field(description="Path to data catalog")
    instruments: list[InstrumentDataConfig] = Field(
        description="Instruments and data types to load"
    )
    sources: list[DataSourceConfig] = Field(description="Data sources")


class BacktestConfig(BaseModel):
    """
    Complete backtest configuration.

    This model contains all parameters needed to run a backtest, including
    time range, data sources, venue setup, strategies, execution simulation,
    risk controls, and output settings.
    """

    backtest: BacktestDetails = Field(description="Backtest details")
    time_range: TimeRangeConfig = Field(description="Time range")
    data: DataConfig = Field(description="Data configuration")
    venues: list[VenueBacktestConfig] = Field(description="Venue configurations")
    strategies: list[StrategyBacktestConfig] = Field(description="Strategy configurations")
    execution: ExecutionSimConfig = Field(description="Execution simulation")
    risk: RiskControlConfig = Field(description="Risk controls")
    output: OutputConfig = Field(description="Output configuration")
    metrics: MetricsConfig = Field(description="Performance metrics")


class BacktestConfigLoader(BaseYamlConfigLoader):
    """Loader for backtest configurations."""

    model_class = BacktestConfig
