# Docker Deployment Guide

This directory contains Docker configuration for containerizing the NautilusTrader HedgeGrid trading system.

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Services](#services)
- [Usage Examples](#usage-examples)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Production Deployment](#production-deployment)

## Quick Start

### Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+
- 4GB RAM minimum (8GB recommended)
- 10GB disk space for images and artifacts

### Build the Image

```bash
# From project root
docker compose build
```

### Run Backtest

```bash
# One-shot backtest execution
docker compose run --rm backtest

# View artifacts
ls -la artifacts/
```

### Start Paper Trading

```bash
# Start paper trading in background
docker compose --profile paper up -d

# View logs
docker compose logs -f paper

# Access Prometheus metrics
curl http://localhost:9090/metrics

# Access FastAPI docs
open http://localhost:8080/docs

# Stop paper trading
docker compose --profile paper down
```

## Architecture

### Multi-Stage Build

The Dockerfile uses a two-stage build pattern:

**Stage 1: Builder**
- Base: `python:3.12-slim`
- Installs uv package manager from official image
- Copies `pyproject.toml` and `uv.lock`
- Creates `.venv` with all dependencies
- Size: ~500MB

**Stage 2: Runtime**
- Base: `python:3.12-slim`
- Copies `.venv` from builder stage
- Copies application code
- Creates non-root user `nautilus` (UID 1000)
- Final size: ~200MB

**Benefits:**
- Fast rebuilds (cached layers)
- Minimal attack surface
- Security through non-root execution
- Optimized for production

### Directory Structure

```
/app/                      # Application root
├── .venv/                 # Python virtual environment (from builder)
├── src/                   # Application code
│   └── naut_hedgegrid/
├── configs/               # Configuration files (mounted)
│   ├── backtest/
│   ├── strategies/
│   └── venues/
├── data/                  # Parquet data catalogs (mounted)
└── artifacts/             # Output reports and logs (mounted)
```

### Volumes

| Volume | Purpose | Mount | Read/Write |
|--------|---------|-------|------------|
| `./data` | Parquet data catalogs | `/app/data` | Read-only for backtest, RW for live |
| `./artifacts` | Backtest reports, trading logs | `/app/artifacts` | Read-write |
| `./configs` | YAML configuration files | `/app/configs` | Read-only |

### Ports

| Port | Service | Purpose |
|------|---------|---------|
| 8080 | Paper/Live | FastAPI control endpoints |
| 8081 | Live | FastAPI (alternate port to avoid conflicts) |
| 9090 | Paper | Prometheus metrics |
| 9091 | Live | Prometheus (alternate port to avoid conflicts) |

## Services

### 1. Backtest Service

**Purpose:** One-shot backtest execution
**Profile:** `backtest`
**Restart:** No (one-shot)

**Features:**
- Loads data from Parquet catalog
- Runs Nautilus BacktestEngine
- Saves results to artifacts directory
- Resource limits: 2 CPU, 4GB RAM

**Command:**
```bash
docker compose run --rm backtest
```

**Output:**
```
artifacts/
└── 20241014_120000/          # Run ID (timestamp)
    ├── config.json           # Full configuration
    ├── summary.json          # Performance metrics
    ├── orders.csv            # All orders with fills
    ├── positions.csv         # Position history
    └── metrics.csv           # Performance metrics
```

### 2. Paper Trading Service

**Purpose:** Simulated execution with live market data
**Profile:** `paper`
**Restart:** `unless-stopped`

**Features:**
- Connects to Binance WebSocket for real-time data
- Simulates order fills locally (no real orders)
- Exports Prometheus metrics on port 9090
- FastAPI control endpoints on port 8080
- Health checks every 30 seconds
- Resource limits: 2 CPU, 4GB RAM

**Command:**
```bash
# Start in background
docker compose --profile paper up -d

# View logs
docker compose logs -f paper

# Stop
docker compose --profile paper down
```

**Operational Commands:**
```bash
# Query status
docker compose exec paper python -m naut_hedgegrid status

# Flatten positions
docker compose exec paper python -m naut_hedgegrid flatten --side LONG

# OR via HTTP API
curl http://localhost:8080/api/v1/status
curl -X POST http://localhost:8080/api/v1/flatten/long
```

### 3. Live Trading Service

**Purpose:** REAL execution with REAL money on Binance Futures
**Profile:** `live`
**Restart:** `unless-stopped`

**Features:**
- Connects to Binance for data AND execution
- Places REAL ORDERS with REAL MONEY
- Requires `BINANCE_API_KEY` and `BINANCE_API_SECRET`
- Exports Prometheus metrics on port 9091
- FastAPI control endpoints on port 8081
- Health checks every 30 seconds
- Resource limits: 4 CPU, 8GB RAM (more conservative)

**⚠️ WARNING: This service trades with real money!**

**Command:**
```bash
# Set API credentials first
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret

# Start in background
docker compose --profile live up -d

# View logs
docker compose logs -f live

# Stop (cancels all orders first)
docker compose --profile live down
```

## Usage Examples

### Example 1: Run Backtest with Custom Config

```bash
# Edit docker-compose.yml to change config paths, OR:

docker compose run --rm backtest \
  python -m naut_hedgegrid backtest \
  --backtest-config /app/configs/backtest/my_custom_config.yaml \
  --strategy-config /app/configs/strategies/my_strategy.yaml \
  --output-dir /app/artifacts/custom_run
```

### Example 2: Paper Trading with Custom Ports

```yaml
# In docker-compose.yml, modify paper service:
ports:
  - "19090:9090"  # Custom Prometheus port
  - "18080:8080"  # Custom API port
```

### Example 3: Monitor Multiple Services

```bash
# Terminal 1: Paper trading
docker compose --profile paper up

# Terminal 2: Live trading (separate ports)
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret
docker compose --profile live up

# Terminal 3: Monitor both
watch -n 5 'curl -s http://localhost:8080/api/v1/status && curl -s http://localhost:8081/api/v1/status'
```

### Example 4: Backtest Multiple Strategies

```bash
# Create custom docker-compose override
cat > docker-compose.override.yml <<EOF
services:
  backtest-strategy-a:
    extends:
      service: backtest
    container_name: backtest-a
    command: >
      python -m naut_hedgegrid backtest
      --strategy-config /app/configs/strategies/strategy_a.yaml
    profiles:
      - backtest-a

  backtest-strategy-b:
    extends:
      service: backtest
    container_name: backtest-b
    command: >
      python -m naut_hedgegrid backtest
      --strategy-config /app/configs/strategies/strategy_b.yaml
    profiles:
      - backtest-b
EOF

# Run both
docker compose --profile backtest-a run --rm backtest-strategy-a &
docker compose --profile backtest-b run --rm backtest-strategy-b &
wait
```

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
# Binance API Credentials (required for live trading)
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Optional: Logging level
LOG_LEVEL=INFO

# Optional: Build metadata
BUILD_DATE=2024-01-01
VCS_REF=main
VERSION=0.1.0
```

**Load automatically:**
```bash
docker compose --profile live up -d
```

### Resource Limits

Adjust in `docker-compose.yml`:

```yaml
services:
  paper:
    deploy:
      resources:
        limits:
          cpus: '4'      # Max 4 CPU cores
          memory: 8G     # Max 8GB RAM
        reservations:
          cpus: '2'      # Guaranteed 2 cores
          memory: 4G     # Guaranteed 4GB
```

### Logging Configuration

Control log rotation:

```yaml
services:
  paper:
    logging:
      driver: "json-file"
      options:
        max-size: "20m"    # Rotate after 20MB
        max-file: "10"     # Keep 10 files (200MB total)
```

## Troubleshooting

### Build Issues

**Problem:** `uv: command not found`
```bash
# Solution: Update base image
docker pull python:3.12-slim
docker pull ghcr.io/astral-sh/uv:latest
docker compose build --no-cache
```

**Problem:** Dependency resolution fails
```bash
# Solution: Update uv.lock locally first
uv sync
docker compose build
```

### Runtime Issues

**Problem:** `Permission denied` on volumes
```bash
# Solution: Fix ownership (host user should match UID 1000)
sudo chown -R 1000:1000 data/ artifacts/ configs/
# OR change Dockerfile to match your UID:
# RUN useradd -r -u $(id -u) -g nautilus nautilus
```

**Problem:** `Cannot connect to Binance`
```bash
# Check DNS resolution inside container
docker compose exec paper ping -c 3 fapi.binance.com

# Check API credentials
docker compose exec paper env | grep BINANCE

# View detailed logs
docker compose logs --tail=100 paper
```

**Problem:** Health check failing
```bash
# Check if API server is running
docker compose exec paper curl http://localhost:8080/health

# Check ports inside container
docker compose exec paper netstat -tulpn

# Disable health check temporarily
# Comment out healthcheck section in docker-compose.yml
```

### Performance Issues

**Problem:** High CPU usage
```bash
# Check container stats
docker stats hedgegrid-paper

# Reduce resource limits
# Edit deploy.resources.limits in docker-compose.yml
```

**Problem:** Out of memory
```bash
# Check memory usage
docker stats hedgegrid-paper

# Increase memory limit
# Edit deploy.resources.limits.memory in docker-compose.yml

# OR restart container
docker compose --profile paper restart
```

## Production Deployment

### 1. Security Hardening

**Use secrets management:**
```bash
# Docker Swarm secrets
echo "your_api_key" | docker secret create binance_api_key -
echo "your_secret" | docker secret create binance_api_secret -
```

**Update docker-compose.yml:**
```yaml
services:
  live:
    secrets:
      - binance_api_key
      - binance_api_secret
    environment:
      - BINANCE_API_KEY=/run/secrets/binance_api_key
      - BINANCE_API_SECRET=/run/secrets/binance_api_secret

secrets:
  binance_api_key:
    external: true
  binance_api_secret:
    external: true
```

### 2. Monitoring Setup

**Prometheus scrape config:**
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'hedgegrid-paper'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'hedgegrid-live'
    static_configs:
      - targets: ['localhost:9091']
```

**Grafana dashboard:**
- Import pre-built dashboard for trading metrics
- Monitor: PnL, position size, maker ratio, funding costs

### 3. Log Aggregation

**Use centralized logging:**
```yaml
services:
  live:
    logging:
      driver: "syslog"
      options:
        syslog-address: "tcp://logs.example.com:514"
        tag: "hedgegrid-live"
```

### 4. High Availability

**Use Docker Swarm or Kubernetes:**
```bash
# Docker Swarm
docker stack deploy -c docker-compose.yml hedgegrid

# Kubernetes (requires conversion)
kompose convert
kubectl apply -f .
```

### 5. Backup Strategy

**Automated backups:**
```bash
# Backup artifacts daily
0 2 * * * tar -czf /backups/artifacts-$(date +\%Y\%m\%d).tar.gz ./artifacts/

# Backup configs
0 2 * * * tar -czf /backups/configs-$(date +\%Y\%m\%d).tar.gz ./configs/
```

## Advanced Topics

### Custom Entrypoint Script

Create `docker/entrypoint.sh`:
```bash
#!/bin/bash
set -e

# Wait for dependencies
echo "Waiting for services..."
sleep 5

# Run migrations or setup
# python -m naut_hedgegrid setup

# Execute main command
exec "$@"
```

Update Dockerfile:
```dockerfile
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "naut_hedgegrid", "--help"]
```

### Multi-Architecture Builds

Build for ARM64 and AMD64:
```bash
docker buildx create --use
docker buildx build --platform linux/amd64,linux/arm64 -t naut-hedgegrid:latest .
```

### Container Registry

Push to private registry:
```bash
docker tag naut-hedgegrid:latest registry.example.com/trading/naut-hedgegrid:latest
docker push registry.example.com/trading/naut-hedgegrid:latest
```

## Support

For issues related to:
- **Docker configuration**: Check this README and troubleshooting section
- **Trading system**: Check main project README.md
- **NautilusTrader**: Visit https://nautilustrader.io/docs

## License

Same as main project (see LICENSE file in repository root)
