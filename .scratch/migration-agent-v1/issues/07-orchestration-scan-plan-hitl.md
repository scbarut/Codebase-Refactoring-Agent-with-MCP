# 07: Orchestration Graph — scan, plan, HITL Gateway

**What to build:** The first half of the LangGraph Orchestration Graph. Given a `MigrationJobConfig` (codebase path or GitHub URL + Target Library), the graph: clones/copies the codebase into an Agent Workspace via `mcp-server-git`, scans all files for Target Library imports via `mcp-server-ast`, loads the matching Migration Rule Set, builds a Migration Plan (listing each affected file, its matched rules, the AST nodes to change, and a per-file risk score), then hits an `interrupt()` at the HITL Gateway, persisting state to PostgreSQL via `PostgresSaver`. The graph pauses and the Migration Plan is available for retrieval. Tested at the Orchestration Graph boundary seam with stubbed MCP servers.

**Blocked by:** 02 (mcp-server-git), 03 (mcp-server-ast), 05 (Pydantic rule set)

**Status:** ready-for-agent

- [ ] LangGraph `StateGraph` with nodes: `ingest`, `scan`, `build_plan`, `hitl_gateway`
- [ ] `ingest` node: calls `mcp-server-git` to clone/copy codebase into Agent Workspace
- [ ] `scan` node: calls `mcp-server-ast` `scan_imports` to find all files importing the Target Library
- [ ] `build_plan` node: for each affected file, loads matching Migration Rules, extracts affected AST nodes, calculates per-file risk (max rule risk, bumped +1 if no test coverage detected)
- [ ] `hitl_gateway` node: calls `interrupt()`, persisting state with a unique `thread_id` via `PostgresSaver`
- [ ] State schema includes: `job_id`, `workspace_path`, `target_library`, `migration_plan` (list of file entries with rules, nodes, risk), `approved_files` (empty until resume)
- [ ] `PostgresSaver` configured against the docker-compose PostgreSQL instance
- [ ] Tests at the Orchestration Graph boundary: stub MCP servers, verify that given a sample codebase the graph produces a correct Migration Plan and pauses at the HITL Gateway
