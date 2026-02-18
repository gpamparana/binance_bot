"""Tests for funding guard system."""
# Allow private member access in tests

from datetime import UTC, datetime, timedelta

import pytest

from naut_hedgegrid.domain.types import Ladder, Rung, Side
from naut_hedgegrid.strategy.funding_guard import FundingGuard

# Test helper to create sample ladders


def create_sample_ladders() -> list[Ladder]:
    """Create sample LONG and SHORT ladders for testing."""
    long_rungs = [
        Rung(price=99.0, qty=0.1, side=Side.LONG, tp=100.0, sl=98.0),
        Rung(price=98.0, qty=0.12, side=Side.LONG, tp=99.0, sl=97.0),
        Rung(price=97.0, qty=0.14, side=Side.LONG, tp=98.0, sl=96.0),
    ]
    short_rungs = [
        Rung(price=101.0, qty=0.1, side=Side.SHORT, tp=100.0, sl=102.0),
        Rung(price=102.0, qty=0.12, side=Side.SHORT, tp=101.0, sl=103.0),
        Rung(price=103.0, qty=0.14, side=Side.SHORT, tp=102.0, sl=104.0),
    ]
    return [
        Ladder.from_list(Side.LONG, long_rungs),
        Ladder.from_list(Side.SHORT, short_rungs),
    ]


# Initialization & State Tests


def test_funding_guard_initialization() -> None:
    """Test FundingGuard initialization with valid parameters."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)

    assert guard._window_minutes == 480
    assert guard._max_cost_bps == 10.0
    assert guard._current_rate is None
    assert guard._next_funding_ts is None
    assert not guard.is_active


def test_funding_guard_invalid_window() -> None:
    """Test FundingGuard rejects invalid window minutes."""
    with pytest.raises(ValueError, match="Window minutes must be positive"):
        FundingGuard(window_minutes=0, max_cost_bps=10.0)

    with pytest.raises(ValueError, match="Window minutes must be positive"):
        FundingGuard(window_minutes=-100, max_cost_bps=10.0)


def test_funding_guard_invalid_max_cost() -> None:
    """Test FundingGuard rejects negative max cost."""
    with pytest.raises(ValueError, match="Max cost bps must be non-negative"):
        FundingGuard(window_minutes=480, max_cost_bps=-5.0)


def test_on_funding_update_stores_values() -> None:
    """Test on_funding_update stores rate and timestamp."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)

    rate = 0.0001  # 0.01%
    next_ts = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    guard.on_funding_update(rate, next_ts)

    assert guard.current_rate == rate
    assert guard.next_funding_ts == next_ts
    assert guard.is_active


# Cost Calculation Tests


def test_projected_cost_calculation() -> None:
    """Test projected funding cost calculation."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)  # 8 hours

    # Rate of 0.01% over 8 hours = 0.01% * 1 period = 0.01% = 1 bps
    guard.on_funding_update(rate=0.0001, next_ts=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC))

    cost = guard._calculate_projected_cost()
    assert cost == pytest.approx(1.0, rel=1e-6)


def test_projected_cost_multiple_periods() -> None:
    """Test projected cost over multiple funding periods."""
    # 16-hour window = 2 funding periods
    guard = FundingGuard(window_minutes=960, max_cost_bps=10.0)

    # Rate of 0.01% * 2 periods = 0.02% = 2 bps
    guard.on_funding_update(rate=0.0001, next_ts=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC))

    cost = guard._calculate_projected_cost()
    assert cost == pytest.approx(2.0, rel=1e-6)


def test_projected_cost_negative_rate() -> None:
    """Test projected cost uses absolute value of rate."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)

    # Negative rate (shorts pay longs), cost is absolute value
    guard.on_funding_update(rate=-0.0001, next_ts=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC))

    cost = guard._calculate_projected_cost()
    assert cost == pytest.approx(1.0, rel=1e-6)


