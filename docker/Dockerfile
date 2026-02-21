# Multi-stage Dockerfile for NautilusTrader HedgeGrid Trading System
#
# This Dockerfile uses a multi-stage build pattern for optimal image size and security:
# - Builder stage: Installs uv and builds dependencies
# - Runtime stage: Minimal production image with non-root user
#
# Build: docker compose build
# Run: docker compose run --rm backtest

# ==============================================================================
# Stage 1: Builder - Install dependencies with uv
# ==============================================================================

FROM python:3.12-slim AS builder

# Set environment variables for build
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files first (better layer caching)
COPY pyproject.toml ./
COPY uv.lock* ./

# Install dependencies into .venv
# --frozen: Use exact versions from uv.lock
# --no-dev: Skip development dependencies
RUN uv sync --frozen --no-dev || uv sync --no-dev

# ==============================================================================
# Stage 2: Runtime - Minimal production image
# ==============================================================================

FROM python:3.12-slim AS runtime

# Set environment variables for runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app:$PYTHONPATH"

# Install system dependencies (currently none required)
# Uncomment if TA-Lib or other system libs are needed in the future:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     libgomp1 \
#     libta-lib0 \
#     && rm -rf /var/lib/apt/lists/*

# Create non-root user and group
# Using UID/GID 1000 for compatibility with most development environments
RUN groupadd -r -g 1000 nautilus && \
    useradd -r -u 1000 -g nautilus -m -s /bin/bash nautilus

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder --chown=nautilus:nautilus /app/.venv /app/.venv

# Copy application code
COPY --chown=nautilus:nautilus naut_hedgegrid/ /app/naut_hedgegrid/
COPY --chown=nautilus:nautilus pyproject.toml /app/
COPY --chown=nautilus:nautilus configs/ /app/configs/

# Create directories for mounted volumes
# These will be overridden by docker-compose volumes
RUN mkdir -p /app/data /app/artifacts /app/reports && \
    chown -R nautilus:nautilus /app

# Switch to non-root user
USER nautilus

# Expose ports for Prometheus metrics and FastAPI control
EXPOSE 8080 9090

# Health check (requires FastAPI server to be running)
# Disabled by default since backtest service doesn't run a server
# HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
#   CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=5)"

# Default command - shows CLI help
# Override this in docker-compose.yml for each service
CMD ["python", "-m", "naut_hedgegrid", "--help"]

# ==============================================================================
# Build Arguments and Labels (for metadata)
# ==============================================================================

ARG BUILD_DATE
ARG VCS_REF
ARG VERSION

LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.authors="Giovanni <giopampax@gmail.com>" \
      org.opencontainers.image.url="https://github.com/your-org/naut-hedgegrid" \
      org.opencontainers.image.source="https://github.com/your-org/naut-hedgegrid" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.title="NautilusTrader HedgeGrid" \
      org.opencontainers.image.description="Hedge-mode grid trading system built on NautilusTrader" \
      org.opencontainers.image.licenses="MIT"
