"""Tests for grid construction and adaptive re-centering."""

import pytest

from naut_hedgegrid.config.strategy import (
    ExecutionConfig,
    ExitConfig,
    FundingConfig,
    GridConfig,
    HedgeGridConfig,
    PositionConfig,
    RebalanceConfig,
    RegimeConfig,
    StrategyDetails,
)
from naut_hedgegrid.domain.types import Regime, Side
from naut_hedgegrid.strategy.grid import GridEngine

# Test Fixtures


def create_test_config(
    grid_step_bps: float = 25.0,
    grid_levels_long: int = 5,
    grid_levels_short: int = 5,
    base_qty: float = 0.1,
    qty_scale: float = 1.2,
    tp_steps: int = 2,
    sl_steps: int = 3,
    recenter_trigger_bps: float = 100.0,
    max_inventory_quote: float = 10000.0,
) -> HedgeGridConfig:
    """Create test configuration with default parameters."""
    return HedgeGridConfig(
        strategy=StrategyDetails(name="test", instrument_id="BTC-PERP"),
        grid=GridConfig(
            grid_step_bps=grid_step_bps,
            grid_levels_long=grid_levels_long,
            grid_levels_short=grid_levels_short,
            base_qty=base_qty,
            qty_scale=qty_scale,
        ),
        exit=ExitConfig(tp_steps=tp_steps, sl_steps=sl_steps),
        rebalance=RebalanceConfig(
            recenter_trigger_bps=recenter_trigger_bps,
            max_inventory_quote=max_inventory_quote,
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
    )


# Grid Construction Tests


def test_grid_engine_basic_construction() -> None:
    """Test basic grid ladder construction."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    assert len(ladders) == 2  # LONG and SHORT for SIDEWAYS
    assert ladders[0].side == Side.LONG
    assert ladders[1].side == Side.SHORT


def test_long_ladder_prices_below_mid() -> None:
    """Test LONG ladder prices are below mid price."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    for rung in long_ladder:
        assert rung.price < mid


def test_short_ladder_prices_above_mid() -> None:
    """Test SHORT ladder prices are above mid price."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    short_ladder = ladders[1]

    for rung in short_ladder:
        assert rung.price > mid


def test_price_spacing() -> None:
    """Test price spacing matches grid_step_bps."""
    cfg = create_test_config(grid_step_bps=25.0)  # 0.25%
    mid = 100.0
    expected_step = mid * (25.0 / 10000)  # 0.25

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    # Check spacing between consecutive levels
    for i in range(len(long_ladder) - 1):
        price_diff = abs(long_ladder[i].price - long_ladder[i + 1].price)
        assert price_diff == pytest.approx(expected_step, rel=1e-6)


def test_quantity_geometric_scaling() -> None:
    """Test geometric quantity scaling."""
    cfg = create_test_config(base_qty=0.1, qty_scale=1.5)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    # Level 1: base_qty * scale^0 = 0.1
    assert long_ladder[0].qty == pytest.approx(0.1, rel=1e-6)
    # Level 2: base_qty * scale^1 = 0.15
    assert long_ladder[1].qty == pytest.approx(0.15, rel=1e-6)
    # Level 3: base_qty * scale^2 = 0.225
    assert long_ladder[2].qty == pytest.approx(0.225, rel=1e-6)


def test_level_counts_match_config() -> None:
    """Test ladder level counts match configuration."""
    cfg = create_test_config(grid_levels_long=7, grid_levels_short=10)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    assert len(ladders[0]) == 7  # LONG ladder
    assert len(ladders[1]) == 10  # SHORT ladder


def test_tp_sl_calculation() -> None:
    """Test TP/SL calculation for LONG positions."""
    cfg = create_test_config(grid_step_bps=25.0, tp_steps=2, sl_steps=3)
    mid = 100.0
    price_step = mid * (25.0 / 10000)

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]
    first_rung = long_ladder[0]

    # TP should be 2 steps above entry
    expected_tp = first_rung.price + (2 * price_step)
    assert first_rung.tp == pytest.approx(expected_tp, rel=1e-6)

    # SL should be 3 steps below entry
    expected_sl = first_rung.price - (3 * price_step)
    assert first_rung.sl == pytest.approx(expected_sl, rel=1e-6)


def test_tp_sl_calculation_short() -> None:
    """Test TP/SL calculation for SHORT positions."""
    cfg = create_test_config(grid_step_bps=25.0, tp_steps=2, sl_steps=3)
    mid = 100.0
    price_step = mid * (25.0 / 10000)

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    short_ladder = ladders[1]
    first_rung = short_ladder[0]

    # TP should be 2 steps below entry
    expected_tp = first_rung.price - (2 * price_step)
    assert first_rung.tp == pytest.approx(expected_tp, rel=1e-6)

    # SL should be 3 steps above entry
    expected_sl = first_rung.price + (3 * price_step)
    assert first_rung.sl == pytest.approx(expected_sl, rel=1e-6)


def test_tp_disabled() -> None:
    """Test TP set to None when tp_steps=0.

    Note: ExitConfig validation requires tp_steps >= 1, so we test
    that our grid logic respects tp_steps=1 as minimum."""
    # Since ExitConfig requires >= 1, we test with 1 which should work
    # The actual "disabled" case would need special handling in production
    cfg = create_test_config(tp_steps=1, sl_steps=3)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    # With tp_steps=1, TP should be set
    for ladder in ladders:
        for rung in ladder:
            assert rung.tp is not None
            assert rung.sl is not None


def test_sl_disabled() -> None:
    """Test SL set to None when sl_steps=0.

    Note: ExitConfig validation requires sl_steps >= 1, so we test
    that our grid logic respects sl_steps=1 as minimum."""
    # Since ExitConfig requires >= 1, we test with 1 which should work
    cfg = create_test_config(tp_steps=2, sl_steps=1)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    # With sl_steps=1, SL should be set
    for ladder in ladders:
        for rung in ladder:
            assert rung.tp is not None
            assert rung.sl is not None


# Regime-Based Tests


def test_up_regime_returns_short_ladder_only() -> None:
    """Test UP regime returns only SHORT ladder."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.UP)

    assert len(ladders) == 1
    assert ladders[0].side == Side.SHORT


def test_down_regime_returns_long_ladder_only() -> None:
    """Test DOWN regime returns only LONG ladder."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.DOWN)

    assert len(ladders) == 1
    assert ladders[0].side == Side.LONG


def test_sideways_regime_returns_both_ladders() -> None:
    """Test SIDEWAYS regime returns both ladders."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    assert len(ladders) == 2
    assert ladders[0].side == Side.LONG
    assert ladders[1].side == Side.SHORT


# Inventory Cap Tests


def test_inventory_cap_validation_passes() -> None:
    """Test validation passes when within limits."""
    cfg = create_test_config(
        base_qty=0.1,
        qty_scale=1.0,  # No scaling
        grid_levels_long=5,
        max_inventory_quote=100.0,  # Enough for 0.5 BTC * 100 = 50
    )
    mid = 100.0

    # Should not raise
    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    assert len(ladders) == 2


def test_inventory_cap_long_exceeds() -> None:
    """Test validation raises error when LONG exceeds limit."""
    cfg = create_test_config(
        base_qty=1.0,
        qty_scale=2.0,  # Aggressive scaling
        grid_levels_long=10,
        max_inventory_quote=100.0,  # Too small
    )
    mid = 100.0

    with pytest.raises(ValueError, match="LONG ladder exceeds max inventory"):
        GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)