def test_projected_cost_zero_rate() -> None:
    """Test projected cost with zero funding rate."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)

    guard.on_funding_update(rate=0.0, next_ts=datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC))

    cost = guard._calculate_projected_cost()
    assert cost == 0.0


# Time Window Logic Tests


def test_no_adjustment_outside_window() -> None:
    """Test no adjustment when outside time window."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    # Funding in 2 hours (120 minutes), window is 60 minutes
    now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=UTC)
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    # High rate to trigger adjustment if within window
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    adjusted = guard.adjust_ladders(ladders, now)

    # Should be unchanged (outside window)
    assert adjusted == ladders


def test_no_adjustment_after_funding_passed() -> None:
    """Test no adjustment when funding time has passed."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    # Funding already passed
    now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=UTC)
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    adjusted = guard.adjust_ladders(ladders, now)

    # Should be unchanged (funding passed)
    assert adjusted == ladders


def test_gradual_scaling_as_funding_approaches() -> None:
    """Test quantities scale down gradually as funding approaches."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    # High rate to trigger adjustment
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)  # 10 bps per period

    ladders = create_sample_ladders()
    original_long = ladders[0]

    # Test at 30 minutes before funding (50% of window)
    now_half = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted_half = guard.adjust_ladders(ladders, now_half)

    # Positive rate → LONG pays → LONG ladder scaled to 50%
    long_half = next(ladder for ladder in adjusted_half if ladder.side == Side.LONG)
    for i in range(len(long_half)):
        expected_qty = original_long[i].qty * 0.5
        assert long_half[i].qty == pytest.approx(expected_qty)


def test_full_scaling_at_funding_time() -> None:
    """Test quantities scale to zero at funding timestamp (ladder becomes empty)."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()

    # At funding time (0 minutes until)
    now_at_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now_at_funding)

    # Positive rate → LONG pays → LONG ladder becomes empty (zero qty rungs filtered)
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    assert long_ladder.is_empty
    assert len(long_ladder) == 0


def test_adjustment_starts_at_window_boundary() -> None:
    """Test adjustment begins exactly at window boundary."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_long = ladders[0]

    # Exactly at window boundary (60 minutes before funding)
    now_boundary = datetime(2024, 1, 1, 7, 0, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now_boundary)

    # Should have full quantity (scale_factor = 1.0)
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    for i in range(len(long_ladder)):
        assert long_ladder[i].qty == pytest.approx(original_long[i].qty)


# Side Determination Tests


def test_negative_funding_reduces_short() -> None:
    """Test negative funding rate reduces SHORT ladder."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    # Negative rate: shorts pay longs
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=-0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_short = ladders[1]

    # 30 minutes before funding
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # SHORT should be scaled, LONG should be unchanged
    short_ladder = next(ladder for ladder in adjusted if ladder.side == Side.SHORT)
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)

    for i in range(len(short_ladder)):
        expected_qty = original_short[i].qty * 0.5
        assert short_ladder[i].qty == pytest.approx(expected_qty)

    # LONG unchanged
    for i in range(len(long_ladder)):
        assert long_ladder[i].qty == pytest.approx(ladders[0][i].qty)


def test_positive_funding_reduces_long() -> None:
    """Test positive funding rate reduces LONG ladder."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    # Positive rate: longs pay shorts
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_long = ladders[0]

    # 30 minutes before funding
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # LONG should be scaled, SHORT should be unchanged
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    short_ladder = next(ladder for ladder in adjusted if ladder.side == Side.SHORT)

    for i in range(len(long_ladder)):
        expected_qty = original_long[i].qty * 0.5
        assert long_ladder[i].qty == pytest.approx(expected_qty)

    # SHORT unchanged
    for i in range(len(short_ladder)):
        assert short_ladder[i].qty == pytest.approx(ladders[1][i].qty)


