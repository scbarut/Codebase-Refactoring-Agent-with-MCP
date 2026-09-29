# 02: mcp-server-git — clone, branch, diff, commit

**What to build:** An MCP server (stdio transport) that automates git operations in the Agent Workspace. Given a local directory path or GitHub URL, it clones/copies the codebase into `~/.migration-agent/workspaces/<repo-name>-<timestamp>/`, creates a `migrate/<target-library>-<timestamp>` branch, can commit individual file changes, generate structured diffs (unified format), and output copy-pasteable git commands the user can run to apply changes to their original repository. Tested at the MCP tool interface seam: call each tool with inputs, verify outputs.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] MCP server registered with stdio transport, discoverable by the agent
- [ ] `clone_repo` tool: accepts local path or GitHub URL, creates Agent Workspace directory, initializes git repo, returns workspace path
- [ ] `create_branch` tool: creates a `migrate/<target-library>-<timestamp>` branch in the workspace
- [ ] `commit_file` tool: stages and commits a single modified file with a descriptive message
- [ ] `generate_diff` tool: produces a unified diff of all changes on the migration branch vs the base
- [ ] `get_apply_commands` tool: returns copy-pasteable git commands for the user to apply changes from the workspace to their repo
- [ ] Tests at the MCP tool interface seam verify clone, branch, commit, diff, and apply-command generation
