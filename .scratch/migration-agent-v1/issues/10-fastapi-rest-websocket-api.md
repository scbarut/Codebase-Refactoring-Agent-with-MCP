# 10: FastAPI REST + WebSocket API

**What to build:** The FastAPI backend exposing six endpoints that wire up to the Orchestration Graph. REST for CRUD operations, WebSocket for real-time streaming. The LangGraph graph is an injected dependency (stubbable in tests). No authentication or multi-tenancy.

**Blocked by:** 09 (complete Orchestration Graph)

**Status:** ready-for-agent

- [ ] `POST /api/jobs`: accepts `{ path_or_url: string, target_library: string }`, creates a Migration Job, kicks off the Orchestration Graph in a background task, returns `{ job_id: string, status: "scanning" }`
- [ ] `GET /api/jobs/{id}`: returns job metadata and current status (`scanning`, `awaiting_approval`, `migrating`, `complete`, `failed`)
- [ ] `GET /api/jobs/{id}/plan`: returns the Migration Plan (list of files with risk levels, AST nodes, matched rules). Returns 409 if the job hasn't reached the HITL Gateway yet
- [ ] `POST /api/jobs/{id}/approve`: accepts `{ approved_files: string[] }`, resumes the Orchestration Graph from the HITL Gateway with the approved file list. Returns 409 if the job isn't awaiting approval
- [ ] `GET /api/jobs/{id}/results`: returns the `MigrationResult` (per-file diffs, statuses, tracebacks, git commands). Returns 409 if the job isn't complete
- [ ] `WS /api/jobs/{id}/stream`: WebSocket endpoint that streams real-time events (scan progress, current file being processed, test results, healing attempts, completion) as JSON messages
- [ ] Pydantic response schemas for all endpoints enforce structured output
- [ ] Integration tests at the HTTP boundary with a stubbed Orchestration Graph verify all status codes, response schemas, and error cases (409s, 404s)