def test_zero_funding_no_adjustment() -> None:
    """Test zero funding rate causes no adjustment."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.0, next_ts=next_funding)

    ladders = create_sample_ladders()

    # Within window but zero cost
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Should be unchanged (cost = 0 <= threshold)
    assert adjusted == ladders


# Quantity Adjustment Tests


def test_scales_to_zero_at_funding_when_cost_exceeds() -> None:
    """Test quantities scale to zero at funding time when cost exceeds cap (ladder becomes empty)."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=5.0)

    # High rate: 0.1% over 8 hours = 10 bps > 5 bps threshold
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.001, next_ts=next_funding)

    ladders = create_sample_ladders()

    # At funding time
    now = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Positive rate → LONG pays → LONG ladder becomes empty (zero qty rungs filtered)
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    assert long_ladder.is_empty
    assert len(long_ladder) == 0


def test_proportional_scaling_based_on_time() -> None:
    """Test scaling is proportional to time remaining."""
    guard = FundingGuard(window_minutes=120, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_long = ladders[0]

    # Test various time points (excluding 0.0 factor which results in empty ladder)
    test_cases = [
        (120, 1.0),  # At window edge: 100% quantity
        (90, 0.75),  # 75% of window: 75% quantity
        (60, 0.5),  # 50% of window: 50% quantity
        (30, 0.25),  # 25% of window: 25% quantity
    ]

    for minutes_before, expected_factor in test_cases:
        now = next_funding - timedelta(minutes=minutes_before)
        adjusted = guard.adjust_ladders(ladders, now)

        long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
        for i in range(len(long_ladder)):
            expected_qty = original_long[i].qty * expected_factor
            assert long_ladder[i].qty == pytest.approx(expected_qty, abs=1e-6)

    # Test at funding time (0.0 factor) - ladder becomes empty
    now_funding = next_funding
    adjusted_funding = guard.adjust_ladders(ladders, now_funding)
    long_ladder_funding = next(ladder for ladder in adjusted_funding if ladder.side == Side.LONG)
    assert long_ladder_funding.is_empty


def test_preserves_price_tp_sl() -> None:
    """Test price, TP, and SL are preserved during scaling."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_long = ladders[0]

    # 30 minutes before funding
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)

    # Check price, TP, SL preserved
    for i in range(len(long_ladder)):
        assert long_ladder[i].price == original_long[i].price
        assert long_ladder[i].tp == original_long[i].tp
        assert long_ladder[i].sl == original_long[i].sl
        assert long_ladder[i].side == original_long[i].side


def test_immutability_original_ladders_unchanged() -> None:
    """Test adjust_ladders doesn't mutate original ladders."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()

    # Store original state
    original_long_qty = ladders[0].total_qty()
    original_short_qty = ladders[1].total_qty()

    # Adjust ladders
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    guard.adjust_ladders(ladders, now)

    # Verify originals unchanged
    assert ladders[0].total_qty() == pytest.approx(original_long_qty)
    assert ladders[1].total_qty() == pytest.approx(original_short_qty)


def test_multiple_ladders_handled_correctly() -> None:
    """Test guard handles multiple ladders of same side."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    # Create multiple LONG ladders
    long1 = Ladder.from_list(Side.LONG, [Rung(price=99.0, qty=0.1, side=Side.LONG, tp=100.0, sl=98.0)])
    long2 = Ladder.from_list(Side.LONG, [Rung(price=98.0, qty=0.2, side=Side.LONG, tp=99.0, sl=97.0)])
    short1 = Ladder.from_list(Side.SHORT, [Rung(price=101.0, qty=0.1, side=Side.SHORT, tp=100.0, sl=102.0)])

    ladders = [long1, long2, short1]

    # 30 minutes before funding
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Both LONG ladders should be scaled
    long_ladders = [ladder for ladder in adjusted if ladder.side == Side.LONG]
    assert len(long_ladders) == 2
    for ladder in long_ladders:
        for rung in ladder:
            # Should be scaled by 0.5
            assert rung.qty < 0.15  # Less than original


def test_only_affects_paying_side() -> None:
    """Test only the paying side is affected."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_short_qty = ladders[1].total_qty()

    # 30 minutes before funding
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # SHORT should be completely unchanged
    short_ladder = next(ladder for ladder in adjusted if ladder.side == Side.SHORT)
    assert short_ladder.total_qty() == pytest.approx(original_short_qty)


# Cost Threshold Tests


def test_no_adjustment_when_cost_below_threshold() -> None:
    """Test no adjustment when projected cost below threshold."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)

    # Low rate: 0.01% over 8 hours = 1 bps < 10 bps threshold
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.0001, next_ts=next_funding)

    ladders = create_sample_ladders()

    # Within window but cost below threshold
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Should be unchanged
    assert adjusted == ladders


def test_adjustment_when_cost_exceeds_threshold() -> None:
    """Test adjustment when projected cost exceeds threshold."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=5.0)

    # High rate: 0.1% over 8 hours = 10 bps > 5 bps threshold
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.001, next_ts=next_funding)

    ladders = create_sample_ladders()
    original_long_qty = ladders[0].total_qty()

    # Within window and cost exceeds threshold
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # LONG should be scaled
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    assert long_ladder.total_qty() < original_long_qty


