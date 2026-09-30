# 07: Orchestration Graph — scan, plan, HITL Gateway

**What to build:** The first half of the LangGraph Orchestration Graph. Given a `MigrationJobConfig` (codebase path or GitHub URL + Target Library), the graph: clones/copies the codebase into an Agent Workspace via `mcp-server-git`, scans all files for Target Library imports via `mcp-server-ast`, loads the matching Migration Rule Set, builds a Migration Plan (listing each affected file, its matched rules, the AST nodes to change, and a per-file risk score), then hits an `interrupt()` at the HITL Gateway, persisting state to PostgreSQL via `PostgresSaver`. The graph pauses and the Migration Plan is available for retrieval. Tested at the Orchestration Graph boundary seam with stubbed MCP servers.

**Blocked by:** 02 (mcp-server-git), 03 (mcp-server-ast), 05 (Pydantic rule set)

**Status:** done

- [x] LangGraph `StateGraph` with nodes: `ingest`, `scan`, `build_plan`, `hitl_gateway`
- [x] `ingest` node: calls `mcp-server-git` to clone/copy codebase into Agent Workspace
- [x] `scan` node: calls `mcp-server-ast` `scan_imports` to find all files importing the Target Library
- [x] `build_plan` node: for each affected file, loads matching Migration Rules, extracts affected AST nodes, calculates per-file risk (max rule risk, bumped +1 if no test coverage detected)
- [x] `hitl_gateway` node: calls `interrupt()`, persisting state with a unique `thread_id` via `PostgresSaver`
- [x] State schema includes: `job_id`, `workspace_path`, `target_library`, `migration_plan` (list of file entries with rules, nodes, risk), `approved_files` (empty until resume)
- [x] `PostgresSaver` configured against the docker-compose PostgreSQL instance
- [x] Tests at the Orchestration Graph boundary: stub MCP servers, verify that given a sample codebase the graph produces a correct Migration Plan and pauses at the HITL Gateway

## Implementation Notes

- Implemented `src/core/models.py` with `MigrationGraphState` (`TypedDict`), `MigrationJobConfig`, `RiskLevel`, `MatchedRule`, `AffectedNode`, and `FilePlanEntry`.
- Implemented `src/core/graph.py` containing the `ingest`, `scan`, `build_plan`, and `hitl_gateway` nodes, risk assessment logic, coverage detection heuristics, rule set resolution, and graph construction (`build_orchestration_graph`, `compile_orchestration_graph`).
- Added synchronous and asynchronous `PostgresSaver` context managers (`get_postgres_saver`, `get_async_postgres_saver`) configured against the docker-compose PostgreSQL instance via `load_config().database_url`.
- Re-exported graph building functions and schemas in `src/core/__init__.py`.
- Tested the full boundary seam in `tests/test_orchestration_graph.py` with 19 unit and integration tests passing.
- Full project test suite (106 tests) and ruff lint checks passing cleanly.

