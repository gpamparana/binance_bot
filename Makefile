.PHONY: help format lint test typecheck run install clean dev-install pre-commit-install all

# Default target
help:
	@echo "Naut HedgeGrid - Make targets:"
	@echo "  install            Install production dependencies"
	@echo "  dev-install        Install dev dependencies"
	@echo "  pre-commit-install Setup pre-commit hooks"
	@echo "  format             Format code with ruff"
	@echo "  lint               Lint code with ruff"
	@echo "  typecheck          Type check with mypy"
	@echo "  test               Run tests with pytest"
	@echo "  run                Run the application"
	@echo "  clean              Clean cache and build artifacts"
	@echo "  all                Run format, lint, typecheck, test"

# Install production dependencies
install:
	uv sync

# Install dev dependencies
dev-install:
	uv sync --all-extras

# Setup pre-commit hooks
pre-commit-install: dev-install
	uv run pre-commit install

# Format code with ruff
format:
	uv run ruff format .
	uv run ruff check --fix .

# Lint code with ruff
lint:
	uv run ruff check .

# Type check with mypy
typecheck:
	uv run mypy naut_hedgegrid/

# Run tests with pytest
test:
	uv run pytest tests/ -v

# Run the application (placeholder - customize as needed)
run:
	uv run python -m naut_hedgegrid

# Clean cache and build artifacts
clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache
	rm -rf **/__pycache__ **/*.pyc **/*.pyo
	rm -rf dist/ build/ *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

# Run all checks
all: format lint typecheck test
