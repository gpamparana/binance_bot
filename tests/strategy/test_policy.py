"""Tests for placement policy and inventory biasing."""

import pytest

from naut_hedgegrid.config.strategy import (
    ExecutionConfig,
    ExitConfig,
    FundingConfig,
    GridConfig,
    HedgeGridConfig,
    PolicyConfig,
    PositionConfig,
    RebalanceConfig,
    RegimeConfig,
    StrategyDetails,
)
from naut_hedgegrid.domain.types import Regime, Side
from naut_hedgegrid.strategy.grid import GridEngine
from naut_hedgegrid.strategy.policy import PlacementPolicy

# Test Fixtures


def create_test_config(
    policy_strategy: str = "throttled-counter",
    counter_levels: int = 2,
    counter_qty_scale: float = 0.5,
    grid_levels_long: int = 5,
    grid_levels_short: int = 5,
) -> HedgeGridConfig:
    """Create test configuration with policy parameters."""
    return HedgeGridConfig(
        strategy=StrategyDetails(name="test", instrument_id="BTC-PERP"),
        grid=GridConfig(
            grid_step_bps=25.0,
            grid_levels_long=grid_levels_long,
            grid_levels_short=grid_levels_short,
            base_qty=0.1,
            qty_scale=1.2,
        ),
        exit=ExitConfig(tp_steps=2, sl_steps=3),
        rebalance=RebalanceConfig(
            recenter_trigger_bps=100.0,
            max_inventory_quote=10000.0,
        ),
        execution=ExecutionConfig(
            maker_only=True,
            use_post_only_retries=True,
            retry_attempts=3,
            retry_delay_ms=100,
        ),
        funding=FundingConfig(
            funding_window_minutes=480,
            funding_max_cost_bps=10.0,
        ),
        position=PositionConfig(
            max_position_size=10.0,
            max_leverage_used=10.0,
            emergency_liquidation_buffer=0.15,
        ),
        regime=RegimeConfig(adx_len=14, ema_fast=20, ema_slow=50, atr_len=14, hysteresis_bps=25.0),
        policy=PolicyConfig(
            strategy=policy_strategy,
            counter_levels=counter_levels,
            counter_qty_scale=counter_qty_scale,
        ),
    )


# SIDEWAYS Regime Tests


def test_sideways_returns_unchanged_ladders() -> None:
    """Test SIDEWAYS regime returns ladders unchanged."""
    cfg = create_test_config()
    mid = 100.0

    # Build original ladders
    original = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    # Apply policy
    shaped = PlacementPolicy.shape_ladders(original, Regime.SIDEWAYS, cfg)

    # Should be unchanged for SIDEWAYS
    assert len(shaped) == len(original)
    assert shaped[0].side == Side.LONG
    assert shaped[1].side == Side.SHORT
    assert len(shaped[0]) == len(original[0])
    assert len(shaped[1]) == len(original[1])


def test_sideways_preserves_quantities() -> None:
    """Test SIDEWAYS regime preserves all quantities."""
    cfg = create_test_config()
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    shaped = PlacementPolicy.shape_ladders(original, Regime.SIDEWAYS, cfg)

    # Check quantities unchanged
    for orig_ladder, shaped_ladder in zip(original, shaped, strict=True):
        assert orig_ladder.total_qty() == pytest.approx(shaped_ladder.total_qty())


# UP Regime Tests


def test_up_regime_throttles_short_side() -> None:
    """Test UP regime throttles SHORT (counter-trend) side.

    In an uptrend, SHORT grid orders fill into strength but need
    price reversal for profit, making SHORT the counter-trend side.
    """
    cfg = create_test_config(counter_levels=2)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    long_ladder = next(ladder for ladder in shaped if ladder.side == Side.LONG)
    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # SHORT should be throttled to counter_levels (counter-trend in UP)
    assert len(short_ladder) == 2

    # LONG should remain full (trend-following in UP)
    assert len(long_ladder) == 5


def test_up_regime_keeps_long_full() -> None:
    """Test UP regime keeps LONG (with-trend) ladder full."""
    cfg = create_test_config(counter_levels=3, grid_levels_long=7)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    long_ladder = next(ladder for ladder in shaped if ladder.side == Side.LONG)

    # LONG should be unchanged (full 7 levels, trend-following in UP)
    assert len(long_ladder) == 7


