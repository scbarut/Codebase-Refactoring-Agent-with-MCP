# 10: FastAPI REST + WebSocket API

**What to build:** The FastAPI backend exposing six endpoints that wire up to the Orchestration Graph. REST for CRUD operations, WebSocket for real-time streaming. The LangGraph graph is an injected dependency (stubbable in tests). No authentication or multi-tenancy.

**Blocked by:** 09 (complete Orchestration Graph)

**Status:** done

- [x] `POST /api/jobs`: accepts `{ path_or_url: string, target_library: string }`, creates a Migration Job, kicks off the Orchestration Graph in a background task, returns `{ job_id: string, status: "scanning" }`
- [x] `GET /api/jobs/{id}`: returns job metadata and current status (`scanning`, `awaiting_approval`, `migrating`, `complete`, `failed`)
- [x] `GET /api/jobs/{id}/plan`: returns the Migration Plan (list of files with risk levels, AST nodes, matched rules). Returns 409 if the job hasn't reached the HITL Gateway yet
- [x] `POST /api/jobs/{id}/approve`: accepts `{ approved_files: string[] }`, resumes the Orchestration Graph from the HITL Gateway with the approved file list. Returns 409 if the job isn't awaiting approval
- [x] `GET /api/jobs/{id}/results`: returns the `MigrationResult` (per-file diffs, statuses, tracebacks, git commands). Returns 409 if the job isn't complete
- [x] `WS /api/jobs/{id}/stream`: WebSocket endpoint that streams real-time events (scan progress, current file being processed, test results, healing attempts, completion) as JSON messages
- [x] Pydantic response schemas for all endpoints enforce structured output
- [x] Integration tests at the HTTP boundary with a stubbed Orchestration Graph verify all status codes, response schemas, and error cases (409s, 404s)

## Implementation Notes

- Added `src/api/models.py` defining Pydantic schemas for all API payloads and responses: `JobStatus`, `JobCreateRequest`, `JobCreateResponse`, `JobResponse`, `JobApproveRequest`, `JobApproveResponse`, and `StreamEvent`, along with re-exports of `FilePlanEntry`, `MigrationResult`, and `RiskLevel`.
- Implemented `JobManager` and `JobInfo` in `src/api/service.py` to manage in-memory job state, run background scan and migration phases using the LangGraph Orchestration Graph, and broadcast events to subscribers and historical event log.
- Implemented FastAPI dependency injection in `src/api/dependencies.py` providing `get_job_manager()` and `get_orchestration_graph()` (configured with `InMemorySaver` checkpointer by default, easily stubbed in tests via `app.dependency_overrides`).
- Implemented router in `src/api/routes/jobs.py` exposing:
  - `POST /api/jobs`: creates job and starts background scan task, returning 201 Created.
  - `GET /api/jobs`: lists all active and historical jobs (used for dashboard).
  - `GET /api/jobs/{id}`: returns job metadata and status. Returns 404 if not found.
  - `GET /api/jobs/{id}/plan`: returns list of `FilePlanEntry`. Returns 409 if not reached HITL Gateway, 404 if not found.
  - `POST /api/jobs/{id}/approve`: resumes graph with approved files. Returns 409 if not awaiting approval, 404 if not found.
  - `GET /api/jobs/{id}/results`: returns `MigrationResult`. Returns 409 if not complete, 404 if not found.
  - `WS /api/jobs/{id}/stream`: WebSocket endpoint streaming real-time JSON events (scan progress, current file, test results, healing attempts, completion, errors) with history replay and graceful client disconnect handling.
- Mounted the router in `src/api/main.py` under `/api/jobs`.
- Created comprehensive HTTP boundary integration tests in `tests/test_api_jobs.py` covering:
  - All status codes (200, 201, 404, 409, 422).
  - Structured output schemas validation.
  - Full end-to-end lifecycle with a deterministic stubbed Orchestration Graph.
  - WebSocket real-time event streaming and history replay.
  - Error conditions (404 on missing jobs, 409 on premature plan/results/approve requests).
  - End-to-end integration test with the real compiled Orchestration Graph.
- All 182 test cases across the entire test suite pass with 100% green status and clean linting.
