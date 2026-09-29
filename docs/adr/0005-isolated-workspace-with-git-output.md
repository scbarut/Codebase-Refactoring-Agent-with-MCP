# Isolated workspace with copy-pasteable git output

The agent works in its own isolated workspace directory (default: `~/.migration-agent/workspaces/<repo-name>-<timestamp>/`), never modifying the user's original directory or repository in-place.

After migration completes, the agent outputs:
1. A visual diff in the Monaco diff viewer (web UI).
2. Copy-pasteable git commands the user can run locally to apply the changes (e.g., `git remote add migration-agent <path> && git fetch migration-agent && git merge migration-agent/migrate/pydantic-v2`).
3. An optional one-click PR trigger for GitHub-sourced repositories.

We considered working in-place on the user's repo (simpler but risky — a bug in the agent could corrupt the user's working tree or force-push). The isolated workspace guarantees the user's code is never touched until they explicitly choose to apply changes. The extra step of "apply these commands" is a feature, not a friction: it's a final human gate.
