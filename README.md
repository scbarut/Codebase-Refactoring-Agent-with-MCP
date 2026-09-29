# Autonomous Codebase Refactoring & Migration Agent with MCP

An autonomous agent that ingests a Python codebase, identifies deprecated library usages, proposes a migration plan for human approval via HITL gateways, rewrites code using deterministic libcst transformers and LLM fallback, and self-heals test regressions within isolated Docker sandboxes.

## Architecture Stack

- **LangGraph Orchestration**: Top-level lifecycle graph with episodic nested sub-graphs per file.
- **FastAPI Backend**: REST API and WebSocket event streaming with structured JSON logging (`structlog`).
- **Next.js Web UI**: Dashboard, Plan Review with selective approval, Live Progress logs, and Monaco split-diff viewer.
- **PostgreSQL Checkpointer**: Managed via Docker Compose for persistent state and interrupt handling.
- **Docker Sandbox**: Multi-version Python (3.9–3.12) ephemeral test runners pre-installed with pytest and coverage.
- **Model Context Protocol (MCP)**: Decoupled tool servers for AST transforms, Doc retrieval, and Git workspace operations.

## Security Notice: Docker Socket Mount

The API container mounts the host Docker socket (`/var/run/docker.sock`) to dynamically spawn isolated Sandbox containers for running test suites against migrated code.

> [!WARNING]
> Mounting the Docker socket grants the container equivalent administrative control over the host Docker daemon. In multi-tenant or untrusted environments, we strongly recommend using rootless Docker or Podman as an alternative daemonless runtime to maintain non-root process boundaries.

## Quick Start

### 1. Prerequisites

- Python `>=3.11`
- Docker & Docker Compose
- `uv` (recommended for Python package management)
- Node.js `>=20` (for local frontend development)

### 2. Configuration

Copy the example environment and config files:

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Set your `GEMINI_API_KEY` in `.env`.

### 3. Start the Stack

To build and start all infrastructure services (PostgreSQL, FastAPI API, Next.js web):

```bash
# Using Python CLI
migration-agent start

# Or directly with Docker Compose
docker compose up --build -d
```

- Web Dashboard: [http://localhost:3000](http://localhost:3000)
- API Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- API Health Check: [http://localhost:8000/health](http://localhost:8000/health)

### 4. Running Tests

```bash
uv run pytest
```
