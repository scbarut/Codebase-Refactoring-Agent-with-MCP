# 09: Orchestration Graph — resume, per-file dispatch, aggregate

**What to build:** The second half of the Orchestration Graph. After the human approves the Migration Plan (providing an approved file list), the graph resumes from the HITL Gateway, spawns a File Sub-graph for each approved file sequentially, aggregates all per-file results into a `MigrationResult`, commits successful rewrites via `mcp-server-git`, generates copy-pasteable git commands (and optional PR trigger for GitHub repos) via `mcp-server-git`, and marks the job as complete. Tested end-to-end at the Orchestration Graph boundary seam with stubbed dependencies.

**Blocked by:** 07 (scan/plan/HITL), 08 (File Sub-graph)

**Status:** done

- [x] `resume_from_hitl` node: receives the approved file list, updates state
- [x] `dispatch_file_subgraphs` node: iterates over approved files, spawns a File Sub-graph for each, collects `FileResult`s sequentially
- [x] `aggregate_results` node: builds `MigrationResult` from all `FileResult`s (list of successes with diffs, list of failures with tracebacks, summary statistics)
- [x] `commit_and_output` node: calls `mcp-server-git` to commit each successful file, generate the full diff, and produce copy-pasteable git commands. For GitHub-sourced repos, includes optional PR creation command
- [x] State schema extended: `approved_files`, `file_results`, `migration_result`, `git_commands`
- [x] End-to-end test at the Orchestration Graph boundary: stub all dependencies, simulate a full job lifecycle (submit → scan → plan → approve → rewrite → result), verify the `MigrationResult` contains correct diffs and git commands

## Implementation Notes

- Extended `src/core/models.py` with `GitCommands`, `MigrationResult`, and updated `MigrationGraphState` to include `approved_files`, `file_results`, `migration_result`, `git_commands`, and `branch_name`.
- Implemented `resume_from_hitl` in `src/core/graph.py` to normalize approved files and create migration branch in the Agent Workspace via `mcp-server-git`.
- Implemented `dispatch_file_subgraphs` in `src/core/graph.py` to sequentially invoke the File Sub-graph (`run_file_subgraph`) per approved file while preserving episodic context isolation.
- Implemented `aggregate_results` in `src/core/graph.py` to construct `MigrationResult` partitioning successes/failures and computing summary statistics.
- Implemented `commit_and_output` in `src/core/graph.py` calling `mcp-server-git` to commit each successful file, generate cumulative diff, produce copy-pasteable apply commands, and add optional `gh pr create` command for GitHub-sourced repos.
- Updated `build_orchestration_graph` to assemble the complete graph (`START` -> `ingest` -> `scan` -> `build_plan` -> `hitl_gateway` -> `resume_from_hitl` -> `dispatch_file_subgraphs` -> `aggregate_results` -> `commit_and_output` -> `END`).
- Re-exported new models and node functions in `src/core/__init__.py`.
- Added end-to-end boundary seam tests and unit tests in `tests/test_orchestration_graph.py` verifying full lifecycle, selective pruning, failure handling, and GitHub PR commands.
- All 152 tests across the complete test suite passing cleanly; zero ruff lint errors.