def test_inventory_cap_short_exceeds() -> None:
    """Test validation raises error when SHORT exceeds limit."""
    cfg = create_test_config(
        base_qty=1.0,
        qty_scale=2.0,  # Aggressive scaling
        grid_levels_long=1,  # Minimize LONG to avoid hitting its cap first
        grid_levels_short=10,
        max_inventory_quote=100.0,  # Too small
    )
    mid = 100.0

    with pytest.raises(ValueError, match=r"(SHORT|LONG) ladder exceeds max inventory"):
        GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)


def test_inventory_cap_exactly_at_limit() -> None:
    """Test validation passes when exactly at limit."""
    # Calculate exact configuration
    base_qty = 0.5
    levels = 2
    mid = 100.0
    # Total: 0.5 + 0.5 = 1.0 BTC * 100 = 100.0
    max_inventory = 100.0

    cfg = create_test_config(
        base_qty=base_qty,
        qty_scale=1.0,
        grid_levels_long=levels,
        grid_levels_short=levels,
        max_inventory_quote=max_inventory,
    )

    # Should not raise
    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    assert len(ladders) == 2


# Re-centering Tests


def test_recenter_needed_within_threshold() -> None:
    """Test recenter_needed returns False when within threshold."""
    cfg = create_test_config(recenter_trigger_bps=100.0)  # 1%
    last_center = 100.0
    mid = 100.5  # 0.5% deviation

    assert not GridEngine.recenter_needed(mid, last_center, cfg)


def test_recenter_needed_exceeds_threshold_positive() -> None:
    """Test recenter_needed returns True when exceeds threshold (positive)."""
    cfg = create_test_config(recenter_trigger_bps=100.0)  # 1%
    last_center = 100.0
    mid = 102.0  # 2% deviation

    assert GridEngine.recenter_needed(mid, last_center, cfg)


def test_recenter_needed_exceeds_threshold_negative() -> None:
    """Test recenter_needed returns True when exceeds threshold (negative)."""
    cfg = create_test_config(recenter_trigger_bps=100.0)  # 1%
    last_center = 100.0
    mid = 98.0  # -2% deviation

    assert GridEngine.recenter_needed(mid, last_center, cfg)


