# naut-hedgegrid

Hedging grid trading system built on NautilusTrader.

## Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
# Install dependencies
uv sync --all-extras

# Install pre-commit hooks
uv run pre-commit install
```

## Development

```bash
# Format code
make format

# Lint code
make lint

# Type check
make typecheck

# Run tests
make test

# Run all checks
make all
```

## Project Structure

- `src/naut_hedgegrid/` - Main package source code
- `tests/` - Test files
- `pyproject.toml` - Project configuration and dependencies
- `ruff.toml` - Ruff linter/formatter configuration
- `mypy.ini` - MyPy type checker configuration
- `.pre-commit-config.yaml` - Pre-commit hooks configuration
- `.editorconfig` - Editor configuration

## Requirements

- Python 3.11+
- All dependencies managed via `pyproject.toml`
