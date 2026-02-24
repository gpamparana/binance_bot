"""Tests for strategy state persistence (peak_balance, realized_pnl)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_strategy_stub(
    instrument_id: str = "BTCUSDT-PERP.BINANCE",
    is_backtest: bool = False,
    is_optimization: bool = False,
) -> MagicMock:
    """Create a minimal mock that exposes the persistence methods."""
    from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

    stub = MagicMock(spec=HedgeGridV1)
    stub.instrument_id = instrument_id
    stub._is_backtest_mode = is_backtest
    stub._is_optimization_mode = is_optimization
    stub._peak_balance = 0.0
    stub._realized_pnl = 0.0
    stub.log = MagicMock()

    # Bind real methods to stub
    stub._state_file_path = HedgeGridV1._state_file_path.__get__(stub)
    stub._load_persisted_state = HedgeGridV1._load_persisted_state.__get__(stub)
    stub._save_persisted_state = HedgeGridV1._save_persisted_state.__get__(stub)
    return stub


class TestStateFilePath:
    def test_returns_none_in_backtest_mode(self) -> None:
        stub = _make_strategy_stub(is_backtest=True)
        assert stub._state_file_path() is None

    def test_returns_none_in_optimization_mode(self) -> None:
        stub = _make_strategy_stub(is_optimization=True)
        assert stub._state_file_path() is None

    def test_returns_path_in_live_mode(self) -> None:
        stub = _make_strategy_stub()
        path = stub._state_file_path()
        assert path is not None
        assert "strategy_state_BTCUSDT-PERP_BINANCE" in path
        assert path.endswith(".json")


class TestSavePersistState:
    def test_save_creates_file(self, tmp_path: pytest.TempPathFactory) -> None:
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "artifacts" / "state.json")
        stub._state_file_path = MagicMock(return_value=state_file)
        stub._peak_balance = 12345.67
        stub._realized_pnl = 89.01

        stub._save_persisted_state()

        assert Path(state_file).exists()
        with open(state_file) as f:
            data = json.load(f)
        assert data["peak_balance"] == pytest.approx(12345.67)
        assert data["realized_pnl"] == pytest.approx(89.01)
        assert "last_saved" in data
        assert data["instrument_id"] == "BTCUSDT-PERP.BINANCE"

    def test_save_noop_in_backtest(self) -> None:
        stub = _make_strategy_stub(is_backtest=True)
        stub._save_persisted_state()
        # No file created, no error
        stub.log.warning.assert_not_called()

    def test_save_overwrites_existing(self, tmp_path: pytest.TempPathFactory) -> None:
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "state.json")
        stub._state_file_path = MagicMock(return_value=state_file)

        stub._peak_balance = 100.0
        stub._realized_pnl = 10.0
        stub._save_persisted_state()

        stub._peak_balance = 200.0
        stub._realized_pnl = 20.0
        stub._save_persisted_state()

        with open(state_file) as f:
            data = json.load(f)
        assert data["peak_balance"] == pytest.approx(200.0)
        assert data["realized_pnl"] == pytest.approx(20.0)


class TestLoadPersistState:
    def test_load_restores_values(self, tmp_path: pytest.TempPathFactory) -> None:
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "state.json")
        stub._state_file_path = MagicMock(return_value=state_file)

        # Write state file
        with open(state_file, "w") as f:
            json.dump({"peak_balance": 5000.0, "realized_pnl": -123.45}, f)

        stub._load_persisted_state()

        assert stub._peak_balance == pytest.approx(5000.0)
        assert stub._realized_pnl == pytest.approx(-123.45)
        stub.log.info.assert_called()

    def test_load_no_file_is_noop(self, tmp_path: pytest.TempPathFactory) -> None:
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "nonexistent.json")
        stub._state_file_path = MagicMock(return_value=state_file)

        stub._load_persisted_state()

        assert stub._peak_balance == 0.0
        assert stub._realized_pnl == 0.0
        stub.log.info.assert_called()  # "No persisted state file found"

    def test_load_noop_in_backtest(self) -> None:
        stub = _make_strategy_stub(is_backtest=True)
        stub._load_persisted_state()
        assert stub._peak_balance == 0.0

    def test_load_invalid_json_warns(self, tmp_path: pytest.TempPathFactory) -> None:
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "state.json")
        stub._state_file_path = MagicMock(return_value=state_file)

        with open(state_file, "w") as f:
            f.write("not valid json!")

        stub._load_persisted_state()

        assert stub._peak_balance == 0.0
        stub.log.warning.assert_called()

    def test_load_ignores_zero_peak_balance(self, tmp_path: pytest.TempPathFactory) -> None:
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "state.json")
        stub._state_file_path = MagicMock(return_value=state_file)

        with open(state_file, "w") as f:
            json.dump({"peak_balance": 0.0, "realized_pnl": 50.0}, f)

        stub._load_persisted_state()

        assert stub._peak_balance == 0.0  # Not restored (zero)
        assert stub._realized_pnl == pytest.approx(50.0)

    def test_round_trip(self, tmp_path: pytest.TempPathFactory) -> None:
        """Save then load should restore exact values."""
        stub = _make_strategy_stub()
        state_file = str(tmp_path / "artifacts" / "state.json")
        stub._state_file_path = MagicMock(return_value=state_file)

        stub._peak_balance = 9999.99
        stub._realized_pnl = -42.0
        stub._save_persisted_state()

        # Reset and reload
        stub._peak_balance = 0.0
        stub._realized_pnl = 0.0
        stub._load_persisted_state()

        assert stub._peak_balance == pytest.approx(9999.99)
        assert stub._realized_pnl == pytest.approx(-42.0)
