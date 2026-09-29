# 09: Orchestration Graph — resume, per-file dispatch, aggregate

**What to build:** The second half of the Orchestration Graph. After the human approves the Migration Plan (providing an approved file list), the graph resumes from the HITL Gateway, spawns a File Sub-graph for each approved file sequentially, aggregates all per-file results into a `MigrationResult`, commits successful rewrites via `mcp-server-git`, generates copy-pasteable git commands (and optional PR trigger for GitHub repos) via `mcp-server-git`, and marks the job as complete. Tested end-to-end at the Orchestration Graph boundary seam with stubbed dependencies.

**Blocked by:** 07 (scan/plan/HITL), 08 (File Sub-graph)

**Status:** ready-for-agent

- [ ] `resume_from_hitl` node: receives the approved file list, updates state
- [ ] `dispatch_file_subgraphs` node: iterates over approved files, spawns a File Sub-graph for each, collects `FileResult`s sequentially
- [ ] `aggregate_results` node: builds `MigrationResult` from all `FileResult`s (list of successes with diffs, list of failures with tracebacks, summary statistics)
- [ ] `commit_and_output` node: calls `mcp-server-git` to commit each successful file, generate the full diff, and produce copy-pasteable git commands. For GitHub-sourced repos, includes optional PR creation command
- [ ] State schema extended: `approved_files`, `file_results`, `migration_result`, `git_commands`
- [ ] End-to-end test at the Orchestration Graph boundary: stub all dependencies, simulate a full job lifecycle (submit → scan → plan → approve → rewrite → result), verify the `MigrationResult` contains correct diffs and git commands