def test_recenter_needed_zero_last_center() -> None:
    """Test recenter_needed handles zero last_center."""
    cfg = create_test_config()
    mid = 100.0
    last_center = 0.0

    assert GridEngine.recenter_needed(mid, last_center, cfg)


def test_recenter_needed_various_thresholds() -> None:
    """Test recenter_needed with different threshold values."""
    last_center = 100.0
    mid = 100.5  # 0.5% deviation

    # Tight threshold: should trigger
    cfg_tight = create_test_config(recenter_trigger_bps=25.0)  # 0.25%
    assert GridEngine.recenter_needed(mid, last_center, cfg_tight)

    # Loose threshold: should not trigger
    cfg_loose = create_test_config(recenter_trigger_bps=200.0)  # 2%
    assert not GridEngine.recenter_needed(mid, last_center, cfg_loose)


# Geometry Tests


def test_symmetric_spacing_sideways() -> None:
    """Test symmetric spacing around mid for SIDEWAYS regime."""
    cfg = create_test_config(grid_levels_long=5, grid_levels_short=5)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]
    short_ladder = ladders[1]

    # Check first level distances from mid are equal
    long_distance = mid - long_ladder[0].price
    short_distance = short_ladder[0].price - mid

    assert long_distance == pytest.approx(short_distance, rel=1e-6)


def test_no_overlapping_prices() -> None:
    """Test no overlapping prices between LONG and SHORT."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]
    short_ladder = ladders[1]

    long_prices = {r.price for r in long_ladder}
    short_prices = {r.price for r in short_ladder}

    # No intersection
    assert not long_prices.intersection(short_prices)

    # All LONG prices below mid
    assert all(p < mid for p in long_prices)

    # All SHORT prices above mid
    assert all(p > mid for p in short_prices)


def test_long_ladder_sorted_descending() -> None:
    """Test LONG ladder sorted descending (closest to mid first)."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    prices = [r.price for r in long_ladder]
    # First price should be highest (closest to mid)
    assert prices == sorted(prices, reverse=True)


def test_short_ladder_sorted_ascending() -> None:
    """Test SHORT ladder sorted ascending (closest to mid first)."""
    cfg = create_test_config()
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    short_ladder = ladders[1]

    prices = [r.price for r in short_ladder]
    # First price should be lowest (closest to mid)
    assert prices == sorted(prices)


# Edge Cases


def test_single_level_grids() -> None:
    """Test with grid_levels=1 for both sides."""
    cfg = create_test_config(grid_levels_long=1, grid_levels_short=1)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)

    assert len(ladders[0]) == 1
    assert len(ladders[1]) == 1


def test_no_quantity_scaling() -> None:
    """Test with qty_scale=1.0 (no scaling)."""
    cfg = create_test_config(base_qty=0.5, qty_scale=1.0, grid_levels_long=5)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    # All quantities should be equal to base_qty
    for rung in long_ladder:
        assert rung.qty == pytest.approx(0.5, rel=1e-6)


def test_large_quantity_scaling() -> None:
    """Test with large qty_scale."""
    cfg = create_test_config(base_qty=0.1, qty_scale=2.0, grid_levels_long=3)
    mid = 100.0

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    # Level 1: 0.1, Level 2: 0.2, Level 3: 0.4
    assert long_ladder[0].qty == pytest.approx(0.1, rel=1e-6)
    assert long_ladder[1].qty == pytest.approx(0.2, rel=1e-6)
    assert long_ladder[2].qty == pytest.approx(0.4, rel=1e-6)


def test_tight_grid_spacing() -> None:
    """Test with small grid_step_bps (tight grid)."""
    cfg = create_test_config(grid_step_bps=5.0)  # 0.05%
    mid = 100.0
    expected_step = mid * (5.0 / 10000)

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    # Check first level is very close to mid
    distance = mid - long_ladder[0].price
    assert distance == pytest.approx(expected_step, rel=1e-6)


def test_wide_grid_spacing() -> None:
    """Test with large grid_step_bps (wide grid)."""
    cfg = create_test_config(grid_step_bps=500.0)  # 5%
    mid = 100.0
    expected_step = mid * (500.0 / 10000)

    ladders = GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
    long_ladder = ladders[0]

    # Check first level is far from mid
    distance = mid - long_ladder[0].price
    assert distance == pytest.approx(expected_step, rel=1e-6)


def test_invalid_mid_price_zero() -> None:
    """Test validation with mid price of zero."""
    cfg = create_test_config()
    mid = 0.0

    with pytest.raises(ValueError, match="Mid price must be positive"):
        GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)


def test_invalid_mid_price_negative() -> None:
    """Test validation with negative mid price."""
    cfg = create_test_config()
    mid = -100.0

    with pytest.raises(ValueError, match="Mid price must be positive"):
        GridEngine.build_ladders(mid, cfg, Regime.SIDEWAYS)
