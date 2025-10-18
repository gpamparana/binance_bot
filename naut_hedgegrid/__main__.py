"""Entry point for python -m naut_hedgegrid.

This module enables running the unified CLI via:
    python -m naut_hedgegrid <command> [options]

Examples:
    python -m naut_hedgegrid --help
    python -m naut_hedgegrid backtest --help
    python -m naut_hedgegrid paper --enable-ops
    python -m naut_hedgegrid live --enable-ops
    python -m naut_hedgegrid flatten --side LONG
    python -m naut_hedgegrid status
    python -m naut_hedgegrid metrics
"""

from naut_hedgegrid.cli import app

if __name__ == "__main__":
    app()
