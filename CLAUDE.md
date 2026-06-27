# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **teaching project** for building an active ETF data crawler system. The planned architecture is:

```
Scheduler → Producer → RabbitMQ → Worker (FinMind API) → MySQL / BigQuery
```

The source files in the README (`worker.py`, `tasks.py`, `producer.py`, etc.) are the **intended target** of development — the repo currently has a placeholder `main.py`. When implementing new files, follow the folder layout described in the README under `crawler/`.

## Package Management

This project uses **uv** (not pip or poetry).

```bash
# Install dependencies from lock file
uv sync

# Add a package
uv add <package>

# Run a script (picks up the venv automatically)
uv run python crawler/<script>.py

# Run with a .env file
uv run --env-file=.env python crawler/<script>.py
```

## Running Components Locally

```bash
# Start a Celery worker (default queue)
uv run celery -A crawler.worker worker --loglevel=info

# Start a worker for specific queues
uv run celery -A crawler.worker worker -Q twse,tpex --loglevel=info

# Send tasks via producer
uv run python crawler/producer.py
uv run python crawler/producer_multi_queue.py

# Generate .env from local.ini (ENV = DEV | DOCKER | PRODUCTION)
ENV=DEV python genenv.py
```

## Docker Compose Stack

Docker Compose files follow a naming convention:
- **`-network`**: uses external `my_network` (must run `docker network create my_network` once)
- **no `-network`**: uses an isolated `dev` network defined inside the compose file
- **`-version`**: image version is controlled by `DOCKER_IMAGE_VERSION` env var
- **`-duplicate`**: uses the upsert (on_duplicate_key_update) task variant

Typical startup order:

```bash
docker network create my_network   # once
docker compose -f rabbitmq-network.yml up -d
docker compose -f mysql.yml up -d
DOCKER_IMAGE_VERSION=0.0.6 docker compose -f docker-compose-worker-network-version.yml up -d
DOCKER_IMAGE_VERSION=0.0.6 docker compose -f docker-compose-scheduler-network-version.yml up -d
```

Monitoring UIs: Flower at `http://localhost:5555`, phpMyAdmin at `http://localhost:8000`.

## Dockerfiles

| File | Purpose |
|------|---------|
| `Dockerfile` | Base; no `.env` baked in — pass env vars at runtime |
| `with.env.Dockerfile` | Runs `ENV=DOCKER genenv.py` at build time |
| `prod.with.env.Dockerfile` | Runs `ENV=PRODUCTION genenv.py` at build time |

All Dockerfiles use `uv sync --frozen` to pin exact versions from `uv.lock`.

## Architecture of Key Source Files (planned)

| File | Role |
|------|------|
| `crawler/config.py` | Centralised env var management — read this first |
| `crawler/worker.py` | Creates the Celery app instance |
| `crawler/tasks.py` | Minimal example Celery task |
| `crawler/tasks_crawler_finmind.py` | Real crawler task; append mode |
| `crawler/tasks_crawler_finmind_duplicate.py` | Same, but upsert mode |
| `crawler/producer.py` | Simplest task dispatch example |
| `crawler/producer_crawler_finmind.py` | Batch dispatch with a for-loop |
| `crawler/producer_multi_queue.py` | Routes tasks to `twse` / `tpex` queues |
| `crawler/scheduler.py` | APScheduler-based automatic dispatch |
| `crawler/upload_*.py` | One-off data upload scripts (MySQL, BigQuery) |

## Code Formatting

```bash
black -l 80 crawler/
```

## Cloud / External Services

- **FinMind API** — source of Taiwan stock price data
- **MySQL** — relational storage for crawled data (local: Docker, port 3306)
- **Google BigQuery** — cloud warehouse for large historical datasets
- **Google Cloud Secret Manager** — stores credentials; never hardcode them
- **RabbitMQ** — message broker for Celery (local: Docker, AMQP 5672)

GCP setup:
```bash
gcloud auth application-default login
gcloud config set project <project-id>
```
