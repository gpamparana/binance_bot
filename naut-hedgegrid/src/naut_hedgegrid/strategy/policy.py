"""Placement policies for regime-based inventory biasing."""

from naut_hedgegrid.config.strategy import HedgeGridConfig
from naut_hedgegrid.domain.types import Ladder, Regime, Rung, Side


class PlacementPolicy:
    """
    Placement policy engine for biasing inventory by market regime.

    Applies throttling to counter-trend ladders to manage directional risk:
    - "core-and-scalp": Maintain thin ladders on both sides (market-making)
    - "throttled-counter": Full ladder with trend, thin ladder against trend

    Works by mutating ladder rung counts or quantities after grid construction.
    """

    @staticmethod
    def shape_ladders(
        ladders: list[Ladder],
        regime: Regime,
        cfg: HedgeGridConfig,
    ) -> list[Ladder]:
        """Apply placement policy to bias inventory by regime.

        Args:
            ladders: Ladders from GridEngine.build_ladders()
            regime: Current market regime
            cfg: Strategy configuration with policy settings

        Returns:
            Shaped ladders with policy applied (new instances, immutable)

        Notes:
            - SIDEWAYS regime: Returns ladders unchanged (balanced)
            - UP regime: Throttles LONG side (counter-trend)
            - DOWN regime: Throttles SHORT side (counter-trend)
            - Both strategies use same throttling logic, differ only in intent

        """
        # SIDEWAYS: No throttling needed, balanced on both sides
        if regime == Regime.SIDEWAYS:
            return ladders

        # Determine which side is counter-trend based on regime
        counter_side = PlacementPolicy._get_counter_side(regime)

        # Apply throttling to counter-trend ladder
        shaped = []
        for ladder in ladders:
            if ladder.side == counter_side:
                # Throttle counter-trend ladder
                throttled = PlacementPolicy._throttle_ladder(ladder, cfg)
                shaped.append(throttled)
            else:
                # Keep trend-following ladder unchanged
                shaped.append(ladder)

        return shaped

    @staticmethod
    def _get_counter_side(regime: Regime) -> Side:
        """Determine counter-trend side for given regime.

        Args:
            regime: Current market regime

        Returns:
            Side that is counter-trend (LONG for UP, SHORT for DOWN)

        """
        if regime == Regime.UP:
            # In uptrend, LONG is counter-trend (buying into strength)
            return Side.LONG
        if regime == Regime.DOWN:
            # In downtrend, SHORT is counter-trend (selling into weakness)
            return Side.SHORT
        # SIDEWAYS: This shouldn't be called, but return LONG as default
        return Side.LONG

    @staticmethod
    def _throttle_ladder(ladder: Ladder, cfg: HedgeGridConfig) -> Ladder:
        """Throttle ladder by limiting levels and scaling quantities.

        Args:
            ladder: Ladder to throttle
            cfg: Configuration with counter_levels and counter_qty_scale

        Returns:
            New throttled ladder (immutable)

        """
        policy_cfg = cfg.policy

        # If counter_levels is 0, return empty ladder
        if policy_cfg.counter_levels == 0:
            return Ladder(side=ladder.side, rungs=())

        # Step 1: Truncate to counter_levels (keep closest to mid)
        # Assumption: Ladders from GridEngine are already sorted with closest rungs first
        truncated_rungs = list(ladder.rungs[: policy_cfg.counter_levels])

        # Step 2: Scale quantities by counter_qty_scale
        if policy_cfg.counter_qty_scale < 1.0:
            scaled_rungs = [
                Rung(
                    price=rung.price,
                    qty=rung.qty * policy_cfg.counter_qty_scale,
                    side=rung.side,
                    tp=rung.tp,
                    sl=rung.sl,
                    tag=rung.tag,
                )
                for rung in truncated_rungs
            ]
        else:
            scaled_rungs = truncated_rungs

        return Ladder.from_list(ladder.side, scaled_rungs)