def test_exactly_at_threshold_no_adjustment() -> None:
    """Test no adjustment when cost exactly at threshold."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=10.0)

    # Exact rate: 0.1% over 8 hours = 10 bps = 10 bps threshold
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.001, next_ts=next_funding)

    ladders = create_sample_ladders()

    # Within window but cost equals threshold
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Should be unchanged (cost <= threshold)
    assert adjusted == ladders


def test_high_cost_aggressive_reduction() -> None:
    """Test high funding cost leads to aggressive reduction."""
    guard = FundingGuard(window_minutes=480, max_cost_bps=5.0)

    # Very high rate: 1% over 8 hours = 100 bps >> 5 bps threshold
    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()

    # 10 minutes before funding (small scale factor)
    now = datetime(2024, 1, 1, 7, 50, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Should have very small quantities
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    scale_factor = 10 / 480  # ~0.021
    for i in range(len(long_ladder)):
        expected_qty = ladders[0][i].qty * scale_factor
        assert long_ladder[i].qty == pytest.approx(expected_qty, rel=0.01)


# Edge Cases


def test_no_funding_data_returns_unchanged() -> None:
    """Test returns unchanged ladders when no funding data available."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    ladders = create_sample_ladders()
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)

    # No funding update called
    adjusted = guard.adjust_ladders(ladders, now)

    # Should return unchanged
    assert adjusted == ladders


def test_very_short_time_until_funding() -> None:
    """Test behavior with very short time until funding."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()

    # 1 minute before funding
    now = datetime(2024, 1, 1, 7, 59, 0, tzinfo=UTC)
    adjusted = guard.adjust_ladders(ladders, now)

    # Should have very small quantities (1/60 = ~0.017)
    long_ladder = next(ladder for ladder in adjusted if ladder.side == Side.LONG)
    for rung in long_ladder:
        assert rung.qty < 0.01  # Very small


def test_funding_timestamp_in_past() -> None:
    """Test handles funding timestamp in the past."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    # Funding already passed
    next_funding = datetime(2024, 1, 1, 7, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    ladders = create_sample_ladders()
    now = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    adjusted = guard.adjust_ladders(ladders, now)

    # Should return unchanged
    assert adjusted == ladders


def test_empty_ladders() -> None:
    """Test handles empty ladders gracefully."""
    guard = FundingGuard(window_minutes=60, max_cost_bps=5.0)

    next_funding = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)
    guard.on_funding_update(rate=0.01, next_ts=next_funding)

    # Empty ladders
    empty_ladders: list[Ladder] = []
    now = datetime(2024, 1, 1, 7, 30, 0, tzinfo=UTC)

    adjusted = guard.adjust_ladders(empty_ladders, now)

    # Should return empty list
    assert adjusted == []
