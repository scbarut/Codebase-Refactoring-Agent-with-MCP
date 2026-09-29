# 02: mcp-server-git — clone, branch, diff, commit

**What to build:** An MCP server (stdio transport) that automates git operations in the Agent Workspace. Given a local directory path or GitHub URL, it clones/copies the codebase into `~/.migration-agent/workspaces/<repo-name>-<timestamp>/`, creates a `migrate/<target-library>-<timestamp>` branch, can commit individual file changes, generate structured diffs (unified format), and output copy-pasteable git commands the user can run to apply changes to their original repository. Tested at the MCP tool interface seam: call each tool with inputs, verify outputs.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] MCP server registered with stdio transport, discoverable by the agent
- [x] `clone_repo` tool: accepts local path or GitHub URL, creates Agent Workspace directory, initializes git repo, returns workspace path
- [x] `create_branch` tool: creates a `migrate/<target-library>-<timestamp>` branch in the workspace
- [x] `commit_file` tool: stages and commits a single modified file with a descriptive message
- [x] `generate_diff` tool: produces a unified diff of all changes on the migration branch vs the base
- [x] `get_apply_commands` tool: returns copy-pasteable git commands for the user to apply changes from the workspace to their repo
- [x] Tests at the MCP tool interface seam verify clone, branch, commit, diff, and apply-command generation

## Implementation Notes

- Implemented `mcp-server-git` server in `src/mcp_servers/git_server.py` using `mcp.server.mcpserver.MCPServer` with stdio transport.
- Configured default git author metadata (`Migration Agent <agent@migration.local>`) on subprocess calls so commits succeed in automated environments.
- Implemented `clone_repo` supporting both local directory copying (with `.git` auto-initialization and artifact filtering) and remote URL cloning into `~/.migration-agent/workspaces/<repo-name>-<timestamp>`.
- Implemented `create_branch` for creating and switching to `migrate/<target-library>-<timestamp>` branches.
- Implemented `commit_file` with workspace boundary containment checks to stage and commit individual modified files.
- Implemented `generate_diff` returning structured unified diffs and lists of changed files against base branches.
- Implemented `get_apply_commands` providing copy-pasteable remote add, fetch, and merge commands (per ADR 0005) plus patch commands.
- Registered script entry points in `pyproject.toml` (`mcp-server-git`) and CLI subcommand `migration-agent mcp-git`.
- Comprehensive TDD suite at the MCP tool interface seam in `tests/test_mcp_git.py` (9 tests passing). Full suite (24 tests) clean and passing.
