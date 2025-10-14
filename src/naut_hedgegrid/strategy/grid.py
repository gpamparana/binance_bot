"""Grid construction and adaptive re-centering for hedge grid strategy."""

from naut_hedgegrid.config.strategy import HedgeGridConfig
from naut_hedgegrid.domain.types import Ladder, Regime, Rung, Side


class GridEngine:
    """
    Grid construction engine for building price ladders with adaptive re-centering.

    Constructs LONG and SHORT ladders with geometric quantity scaling,
    TP/SL levels, and inventory cap validation. Supports regime-based
    ladder selection for directional bias.
    """

    @staticmethod
    def build_ladders(
        mid: float,
        cfg: HedgeGridConfig,
        regime: Regime,
    ) -> list[Ladder]:
        """Build grid ladders around mid price based on config and regime.

        Args:
            mid: Mid price to center grid around
            cfg: Strategy configuration with grid parameters
            regime: Current market regime for directional bias

        Returns:
            List of Ladder instances (1-2 ladders depending on regime)
            - UP regime: [SHORT ladder] only
            - DOWN regime: [LONG ladder] only
            - SIDEWAYS regime: [LONG ladder, SHORT ladder]

        Raises:
            ValueError: If mid price invalid or inventory caps exceeded

        """
        if mid <= 0:
            msg = f"Mid price must be positive, got {mid}"
            raise ValueError(msg)

        # Calculate price step from basis points
        price_step = mid * (cfg.grid.grid_step_bps / 10000)

        # Build LONG ladder (below mid price)
        long_ladder = GridEngine._build_long_ladder(mid, price_step, cfg)

        # Build SHORT ladder (above mid price)
        short_ladder = GridEngine._build_short_ladder(mid, price_step, cfg)

        # Validate inventory caps
        GridEngine._validate_inventory_caps(long_ladder, short_ladder, mid, cfg)

        # Select ladders based on regime
        return GridEngine._select_ladders_by_regime(long_ladder, short_ladder, regime)

    @staticmethod
    def _build_long_ladder(
        mid: float,
        price_step: float,
        cfg: HedgeGridConfig,
    ) -> Ladder:
        """Build LONG ladder with levels below mid price.

        Args:
            mid: Mid price
            price_step: Price increment between levels
            cfg: Strategy configuration

        Returns:
            Ladder with LONG side rungs

        """
        rungs = []

        for level in range(1, cfg.grid.grid_levels_long + 1):
            # Price below mid
            price = mid - (level * price_step)

            # Geometric quantity scaling
            qty = cfg.grid.base_qty * (cfg.grid.qty_scale ** (level - 1))

            # TP above entry, SL below entry
            tp = None
            if cfg.exit.tp_steps > 0:
                tp = price + (cfg.exit.tp_steps * price_step)

            sl = None
            if cfg.exit.sl_steps > 0:
                sl = price - (cfg.exit.sl_steps * price_step)
                # Ensure SL is positive
                if sl <= 0:
                    sl = price * 0.01  # Minimum 1% of entry price

            rung = Rung(
                price=price,
                qty=qty,
                side=Side.LONG,
                tp=tp,
                sl=sl,
            )
            rungs.append(rung)

        return Ladder.from_list(Side.LONG, rungs)

    @staticmethod
    def _build_short_ladder(
        mid: float,
        price_step: float,
        cfg: HedgeGridConfig,
    ) -> Ladder:
        """Build SHORT ladder with levels above mid price.

        Args:
            mid: Mid price
            price_step: Price increment between levels
            cfg: Strategy configuration

        Returns:
            Ladder with SHORT side rungs

        """
        rungs = []

        for level in range(1, cfg.grid.grid_levels_short + 1):
            # Price above mid
            price = mid + (level * price_step)

            # Geometric quantity scaling
            qty = cfg.grid.base_qty * (cfg.grid.qty_scale ** (level - 1))

            # TP below entry, SL above entry
            tp = None
            if cfg.exit.tp_steps > 0:
                tp = price - (cfg.exit.tp_steps * price_step)
                # Ensure TP is positive
                if tp <= 0:
                    tp = price * 0.01  # Minimum 1% of entry price

            sl = None
            if cfg.exit.sl_steps > 0:
                sl = price + (cfg.exit.sl_steps * price_step)

            rung = Rung(
                price=price,
                qty=qty,
                side=Side.SHORT,
                tp=tp,
                sl=sl,
            )
            rungs.append(rung)

        return Ladder.from_list(Side.SHORT, rungs)

    @staticmethod
    def _validate_inventory_caps(
        long_ladder: Ladder,
        short_ladder: Ladder,
        mid: float,
        cfg: HedgeGridConfig,
    ) -> None:
        """Validate that ladders respect inventory caps.

        Args:
            long_ladder: LONG side ladder
            short_ladder: SHORT side ladder
            mid: Mid price for notional calculation
            cfg: Strategy configuration with max_inventory_quote

        Raises:
            ValueError: If either ladder exceeds max inventory

        """
        max_inventory = cfg.rebalance.max_inventory_quote

        # Calculate notional values
        long_notional = long_ladder.total_qty() * mid
        short_notional = short_ladder.total_qty() * mid

        if long_notional > max_inventory:
            msg = (
                f"LONG ladder exceeds max inventory: "
                f"{long_notional:.2f} > {max_inventory:.2f} quote currency. "
                f"Reduce grid_levels_long, base_qty, or qty_scale."
            )
            raise ValueError(msg)

        if short_notional > max_inventory:
            msg = (
                f"SHORT ladder exceeds max inventory: "
                f"{short_notional:.2f} > {max_inventory:.2f} quote currency. "
                f"Reduce grid_levels_short, base_qty, or qty_scale."
            )
            raise ValueError(msg)

    @staticmethod
    def _select_ladders_by_regime(
        long_ladder: Ladder,
        short_ladder: Ladder,
        regime: Regime,
    ) -> list[Ladder]:
        """Select ladders based on market regime.

        Args:
            long_ladder: LONG side ladder
            short_ladder: SHORT side ladder
            regime: Current market regime

        Returns:
            List of selected ladders
            - UP: [short_ladder] (sell into strength)
            - DOWN: [long_ladder] (buy into weakness)
            - SIDEWAYS: [long_ladder, short_ladder] (range trading)

        """
        if regime == Regime.UP:
            # Uptrend: favor SHORT ladder (sell into strength)
            return [short_ladder]
        if regime == Regime.DOWN:
            # Downtrend: favor LONG ladder (buy into weakness)
            return [long_ladder]
        # SIDEWAYS: both sides for range trading
        return [long_ladder, short_ladder]

    @staticmethod
    def recenter_needed(
        mid: float,
        last_center: float,
        cfg: HedgeGridConfig,
    ) -> bool:
        """Determine if grid needs re-centering based on price deviation.

        Args:
            mid: Current mid price
            last_center: Previous center price
            cfg: Strategy configuration with recenter_trigger_bps

        Returns:
            True if deviation exceeds threshold, False otherwise

        """
        if last_center == 0:
            return True  # Always recenter if no previous center

        # Calculate deviation in basis points
        deviation_bps = abs((mid - last_center) / last_center) * 10000

        # Check if exceeds threshold
        return deviation_bps > cfg.rebalance.recenter_trigger_bps
