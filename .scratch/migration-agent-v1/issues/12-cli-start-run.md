# 12: CLI — `migration-agent start` and `migration-agent run`

**What to build:** The CLI entry points for the migration agent. `migration-agent start` boots the docker-compose stack and opens the browser to the web dashboard. `migration-agent run` submits a Migration Job directly via the REST API for scriptable/automated use. Config values from `config.yaml` and `.env` can be overridden via CLI flags.

**Blocked by:** 10 (FastAPI API)

**Status:** ready-for-human

- [x] `migration-agent start`: runs `docker compose up -d`, waits for all services to be healthy, opens the default browser to the Dashboard URL (e.g., `http://localhost:3000`)
- [x] `migration-agent stop`: runs `docker compose down` cleanly
- [x] `migration-agent run <path_or_url> --target <target_library>`: submits a Migration Job to `POST /api/jobs`, prints the job ID and a URL to track it in the web UI. Optionally waits for completion with `--wait` flag, printing streaming status updates to the terminal
- [x] `migration-agent status <job_id>`: queries `GET /api/jobs/{id}` and prints current status
- [x] CLI flags `--config`, `--env-file`, `--workspace-path`, `--model-lite`, `--model-default` override values from config files
- [x] Helpful error messages when docker-compose is not installed, Docker is not running, or the API is unreachable
- [x] `--help` documentation for all commands and flags
