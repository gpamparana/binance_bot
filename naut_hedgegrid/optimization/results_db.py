"""SQLite database for storing optimization results.

This module provides persistent storage for optimization trials,
enabling result analysis, best parameter retrieval, and experiment tracking.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel


class OptimizationTrial(BaseModel):
    """Represents a single optimization trial."""

    trial_id: int | None = None
    study_name: str
    parameters: dict[str, Any]
    metrics: dict[str, float]
    score: float
    is_valid: bool
    violations: list[str]
    timestamp: datetime
    duration_seconds: float
    error_message: str | None = None


class OptimizationResultsDB:
    """
    SQLite database for storing optimization results.

    This class provides thread-safe storage and retrieval of optimization
    trials, with support for querying best results, exporting to CSV,
    and tracking experiment history.

    The database schema includes:
    - trials: Main table storing all trial results
    - studies: Metadata about optimization studies
    - best_params: Cache of best parameters per study

    Attributes
    ----------
    db_path : Path
        Path to SQLite database file
    _local : threading.local
        Thread-local storage for connections
    """

    def __init__(self, db_path: Path | None = None):
        """
        Initialize optimization results database.

        Parameters
        ----------
        db_path : Path, optional
            Path to database file (defaults to ./optimization_results.db)
        """
        self.db_path = db_path or Path("optimization_results.db")
        self._local = threading.local()
        self._lock = threading.Lock()
        self._create_schema()

    @contextmanager
    def _get_connection(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                isolation_level="DEFERRED",  # Better concurrency than IMMEDIATE
            )
            self._local.conn.row_factory = sqlite3.Row

        try:
            yield self._local.conn
        except Exception as e:
            self._local.conn.rollback()
            raise e
        else:
            self._local.conn.commit()

    def _create_schema(self):
        """Create database schema if it doesn't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Create trials table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_name TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    score REAL NOT NULL,
                    is_valid BOOLEAN NOT NULL,
                    violations TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    duration_seconds REAL,
                    error_message TEXT,

                    -- Denormalized key metrics for fast queries
                    sharpe_ratio REAL,
                    profit_factor REAL,
                    calmar_ratio REAL,
                    max_drawdown_pct REAL,
                    total_trades INTEGER,
                    win_rate_pct REAL,
                    total_return_pct REAL
                )
            """)

            # Create indices for fast queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_study_name
                ON trials(study_name)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_score
                ON trials(score DESC)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON trials(timestamp DESC)
            """)

            # Create studies metadata table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS studies (
                    name TEXT PRIMARY KEY,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_trials INTEGER DEFAULT 0,
                    best_score REAL,
                    best_params TEXT,
                    config TEXT
                )
            """)

            # Create best parameters cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS best_params (
                    study_name TEXT PRIMARY KEY,
                    parameters TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    score REAL NOT NULL,
                    trial_id INTEGER,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trial_id) REFERENCES trials(id)
                )
            """)

            conn.commit()

    def save_trial(self, trial: OptimizationTrial) -> int:
        """
        Save optimization trial to database.

        Parameters
        ----------
        trial : OptimizationTrial
            Trial data to save

        Returns
        -------
        int
            ID of saved trial
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Extract key metrics for denormalization
                metrics = trial.metrics
                sharpe = metrics.get("sharpe_ratio")
                profit = metrics.get("profit_factor")
                calmar = metrics.get("calmar_ratio")
                max_dd = metrics.get("max_drawdown_pct")
                trades = metrics.get("total_trades")
                win_rate = metrics.get("win_rate_pct")
                total_ret = metrics.get("total_return_pct")

                # Insert trial
                cursor.execute(
                    """
                    INSERT INTO trials (
                        study_name, parameters, metrics, score, is_valid,
                        violations, timestamp, duration_seconds, error_message,
                        sharpe_ratio, profit_factor, calmar_ratio, max_drawdown_pct,
                        total_trades, win_rate_pct, total_return_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        trial.study_name,
                        json.dumps(trial.parameters),
                        json.dumps(trial.metrics),
                        trial.score,
                        trial.is_valid,
                        json.dumps(trial.violations) if trial.violations else None,
                        trial.timestamp,
                        trial.duration_seconds,
                        trial.error_message,
                        sharpe,
                        profit,
                        calmar,
                        max_dd,
                        trades,
                        win_rate,
                        total_ret,
                    ),
                )

                trial_id = cursor.lastrowid

                # Update study metadata
                cursor.execute(
                    """
                    INSERT INTO studies (name, total_trials, best_score)
                    VALUES (?, 1, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        updated_at = CURRENT_TIMESTAMP,
                        total_trials = total_trials + 1,
                        best_score = MAX(best_score, excluded.best_score)
                """,
                    (trial.study_name, trial.score),
                )

                # Update best params if this is the best trial
                cursor.execute(
                    """
                    SELECT score FROM best_params WHERE study_name = ?
                """,
                    (trial.study_name,),
                )

                row = cursor.fetchone()
                if row is None or trial.score > row[0]:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO best_params
                        (study_name, parameters, metrics, score, trial_id)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (
                            trial.study_name,
                            json.dumps(trial.parameters),
                            json.dumps(trial.metrics),
                            trial.score,
                            trial_id,
                        ),
                    )

                conn.commit()
                return trial_id

    def get_best_trials(
        self, study_name: str, n: int = 10, only_valid: bool = True
    ) -> list[dict[str, Any]]:
        """
        Get top N best trials for a study.

        Parameters
        ----------
        study_name : str
            Name of optimization study
        n : int
            Number of top trials to retrieve
        only_valid : bool
            If True, only return trials that met constraints

        Returns
        -------
        List[Dict[str, Any]]
            List of best trials with parameters and metrics
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            validity_filter = "AND is_valid = 1" if only_valid else ""

            cursor.execute(
                f"""
                SELECT
                    id, parameters, metrics, score, is_valid,
                    violations, timestamp, duration_seconds,
                    sharpe_ratio, profit_factor, calmar_ratio,
                    max_drawdown_pct, total_trades, win_rate_pct
                FROM trials
                WHERE study_name = ? {validity_filter}
                ORDER BY score DESC
                LIMIT ?
            """,
                (study_name, n),
            )

            trials = []
            for row in cursor.fetchall():
                trials.append(
                    {
                        "id": row["id"],
                        "parameters": json.loads(row["parameters"]),
                        "metrics": json.loads(row["metrics"]),
                        "score": row["score"],
                        "is_valid": bool(row["is_valid"]),
                        "violations": json.loads(row["violations"]) if row["violations"] else [],
                        "timestamp": row["timestamp"],
                        "duration_seconds": row["duration_seconds"],
                    }
                )

            return trials

    def get_best_parameters(self, study_name: str) -> dict[str, Any] | None:
        """
        Get best parameters for a study.

        Parameters
        ----------
        study_name : str
            Name of optimization study

        Returns
        -------
        Dict[str, Any] or None
            Best parameters if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT parameters FROM best_params
                WHERE study_name = ?
            """,
                (study_name,),
            )

            row = cursor.fetchone()
            if row:
                return json.loads(row["parameters"])
            return None

    def export_to_csv(self, study_name: str, output_path: Path):
        """
        Export study results to CSV file.

        Parameters
        ----------
        study_name : str
            Name of optimization study
        output_path : Path
            Path for output CSV file
        """
        with self._get_connection() as conn:
            # Read trials into DataFrame
            query = """
                SELECT
                    id, score, is_valid, timestamp, duration_seconds,
                    sharpe_ratio, profit_factor, calmar_ratio,
                    max_drawdown_pct, total_trades, win_rate_pct,
                    total_return_pct, parameters
                FROM trials
                WHERE study_name = ?
                ORDER BY score DESC
            """

            df = pd.read_sql_query(query, conn, params=(study_name,))

            # Expand parameters JSON into columns
            if not df.empty:
                params_df = pd.json_normalize(df["parameters"].apply(json.loads))
                df = pd.concat([df.drop("parameters", axis=1), params_df], axis=1)

            # Save to CSV
            df.to_csv(output_path, index=False)

    def get_study_stats(self, study_name: str) -> dict[str, Any]:
        """
        Get statistics for an optimization study.

        Parameters
        ----------
        study_name : str
            Name of optimization study

        Returns
        -------
        Dict[str, Any]
            Study statistics including trial count, best score, etc.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get study metadata
            cursor.execute(
                """
                SELECT * FROM studies WHERE name = ?
            """,
                (study_name,),
            )

            study_row = cursor.fetchone()
            if not study_row:
                return {"error": f"Study {study_name} not found"}

            # Get trial statistics
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_trials,
                    COUNT(CASE WHEN is_valid = 1 THEN 1 END) as valid_trials,
                    AVG(score) as avg_score,
                    MAX(score) as best_score,
                    MIN(score) as worst_score,
                    AVG(duration_seconds) as avg_duration,
                    AVG(sharpe_ratio) as avg_sharpe,
                    AVG(max_drawdown_pct) as avg_drawdown
                FROM trials
                WHERE study_name = ?
            """,
                (study_name,),
            )

            stats_row = cursor.fetchone()

            return {
                "study_name": study_name,
                "created_at": study_row["created_at"],
                "updated_at": study_row["updated_at"],
                "total_trials": stats_row["total_trials"],
                "valid_trials": stats_row["valid_trials"],
                "validity_rate": stats_row["valid_trials"] / max(1, stats_row["total_trials"]),
                "best_score": stats_row["best_score"],
                "worst_score": stats_row["worst_score"],
                "avg_score": stats_row["avg_score"],
                "avg_duration_seconds": stats_row["avg_duration"],
                "avg_sharpe": stats_row["avg_sharpe"],
                "avg_drawdown_pct": stats_row["avg_drawdown"],
            }

    def cleanup_old_trials(self, study_name: str, keep_top_n: int = 100):
        """
        Remove old trials keeping only top N best.

        Parameters
        ----------
        study_name : str
            Name of optimization study
        keep_top_n : int
            Number of top trials to keep
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get IDs of trials to keep
                cursor.execute(
                    """
                    SELECT id FROM trials
                    WHERE study_name = ?
                    ORDER BY score DESC
                    LIMIT ?
                """,
                    (study_name, keep_top_n),
                )

                keep_ids = [row[0] for row in cursor.fetchall()]

                if keep_ids:
                    # Delete trials not in keep list
                    placeholders = ",".join("?" * len(keep_ids))
                    cursor.execute(
                        f"""
                        DELETE FROM trials
                        WHERE study_name = ? AND id NOT IN ({placeholders})
                    """,
                        [study_name] + keep_ids,
                    )

                conn.commit()
