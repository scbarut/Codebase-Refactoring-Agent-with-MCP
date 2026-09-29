# 01: Project scaffold, Docker Compose, and config

**What to build:** A single `migration-agent start` command that boots the full infrastructure stack (PostgreSQL, FastAPI API, Next.js web) via docker-compose and produces structured JSON logs. The monorepo directory structure, Python packaging with uv, Sandbox Dockerfile (Python 3.9–3.12 base images with pytest and coverage), `.env.example`, `config.example.yaml`, and structlog configuration are all in place. After running `start`, all three services are healthy and reachable, and a `docker compose logs` shows structured JSON output.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Monorepo directory structure matches the spec layout (`src/core/`, `src/mcp_servers/`, `src/api/`, `src/rules/`, `src/docs_corpus/`, `web/`, `tests/`, `docker/`)
- [x] `pyproject.toml` with uv workspace configuration, core dependencies declared (fastapi, langgraph, litellm, libcst, structlog, psycopg, docker)
- [x] `docker-compose.yml` with three services: `postgres` (PostgreSQL 16), `api` (FastAPI, Docker socket mount), `web` (Next.js)
- [x] Sandbox Dockerfile at `docker/sandbox/Dockerfile` producing images for Python 3.9–3.12 with pytest and coverage pre-installed
- [x] `.env.example` with all secret placeholders (GEMINI_API_KEY, LANGSMITH_API_KEY, DATABASE_URL, TAVILY_API_KEY)
- [x] `config.example.yaml` with default preferences (workspace_path, model_lite, model_default, max_healing_attempts)
- [x] structlog configured for JSON output in the API service
- [x] `migration-agent start` CLI entry point boots docker-compose and reports service health
- [x] All services start and pass a basic health check

## Implementation Notes

- Project layout established with `src/core/`, `src/mcp_servers/`, `src/api/`, `src/rules/`, `src/docs_corpus/`, `web/`, `tests/`, and `docker/`.
- Config loading in `src/core/config.py` with fallback defaults, YAML support, and environment overrides.
- Structured JSON logging in `src/core/logging.py` configured with `structlog` and stdlib integration.
- FastAPI backend in `src/api/main.py` with `/health` and request logging middleware.
- Next.js web frontend in `web/` with responsive dark-mode dashboard and health checks.
- Sandbox Dockerfile in `docker/sandbox/Dockerfile` supporting Python 3.9–3.12 via build arguments, with pytest and coverage pre-installed.
- `migration-agent` CLI entry points (`start`, `stop`, `status`) in `src/cli.py` tested and verified against running Docker daemon.
- All 14 pytest unit tests passing. Docker services started and verified healthy.
