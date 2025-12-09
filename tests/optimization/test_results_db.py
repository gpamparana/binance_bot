"""Tests for optimization results database."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from naut_hedgegrid.optimization.results_db import OptimizationResultsDB, OptimizationTrial


class TestOptimizationResultsDB:
    """Tests for OptimizationResultsDB."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        db = OptimizationResultsDB(db_path=db_path)
        yield db

        # Cleanup
        db_path.unlink(missing_ok=True)

    def test_database_creation(self, temp_db):
        """Test that database and schema are created."""
        assert temp_db.db_path.exists()

    def test_save_trial(self, temp_db):
        """Test saving a trial."""
        trial = OptimizationTrial(
            study_name="test_study",
            parameters={"grid": {"grid_step_bps": 50.0}},
            metrics={"sharpe_ratio": 2.5, "profit_factor": 2.0},
            score=0.85,
            is_valid=True,
            violations=[],
            timestamp=datetime.now(),
            duration_seconds=120.0,
        )

        trial_id = temp_db.save_trial(trial)

        assert trial_id > 0

    def test_save_multiple_trials(self, temp_db):
        """Test saving multiple trials."""
        for i in range(5):
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": 50.0 + i * 10}},
                metrics={"sharpe_ratio": 1.5 + i * 0.2, "profit_factor": 1.5 + i * 0.1},
                score=0.5 + i * 0.1,
                is_valid=True,
                violations=[],
                timestamp=datetime.now(),
                duration_seconds=100.0 + i * 10,
            )

            temp_db.save_trial(trial)

        # Verify all saved
        best_trials = temp_db.get_best_trials("test_study", n=10)
        assert len(best_trials) == 5

    def test_get_best_trials(self, temp_db):
        """Test retrieving best trials."""
        # Save trials with different scores
        scores = [0.8, 0.6, 0.9, 0.7, 0.5]

        for score in scores:
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": score * 100}},
                metrics={"sharpe_ratio": score * 3, "profit_factor": score * 2},
                score=score,
                is_valid=True,
                violations=[],
                timestamp=datetime.now(),
                duration_seconds=120.0,
            )
            temp_db.save_trial(trial)

        # Get top 3
        best_trials = temp_db.get_best_trials("test_study", n=3)

        assert len(best_trials) == 3
        assert best_trials[0]["score"] == 0.9  # Highest score first
        assert best_trials[1]["score"] == 0.8
        assert best_trials[2]["score"] == 0.7

    def test_get_best_trials_only_valid(self, temp_db):
        """Test filtering to only valid trials."""
        # Save mix of valid and invalid trials
        for i, is_valid in enumerate([True, False, True, False, True]):
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": 50.0}},
                metrics={"sharpe_ratio": 2.0, "profit_factor": 1.5},
                score=0.8,
                is_valid=is_valid,
                violations=[] if is_valid else ["Failed constraint"],
                timestamp=datetime.now(),
                duration_seconds=120.0,
            )
            temp_db.save_trial(trial)

        # Get only valid trials
        valid_trials = temp_db.get_best_trials("test_study", n=10, only_valid=True)

        assert len(valid_trials) == 3
        assert all(t["is_valid"] for t in valid_trials)

    def test_get_best_parameters(self, temp_db):
        """Test retrieving best parameters."""
        # Save trials
        for score in [0.7, 0.9, 0.8]:
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": score * 100}},
                metrics={"sharpe_ratio": score * 3, "profit_factor": score * 2},
                score=score,
                is_valid=True,
                violations=[],
                timestamp=datetime.now(),
                duration_seconds=120.0,
            )
            temp_db.save_trial(trial)

        # Get best parameters
        best_params = temp_db.get_best_parameters("test_study")

        assert best_params is not None
        assert best_params["grid"]["grid_step_bps"] == 90.0  # From score 0.9

    def test_export_to_csv(self, temp_db):
        """Test exporting results to CSV."""
        # Save some trials
        for i in range(3):
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": 50.0 + i * 10}},
                metrics={"sharpe_ratio": 2.0, "profit_factor": 1.5},
                score=0.8,
                is_valid=True,
                violations=[],
                timestamp=datetime.now(),
                duration_seconds=120.0,
            )
            temp_db.save_trial(trial)

        # Export to CSV
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            csv_path = Path(f.name)

        temp_db.export_to_csv("test_study", csv_path)

        assert csv_path.exists()
        assert csv_path.stat().st_size > 0

        # Cleanup
        csv_path.unlink(missing_ok=True)

    def test_get_study_stats(self, temp_db):
        """Test retrieving study statistics."""
        # Save trials
        for i in range(10):
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": 50.0}},
                metrics={"sharpe_ratio": 2.0 + i * 0.1, "max_drawdown_pct": 10.0},
                score=0.5 + i * 0.05,
                is_valid=i % 2 == 0,  # 5 valid, 5 invalid
                violations=[] if i % 2 == 0 else ["Failed"],
                timestamp=datetime.now(),
                duration_seconds=100.0,
            )
            temp_db.save_trial(trial)

        # Get stats
        stats = temp_db.get_study_stats("test_study")

        assert stats["study_name"] == "test_study"
        assert stats["total_trials"] == 10
        assert stats["valid_trials"] == 5
        assert stats["validity_rate"] == 0.5
        assert "avg_score" in stats
        assert "best_score" in stats

    def test_cleanup_old_trials(self, temp_db):
        """Test cleanup of old trials."""
        # Save 20 trials
        for i in range(20):
            trial = OptimizationTrial(
                study_name="test_study",
                parameters={"grid": {"grid_step_bps": 50.0}},
                metrics={"sharpe_ratio": 2.0, "profit_factor": 1.5},
                score=0.5 + i * 0.01,  # Ascending scores
                is_valid=True,
                violations=[],
                timestamp=datetime.now(),
                duration_seconds=120.0,
            )
            temp_db.save_trial(trial)

        # Cleanup, keeping only top 10
        temp_db.cleanup_old_trials("test_study", keep_top_n=10)

        # Verify only 10 remain
        trials = temp_db.get_best_trials("test_study", n=100, only_valid=False)
        assert len(trials) == 10

        # Verify they're the best 10 (scores range from 0.50 to 0.69)
        # Top 10 are indices 10-19: scores 0.60 to 0.69
        assert all(t["score"] >= 0.60 for t in trials)  # Top 10 scores

    def test_thread_safety(self, temp_db):
        """Test that database operations are thread-safe."""
        import threading

        def save_trials(db, study_name, n_trials):
            for i in range(n_trials):
                trial = OptimizationTrial(
                    study_name=study_name,
                    parameters={"grid": {"grid_step_bps": 50.0}},
                    metrics={"sharpe_ratio": 2.0, "profit_factor": 1.5},
                    score=0.8,
                    is_valid=True,
                    violations=[],
                    timestamp=datetime.now(),
                    duration_seconds=120.0,
                )
                db.save_trial(trial)

        # Create multiple threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=save_trials, args=(temp_db, "test_study", 10))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Verify all trials were saved
        stats = temp_db.get_study_stats("test_study")
        assert stats["total_trials"] == 50  # 5 threads * 10 trials each
