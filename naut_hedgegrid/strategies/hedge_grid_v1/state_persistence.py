"""State persistence mixin for HedgeGridV1 strategy.

Provides atomic save/load of peak_balance and realized_pnl to disk
for live/paper trading modes. Backtest and optimization modes skip persistence.

Expected attributes on self (initialized in HedgeGridV1.__init__):
    _peak_balance: float
    _realized_pnl: float
    _is_backtest_mode: bool
    _is_optimization_mode: bool
    instrument_id: InstrumentId
    log: Logger (from Strategy base)
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class StatePersistenceMixin:
    """Mixin providing disk-based state persistence for strategy risk tracking."""

    def _state_file_path(self) -> str | None:
        """Return path for persisted state file, or None in backtest/optimization mode."""
        if self._is_backtest_mode or self._is_optimization_mode:
            return None

        artifacts_dir = Path.cwd() / "artifacts"
        safe_id = str(self.instrument_id).replace(".", "_").replace("/", "_")
        return str(artifacts_dir / f"strategy_state_{safe_id}.json")

    def _load_persisted_state(self) -> None:
        """Load persisted peak_balance and realized_pnl from disk.

        Only runs in live/paper mode. Failures are non-fatal (log warning, continue
        with defaults).
        """
        path = self._state_file_path()
        if path is None:
            return

        if not Path(path).exists():
            self.log.info("No persisted state file found, starting fresh")
            return

        try:
            with open(path) as f:
                data = json.load(f)

            if not isinstance(data, dict):
                self.log.warning(f"Invalid persisted state format, ignoring: {path}")
                return

            restored_peak = data.get("peak_balance", 0.0)
            restored_pnl = data.get("realized_pnl", 0.0)

            if isinstance(restored_peak, int | float) and restored_peak > 0:
                self._peak_balance = float(restored_peak)
            if isinstance(restored_pnl, int | float):
                self._realized_pnl = float(restored_pnl)

            self.log.info(
                f"Restored persisted state: peak_balance={self._peak_balance:.2f}, "
                f"realized_pnl={self._realized_pnl:.2f}"
            )
        except Exception as e:
            self.log.warning(f"Failed to load persisted state from {path}: {e}")

    def _save_persisted_state(self) -> None:
        """Save peak_balance and realized_pnl to disk atomically.

        Only runs in live/paper mode. Failures are non-fatal.
        """
        path = self._state_file_path()
        if path is None:
            return

        try:
            artifacts_dir = Path(path).parent
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            data = {
                "peak_balance": self._peak_balance,
                "realized_pnl": self._realized_pnl,
                "last_saved": datetime.now(tz=UTC).isoformat(),
                "instrument_id": str(self.instrument_id),
            }

            # Atomic write via temp file + rename
            fd, tmp_path = tempfile.mkstemp(dir=str(artifacts_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                Path(tmp_path).replace(path)
            except BaseException:
                # Clean up temp file on failure
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass
                raise
        except Exception as e:
            self.log.warning(f"Failed to save persisted state to {path}: {e}")