def test_up_regime_scales_short_quantities() -> None:
    """Test UP regime scales SHORT quantities by counter_qty_scale."""
    cfg = create_test_config(counter_levels=3, counter_qty_scale=0.5)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    short_original = next(ladder for ladder in original if ladder.side == Side.SHORT)
    short_shaped = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # Check each rung quantity is scaled by 0.5
    for i in range(len(short_shaped)):
        expected_qty = short_original[i].qty * 0.5
        assert short_shaped[i].qty == pytest.approx(expected_qty)


# DOWN Regime Tests


def test_down_regime_throttles_long_side() -> None:
    """Test DOWN regime throttles LONG (counter-trend) side.

    In a downtrend, LONG grid orders fill into weakness but need
    price reversal for profit, making LONG the counter-trend side.
    """
    cfg = create_test_config(counter_levels=2)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.DOWN)
    shaped = PlacementPolicy.shape_ladders(original, Regime.DOWN, cfg)

    long_ladder = next(ladder for ladder in shaped if ladder.side == Side.LONG)
    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # LONG should be throttled to counter_levels (counter-trend in DOWN)
    assert len(long_ladder) == 2

    # SHORT should remain full (trend-following in DOWN)
    assert len(short_ladder) == 5


def test_down_regime_keeps_short_full() -> None:
    """Test DOWN regime keeps SHORT (with-trend) ladder full."""
    cfg = create_test_config(counter_levels=3, grid_levels_short=8)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.DOWN)
    shaped = PlacementPolicy.shape_ladders(original, Regime.DOWN, cfg)

    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # SHORT should be unchanged (full 8 levels, trend-following in DOWN)
    assert len(short_ladder) == 8


def test_down_regime_scales_long_quantities() -> None:
    """Test DOWN regime scales LONG quantities by counter_qty_scale."""
    cfg = create_test_config(counter_levels=3, counter_qty_scale=0.3)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.DOWN)
    shaped = PlacementPolicy.shape_ladders(original, Regime.DOWN, cfg)

    long_original = next(ladder for ladder in original if ladder.side == Side.LONG)
    long_shaped = next(ladder for ladder in shaped if ladder.side == Side.LONG)

    # Check each rung quantity is scaled by 0.3
    for i in range(len(long_shaped)):
        expected_qty = long_original[i].qty * 0.3
        assert long_shaped[i].qty == pytest.approx(expected_qty)


# Property Tests


def test_immutability_original_ladders_unchanged() -> None:
    """Test shape_ladders doesn't mutate original ladders."""
    cfg = create_test_config(counter_levels=2, counter_qty_scale=0.5)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    # Store original state
    original_long_count = len(original[0])
    original_short_count = len(original[1])
    original_long_qty = original[0].total_qty()
    original_short_qty = original[1].total_qty()

    # Apply policy
    PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    # Verify originals unchanged
    assert len(original[0]) == original_long_count
    assert len(original[1]) == original_short_count
    assert original[0].total_qty() == pytest.approx(original_long_qty)
    assert original[1].total_qty() == pytest.approx(original_short_qty)


def test_truncation_keeps_closest_to_mid() -> None:
    """Test truncation keeps rungs closest to mid price."""
    cfg = create_test_config(counter_levels=3)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    short_original = next(ladder for ladder in original if ladder.side == Side.SHORT)
    short_shaped = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # First 3 rungs should be preserved (closest to mid)
    for i in range(3):
        assert short_shaped[i].price == short_original[i].price
        # Quantities will be scaled, but prices should match


def test_counter_levels_zero_removes_side() -> None:
    """Test counter_levels=0 completely removes counter-trend side."""
    cfg = create_test_config(counter_levels=0)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    long_ladder = next(ladder for ladder in shaped if ladder.side == Side.LONG)
    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # SHORT should be empty (counter-trend in UP)
    assert len(short_ladder) == 0
    assert short_ladder.is_empty

    # LONG should be full (trend-following)
    assert len(long_ladder) == 5


def test_counter_qty_scale_one_preserves_quantities() -> None:
    """Test counter_qty_scale=1.0 preserves original quantities."""
    cfg = create_test_config(counter_levels=3, counter_qty_scale=1.0)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.DOWN)
    shaped = PlacementPolicy.shape_ladders(original, Regime.DOWN, cfg)

    long_original = next(ladder for ladder in original if ladder.side == Side.LONG)
    long_shaped = next(ladder for ladder in shaped if ladder.side == Side.LONG)

    # Quantities should match exactly (after truncation)
    for i in range(len(long_shaped)):
        assert long_shaped[i].qty == pytest.approx(long_original[i].qty)


