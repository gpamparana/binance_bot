"""Parameter search space definition for strategy optimization.

This module defines the parameter ranges and distributions for
Optuna-based Bayesian optimization of the HedgeGridV1 strategy.
"""

from typing import Any

import optuna
from pydantic import BaseModel, Field


class ParameterBounds(BaseModel):
    """Defines bounds and distribution for a parameter."""

    min_value: float = Field(description="Minimum value")
    max_value: float = Field(description="Maximum value")
    step: float | None = Field(default=None, description="Step size for discrete params")
    log_scale: bool = Field(default=False, description="Use log scale for distribution")


class ParameterSpace:
    """
    Defines the search space for HedgeGridV1 strategy parameters.

    This class encapsulates the parameter ranges and provides methods
    to suggest parameters from an Optuna trial object. It handles both
    continuous and discrete distributions, with support for log-scale
    sampling for exponential ranges.

    Parameters are organized into categories matching HedgeGridConfig:
    - Grid parameters (5): step size, levels, quantities
    - Exit parameters (2): take profit and stop loss distances
    - Regime parameters (5): technical indicator periods
    - Policy parameters (2): counter-trend configuration
    - Rebalance parameters (1): grid recenter trigger
    - Funding parameters (1): maximum acceptable funding cost
    - Position parameters (1): maximum position percentage

    Total: 17 tunable parameters
    """

    # Grid parameters (optimized for 1-minute bars with $10k account)
    # Inventory validation: total_qty_both_sides * BTC_price <= account * max_position_pct
    # Bitcoin typically moves 0.1-0.3% per minute, so we need tighter grids
    # With levels=10, qty_scale=1.2, base_qty=0.005: ~0.08 BTC total = ~$7200 at $90k
    GRID_STEP_BPS = ParameterBounds(min_value=10, max_value=50, step=5)  # 0.1% to 0.5% spacing for 1-min bars
    GRID_LEVELS_LONG = ParameterBounds(
        min_value=3, max_value=5, step=1
    )  # 3-5 levels below mid (reduced for optimization speed)
    GRID_LEVELS_SHORT = ParameterBounds(
        min_value=3, max_value=5, step=1
    )  # 3-5 levels above mid (reduced for optimization speed)
    BASE_QTY = ParameterBounds(min_value=0.001, max_value=0.01, log_scale=True)  # $90-$900 per level (wider range)
    QTY_SCALE = ParameterBounds(min_value=1.0, max_value=1.2, step=0.05)  # Max 1.2 (20% growth)

    # Exit parameters
    TP_STEPS = ParameterBounds(min_value=1, max_value=10, step=1)
    SL_STEPS = ParameterBounds(min_value=3, max_value=20, step=1)

    # Regime detection parameters (optimized for 1-minute bars)
    # Shorter periods for faster response to 1-minute price action
    ADX_LEN = ParameterBounds(min_value=7, max_value=21, step=1)  # Shorter for 1-min bars
    EMA_FAST = ParameterBounds(min_value=5, max_value=15, step=1)  # Min 5 to match config validation
    EMA_SLOW = ParameterBounds(min_value=15, max_value=50, step=2)  # Shorter slow period
    ATR_LEN = ParameterBounds(min_value=7, max_value=21, step=1)  # Shorter for 1-min bars
    HYSTERESIS_BPS = ParameterBounds(min_value=5, max_value=30, step=5)  # Tighter for frequent updates

    # Policy parameters
    COUNTER_LEVELS = ParameterBounds(min_value=2, max_value=10, step=1)
    COUNTER_QTY_SCALE = ParameterBounds(min_value=0.3, max_value=1.0, step=0.05)  # Allow full size on counter-trend

    # Rebalance parameters (tighter for 1-minute bars)
    RECENTER_TRIGGER_BPS = ParameterBounds(min_value=25, max_value=200, step=25)  # Tighter trigger for 1-min bars

    # Funding parameters
    FUNDING_MAX_COST_BPS = ParameterBounds(min_value=5, max_value=50, step=5)

    # Position parameters (as decimal fraction, not percentage)
    MAX_POSITION_PCT = ParameterBounds(min_value=0.5, max_value=0.95, step=0.05)

    def __init__(self, custom_bounds: dict[str, ParameterBounds] | None = None):
        """
        Initialize parameter space with optional custom bounds.

        Parameters
        ----------
        custom_bounds : Dict[str, ParameterBounds], optional
            Override default bounds for specific parameters
        """
        self.custom_bounds = custom_bounds or {}

    def _get_bounds(self, param_name: str) -> ParameterBounds:
        """Get parameter bounds, using custom if provided."""
        if param_name in self.custom_bounds:
            return self.custom_bounds[param_name]
        return getattr(self.__class__, param_name)

    def suggest_parameters(self, trial: optuna.Trial) -> dict[str, Any]:
        """
        Suggest parameters from an Optuna trial.

        This method samples parameter values from the defined distributions
        and returns a dictionary compatible with HedgeGridConfig.

        Parameters
        ----------
        trial : optuna.Trial
            Optuna trial object for suggesting parameters

        Returns
        -------
        Dict[str, Any]
            Dictionary of suggested parameters organized by config section
        """
        # Grid parameters
        grid_step_bps = self._suggest_float(trial, "grid_step_bps", self._get_bounds("GRID_STEP_BPS"))
        grid_levels_long = self._suggest_int(trial, "grid_levels_long", self._get_bounds("GRID_LEVELS_LONG"))
        grid_levels_short = self._suggest_int(trial, "grid_levels_short", self._get_bounds("GRID_LEVELS_SHORT"))
        base_qty = self._suggest_float(trial, "base_qty", self._get_bounds("BASE_QTY"))
        qty_scale = self._suggest_float(trial, "qty_scale", self._get_bounds("QTY_SCALE"))

        # Exit parameters
        tp_steps = self._suggest_int(trial, "tp_steps", self._get_bounds("TP_STEPS"))
        sl_steps = self._suggest_int(trial, "sl_steps", self._get_bounds("SL_STEPS"))

        # Ensure TP is reasonable relative to SL
        if tp_steps > sl_steps * 3:
            tp_steps = min(tp_steps, sl_steps * 3)

        # Regime parameters
        adx_len = self._suggest_int(trial, "adx_len", self._get_bounds("ADX_LEN"))
        ema_fast = self._suggest_int(trial, "ema_fast", self._get_bounds("EMA_FAST"))
        ema_slow = self._suggest_int(trial, "ema_slow", self._get_bounds("EMA_SLOW"))

        # Ensure EMA fast is actually faster than slow (with minimum gap of 3 for 1-min bars)
        if ema_fast >= ema_slow:
            ema_fast = min(ema_fast, ema_slow - 3)

        atr_len = self._suggest_int(trial, "atr_len", self._get_bounds("ATR_LEN"))
        hysteresis_bps = self._suggest_float(trial, "hysteresis_bps", self._get_bounds("HYSTERESIS_BPS"))

        # Policy parameters
        counter_levels = self._suggest_int(trial, "counter_levels", self._get_bounds("COUNTER_LEVELS"))
        counter_qty_scale = self._suggest_float(trial, "counter_qty_scale", self._get_bounds("COUNTER_QTY_SCALE"))

        # Rebalance parameters
        recenter_trigger_bps = self._suggest_float(
            trial, "recenter_trigger_bps", self._get_bounds("RECENTER_TRIGGER_BPS")
        )

        # Funding parameters
        funding_max_cost_bps = self._suggest_float(
            trial, "funding_max_cost_bps", self._get_bounds("FUNDING_MAX_COST_BPS")
        )

        # Position parameters
        max_position_pct = self._suggest_float(trial, "max_position_pct", self._get_bounds("MAX_POSITION_PCT"))

        # Return parameters organized by config section
        return {
            "grid": {
                "grid_step_bps": grid_step_bps,
                "grid_levels_long": grid_levels_long,
                "grid_levels_short": grid_levels_short,
                "base_qty": base_qty,
                "qty_scale": qty_scale,
            },
            "exit": {
                "tp_steps": tp_steps,
                "sl_steps": sl_steps,
            },
            "regime": {
                "adx_len": adx_len,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "atr_len": atr_len,
                "hysteresis_bps": hysteresis_bps,
            },
            "policy": {
                "strategy": "throttled-counter",  # Fixed for optimization
                "counter_levels": counter_levels,
                "counter_qty_scale": counter_qty_scale,
            },
            "rebalance": {
                "recenter_trigger_bps": recenter_trigger_bps,
                # max_inventory_quote will come from base config
            },
            "funding": {
                "funding_window_minutes": 480,  # Fixed at 8 hours
                "funding_max_cost_bps": funding_max_cost_bps,
            },
            "position": {
                "max_position_pct": max_position_pct,  # As decimal fraction (0.5-0.95)
                # Other position params will come from base config
            },
        }

    def _suggest_float(self, trial: optuna.Trial, name: str, bounds: ParameterBounds) -> float:
        """Suggest a float parameter from trial."""
        if bounds.step is not None:
            # Discrete float with step
            return trial.suggest_float(name, bounds.min_value, bounds.max_value, step=bounds.step)
        # Continuous float
        return trial.suggest_float(name, bounds.min_value, bounds.max_value, log=bounds.log_scale)

    def _suggest_int(self, trial: optuna.Trial, name: str, bounds: ParameterBounds) -> int:
        """Suggest an integer parameter from trial."""
        return trial.suggest_int(
            name,
            int(bounds.min_value),
            int(bounds.max_value),
            step=int(bounds.step) if bounds.step else 1,
        )

    def validate_parameters(self, params: dict[str, Any]) -> bool:
        """
        Validate that suggested parameters meet all constraints.

        Parameters
        ----------
        params : Dict[str, Any]
            Parameter dictionary to validate

        Returns
        -------
        bool
            True if all parameters are valid
        """
        try:
            # Check grid parameters (minimum 10 bps for 1-minute bars)
            if params["grid"]["grid_step_bps"] < 10.0:
                return False
            if params["grid"]["qty_scale"] > 3.0:
                return False

            # Check exit relationship
            if params["exit"]["tp_steps"] > params["exit"]["sl_steps"] * 3:
                return False

            # Check EMA relationship
            if params["regime"]["ema_fast"] >= params["regime"]["ema_slow"]:
                return False

            # Check position limits (stored as decimal fraction: 0.5-0.95)
            if params["position"]["max_position_pct"] > 1.0 or params["position"]["max_position_pct"] < 0.1:
                return False

            # Check inventory caps - calculate exact max grid cost
            grid_params = params["grid"]
            levels_long = grid_params["grid_levels_long"]
            levels_short = grid_params["grid_levels_short"]
            base_qty = grid_params["base_qty"]
            qty_scale = grid_params["qty_scale"]

            # Calculate exact total quantity for geometric progression
            # Formula: sum = base_qty * (1 - scale^n) / (1 - scale)
            # For scale = 1.0 (no scaling), it's simply base_qty * n
            if qty_scale == 1.0:
                total_qty_long = base_qty * levels_long
                total_qty_short = base_qty * levels_short
            else:
                # Geometric sum formula
                total_qty_long = base_qty * (1 - qty_scale**levels_long) / (1 - qty_scale)
                total_qty_short = base_qty * (1 - qty_scale**levels_short) / (1 - qty_scale)

            # Total BTC exposure across both sides (worst case: all orders filled)
            total_btc_exposure = total_qty_long + total_qty_short

            # Estimate BTC price range for validation
            # Use conservative range to ensure we don't exceed limits
            max_btc_price = 100000.0  # Upper bound

            # Calculate maximum possible USDT exposure
            max_usdt_exposure = total_btc_exposure * max_btc_price

            # Account balance from backtest config
            account_balance = 10000.0  # USDT

            # Get max position percentage parameter (already as decimal fraction)
            max_position_pct = params.get("position", {}).get("max_position_pct", 0.8)

            # Maximum allowed exposure
            max_allowed_exposure = account_balance * max_position_pct

            # Reject if estimated inventory exceeds allowed exposure
            # Be more conservative for optimization to avoid rejection errors
            if max_usdt_exposure > max_allowed_exposure:
                # Log rejection reason (useful for debugging)
                # print(f"Rejected: {max_usdt_exposure:.2f} USDT > {max_allowed_exposure:.2f} USDT allowed")
                return False

            return True

        except (KeyError, TypeError):
            return False
