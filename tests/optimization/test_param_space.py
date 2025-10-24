"""Tests for parameter space definition and sampling."""

import optuna
import pytest

from naut_hedgegrid.optimization.param_space import ParameterBounds, ParameterSpace


class TestParameterBounds:
    """Tests for ParameterBounds model."""

    def test_valid_bounds(self):
        """Test creation of valid parameter bounds."""
        bounds = ParameterBounds(min_value=1.0, max_value=10.0)
        assert bounds.min_value == 1.0
        assert bounds.max_value == 10.0
        assert bounds.step is None
        assert bounds.log_scale is False

    def test_log_scale_bounds(self):
        """Test log scale parameter bounds."""
        bounds = ParameterBounds(min_value=0.001, max_value=1.0, log_scale=True)
        assert bounds.log_scale is True

    def test_discrete_bounds(self):
        """Test discrete parameter bounds with step."""
        bounds = ParameterBounds(min_value=10, max_value=100, step=10)
        assert bounds.step == 10


class TestParameterSpace:
    """Tests for ParameterSpace class."""

    def test_initialization(self):
        """Test parameter space initialization."""
        space = ParameterSpace()
        assert space.custom_bounds == {}

    def test_custom_bounds(self):
        """Test parameter space with custom bounds."""
        custom = {
            "GRID_STEP_BPS": ParameterBounds(min_value=20, max_value=100, step=10)
        }
        space = ParameterSpace(custom_bounds=custom)
        assert space.custom_bounds == custom

    def test_suggest_parameters(self):
        """Test parameter suggestion from Optuna trial."""
        study = optuna.create_study(direction="maximize")
        trial = study.ask()

        space = ParameterSpace()
        params = space.suggest_parameters(trial)

        # Check structure
        assert "grid" in params
        assert "exit" in params
        assert "regime" in params
        assert "policy" in params
        assert "rebalance" in params
        assert "funding" in params
        assert "position" in params

        # Check grid parameters
        assert "grid_step_bps" in params["grid"]
        assert "grid_levels_long" in params["grid"]
        assert "grid_levels_short" in params["grid"]
        assert "base_qty" in params["grid"]
        assert "qty_scale" in params["grid"]

        # Check exit parameters
        assert "tp_steps" in params["exit"]
        assert "sl_steps" in params["exit"]

        # Check regime parameters
        assert "adx_len" in params["regime"]
        assert "ema_fast" in params["regime"]
        assert "ema_slow" in params["regime"]
        assert "atr_len" in params["regime"]
        assert "hysteresis_bps" in params["regime"]

    def test_parameter_ranges(self):
        """Test that suggested parameters are within bounds."""
        study = optuna.create_study(direction="maximize")

        space = ParameterSpace()

        for _ in range(10):
            trial = study.ask()
            params = space.suggest_parameters(trial)

            # Check grid ranges
            assert 10 <= params["grid"]["grid_step_bps"] <= 200
            assert 3 <= params["grid"]["grid_levels_long"] <= 20
            assert 3 <= params["grid"]["grid_levels_short"] <= 20
            assert 0.001 <= params["grid"]["base_qty"] <= 0.1
            assert 1.0 <= params["grid"]["qty_scale"] <= 1.5

            # Check exit ranges
            assert 1 <= params["exit"]["tp_steps"] <= 10
            assert 3 <= params["exit"]["sl_steps"] <= 20

            # Check regime ranges
            assert 7 <= params["regime"]["adx_len"] <= 30
            assert 5 <= params["regime"]["ema_fast"] <= 25
            assert 20 <= params["regime"]["ema_slow"] <= 60
            assert 7 <= params["regime"]["atr_len"] <= 30
            assert 5 <= params["regime"]["hysteresis_bps"] <= 50

    def test_ema_relationship_enforcement(self):
        """Test that EMA fast is always less than EMA slow."""
        study = optuna.create_study(direction="maximize")

        space = ParameterSpace()

        for _ in range(20):
            trial = study.ask()
            params = space.suggest_parameters(trial)

            # EMA fast must be less than EMA slow
            assert params["regime"]["ema_fast"] < params["regime"]["ema_slow"]

    def test_tp_sl_relationship_enforcement(self):
        """Test that TP steps is reasonable relative to SL steps."""
        study = optuna.create_study(direction="maximize")

        space = ParameterSpace()

        for _ in range(20):
            trial = study.ask()
            params = space.suggest_parameters(trial)

            # TP should not be more than 3x SL
            assert params["exit"]["tp_steps"] <= params["exit"]["sl_steps"] * 3

    def test_validate_parameters_valid(self):
        """Test validation of valid parameters."""
        space = ParameterSpace()

        valid_params = {
            "grid": {
                "grid_step_bps": 50.0,
                "grid_levels_long": 10,
                "grid_levels_short": 10,
                "base_qty": 0.01,
                "qty_scale": 1.2,
            },
            "exit": {
                "tp_steps": 2,
                "sl_steps": 5,
            },
            "regime": {
                "adx_len": 14,
                "ema_fast": 21,
                "ema_slow": 50,
                "atr_len": 14,
                "hysteresis_bps": 10.0,
            },
            "policy": {
                "counter_levels": 5,
                "counter_qty_scale": 0.5,
            },
            "position": {
                "max_position_pct": 0.85,
            },
        }

        assert space.validate_parameters(valid_params) is True

    def test_validate_parameters_invalid_grid_step(self):
        """Test validation fails for too small grid step."""
        space = ParameterSpace()

        invalid_params = {
            "grid": {
                "grid_step_bps": 2.0,  # Too small
                "qty_scale": 1.2,
            },
            "exit": {"tp_steps": 2, "sl_steps": 5},
            "regime": {"ema_fast": 21, "ema_slow": 50},
            "position": {"max_position_pct": 0.85},
        }

        assert space.validate_parameters(invalid_params) is False

    def test_validate_parameters_invalid_ema_relationship(self):
        """Test validation fails for invalid EMA relationship."""
        space = ParameterSpace()

        invalid_params = {
            "grid": {
                "grid_step_bps": 50.0,
                "qty_scale": 1.2,
            },
            "exit": {"tp_steps": 2, "sl_steps": 5},
            "regime": {
                "ema_fast": 50,  # Greater than or equal to slow
                "ema_slow": 50,
            },
            "position": {"max_position_pct": 0.85},
        }

        assert space.validate_parameters(invalid_params) is False

    def test_validate_parameters_invalid_tp_sl(self):
        """Test validation fails for invalid TP/SL relationship."""
        space = ParameterSpace()

        invalid_params = {
            "grid": {
                "grid_step_bps": 50.0,
                "qty_scale": 1.2,
            },
            "exit": {
                "tp_steps": 20,  # More than 3x SL
                "sl_steps": 5,
            },
            "regime": {"ema_fast": 21, "ema_slow": 50},
            "position": {"max_position_pct": 0.85},
        }

        assert space.validate_parameters(invalid_params) is False