def test_prices_and_tpsl_preserved_after_throttling() -> None:
    """Test price, TP, and SL are preserved during throttling."""
    cfg = create_test_config(counter_levels=2, counter_qty_scale=0.7)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    short_original = next(ladder for ladder in original if ladder.side == Side.SHORT)
    short_shaped = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # Check price, TP, SL preserved for each rung
    for i in range(len(short_shaped)):
        assert short_shaped[i].price == short_original[i].price
        assert short_shaped[i].tp == short_original[i].tp
        assert short_shaped[i].sl == short_original[i].sl
        assert short_shaped[i].side == short_original[i].side


# Strategy-Specific Tests


def test_core_and_scalp_strategy_behavior() -> None:
    """Test core-and-scalp thins BOTH sides to counter_levels (narrow band)."""
    cfg = create_test_config(policy_strategy="core-and-scalp", counter_levels=2)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)

    # Test UP regime: SHORT is counter-trend
    shaped_up = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)
    long_up = next(ladder for ladder in shaped_up if ladder.side == Side.LONG)
    short_up = next(ladder for ladder in shaped_up if ladder.side == Side.SHORT)

    # Core-and-scalp thins BOTH sides to counter_levels (narrow market-making band)
    assert len(long_up) == 2  # Trend side also thinned to counter_levels
    assert len(short_up) == 2  # Counter-trend throttled

    # Test DOWN regime: LONG is counter-trend
    original_down = GridEngine.build_ladders(mid, cfg, Regime.DOWN)
    shaped_down = PlacementPolicy.shape_ladders(original_down, Regime.DOWN, cfg)
    long_down = next(ladder for ladder in shaped_down if ladder.side == Side.LONG)
    short_down = next(ladder for ladder in shaped_down if ladder.side == Side.SHORT)

    # Both sides thinned
    assert len(long_down) == 2  # Counter-trend throttled
    assert len(short_down) == 2  # Trend side also thinned to counter_levels


def test_throttled_counter_strategy_behavior() -> None:
    """Test throttled-counter strategy behavior matches core-and-scalp."""
    # Note: Both strategies use same throttling logic, differ in trading intent
    cfg = create_test_config(policy_strategy="throttled-counter", counter_levels=3)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    long_ladder = next(ladder for ladder in shaped if ladder.side == Side.LONG)
    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # LONG full (trend-following), SHORT throttled (counter-trend in UP)
    assert len(long_ladder) == 5
    assert len(short_ladder) == 3


# Edge Cases


def test_counter_levels_exceeds_available() -> None:
    """Test counter_levels > available levels uses all available."""
    cfg = create_test_config(counter_levels=10, grid_levels_long=4, grid_levels_short=4)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg)

    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # Should use all 4 available levels (not fail)
    assert len(short_ladder) == 4


def test_single_level_grid_with_throttling() -> None:
    """Test throttling with single-level grid."""
    cfg = create_test_config(counter_levels=1, grid_levels_long=1, grid_levels_short=1)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg, Regime.DOWN)
    shaped = PlacementPolicy.shape_ladders(original, Regime.DOWN, cfg)

    long_ladder = next(ladder for ladder in shaped if ladder.side == Side.LONG)
    short_ladder = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # Both should have 1 level
    assert len(long_ladder) == 1
    assert len(short_ladder) == 1


def test_extreme_qty_scaling() -> None:
    """Test extreme counter_qty_scale values."""
    # Very small scale
    cfg_small = create_test_config(counter_levels=3, counter_qty_scale=0.01)
    mid = 100.0

    original = GridEngine.build_ladders(mid, cfg_small, Regime.UP)
    shaped = PlacementPolicy.shape_ladders(original, Regime.UP, cfg_small)

    short_shaped = next(ladder for ladder in shaped if ladder.side == Side.SHORT)

    # Quantities should be 1% of original (SHORT is counter-trend in UP)
    short_original = next(ladder for ladder in original if ladder.side == Side.SHORT)
    for i in range(len(short_shaped)):
        expected_qty = short_original[i].qty * 0.01
        assert short_shaped[i].qty == pytest.approx(expected_qty)
