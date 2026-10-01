from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

from src.core.config import load_config


def _run_git(args: list[str], cwd: Path | str | None = None) -> subprocess.CompletedProcess[str]:
    """Execute git command with default author config."""
    cmd = [
        "git",
        "-c", "user.name=Migration Agent",
        "-c", "user.email=agent@migration.local",
    ] + args
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _get_current_branch(workspace: Path) -> str:
    """Get the current checked-out git branch name."""
    res = _run_git(["branch", "--show-current"], cwd=workspace)
    branch = res.stdout.strip()
    if not branch:
        res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace)
        branch = res.stdout.strip()
    return branch or "main"


def _extract_repo_name(source: str) -> str:
    """Extract a repository name from a local path or git URL."""
    if source.startswith(("http://", "https://", "git@", "ssh://", "file://", "git://")):
        parsed = urlparse(source)
        path = parsed.path.rstrip("/")
        name = os.path.basename(path)
        name = name.removesuffix(".git")
        return name or "repository"
    norm = source.strip().strip("'\"").replace("\\", "/").rstrip("/")
    name = os.path.basename(norm)
    return name or "repository"


def _resolve_local_source_path(source: str) -> Path:
    """Resolve a local source path, gracefully handling Windows paths inside Docker containers."""
    norm = source.strip().strip("'\"").replace("\\", "/").rstrip("/")

    # 1. Direct path check (works on host or native Linux paths)
    direct = Path(norm).resolve()
    if direct.exists():
        return direct

    # 2. Relative from current working directory
    rel = (Path.cwd() / norm.lstrip("/")).resolve()
    if rel.exists():
        return rel

    # 3. Handle host Windows absolute path inside a Linux Docker container:
    # E.g. source is 'C:/Users/.../temprepo', but inside container cwd is '/app' and temprepo is at '/app/temprepo'
    parts = [p for p in norm.split("/") if p and not p.endswith(":")]
    for i in range(len(parts)):
        sub_rel = "/".join(parts[i:])
        candidate = (Path.cwd() / sub_rel).resolve()
        if candidate.exists() and candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        f"Source directory does not exist: {source} (resolved as {direct})"
    )


def create_git_server() -> MCPServer:
    """Create and configure the mcp-server-git MCP server."""
    server = MCPServer("mcp-server-git")

    @server.tool()
    def clone_repo(
        source: str,
        workspace_base_dir: str | None = None,
    ) -> dict[str, Any]:
        """Clone or copy a repository into an isolated Agent Workspace.

        Args:
            source: Local directory path or remote Git URL.
            workspace_base_dir: Optional base directory for Agent Workspaces.
                                Defaults to ~/.migration-agent/workspaces.

        Returns:
            Dict containing workspace_path, repo_name, base_branch, and source.
        """
        if workspace_base_dir:
            base_dir = Path(os.path.expanduser(workspace_base_dir)).resolve()
        else:
            base_dir = load_config().resolved_workspace_path

        base_dir.mkdir(parents=True, exist_ok=True)
        repo_name = _extract_repo_name(source)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        workspace_path = base_dir / f"{repo_name}-{timestamp}"

        is_remote = source.startswith(("http://", "https://", "git@", "ssh://", "file://", "git://"))

        if is_remote:
            _run_git(["clone", source, str(workspace_path)])
            base_branch = _get_current_branch(workspace_path)
        else:
            source_path = _resolve_local_source_path(source)

            shutil.copytree(
                source_path,
                workspace_path,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    "venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                ),
            )

            git_dir = workspace_path / ".git"
            if not git_dir.is_dir():
                try:
                    _run_git(["init", "-b", "main"], cwd=workspace_path)
                except subprocess.CalledProcessError:
                    _run_git(["init"], cwd=workspace_path)
                _run_git(["add", "-A"], cwd=workspace_path)
                _run_git(["commit", "-m", "Initial commit before migration"], cwd=workspace_path)
                base_branch = _get_current_branch(workspace_path)
            else:
                base_branch = _get_current_branch(workspace_path)

        return {
            "workspace_path": str(workspace_path.resolve()),
            "repo_name": repo_name,
            "base_branch": base_branch,
            "source": source,
        }

    @server.tool()
    def create_branch(
        workspace_path: str,
        target_library: str,
        branch_name: str | None = None,
    ) -> dict[str, Any]:
        """Create and switch to a migration branch in the workspace.

        Args:
            workspace_path: Path to the isolated Agent Workspace.
            target_library: Name of the target library (e.g. pydantic-v2).
            branch_name: Optional explicit branch name. If not provided,
                         generates migrate/<target-library>-<timestamp>.

        Returns:
            Dict containing branch_name, workspace_path, and base_branch.
        """
        workspace = Path(workspace_path).resolve()
        if not workspace.exists() or not (workspace / ".git").is_dir():
            raise FileNotFoundError(f"Valid git workspace not found at: {workspace}")

        base_branch = _get_current_branch(workspace)
        if not branch_name:
            sanitized_lib = re.sub(r"[^a-zA-Z0-9_\-\.]", "-", target_library).strip("-")
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            branch_name = f"migrate/{sanitized_lib}-{timestamp}"

        _run_git(["checkout", "-b", branch_name], cwd=workspace)

        return {
            "branch_name": branch_name,
            "workspace_path": str(workspace),
            "base_branch": base_branch,
        }

    @server.tool()
    def commit_file(
        workspace_path: str,
        file_path: str,
        message: str,
    ) -> dict[str, Any]:
        """Stage and commit a single modified file with a descriptive message.

        Args:
            workspace_path: Path to the isolated Agent Workspace.
            file_path: Relative or absolute path to the file inside workspace.
            message: Commit message describing the migration rewrite.

        Returns:
            Dict containing commit_hash, file_path, message, and branch_name.
        """
        workspace = Path(workspace_path).resolve()
        if not workspace.exists() or not (workspace / ".git").is_dir():
            raise FileNotFoundError(f"Valid git workspace not found at: {workspace}")

        target_file = (workspace / file_path).resolve()
        if not target_file.is_relative_to(workspace):
            raise ValueError(f"File path {file_path} escapes workspace boundary")

        rel_path = target_file.relative_to(workspace)

        _run_git(["add", str(rel_path)], cwd=workspace)
        _run_git(["commit", "-m", message], cwd=workspace)
        res = _run_git(["rev-parse", "HEAD"], cwd=workspace)
        commit_hash = res.stdout.strip()
        current_branch = _get_current_branch(workspace)

        return {
            "commit_hash": commit_hash,
            "file_path": str(rel_path).replace("\\", "/"),
            "message": message,
            "branch_name": current_branch,
        }

    @server.tool()
    def generate_diff(
        workspace_path: str,
        base_branch: str | None = None,
    ) -> dict[str, Any]:
        """Produce a unified diff of all changes on the migration branch vs the base.

        Args:
            workspace_path: Path to the isolated Agent Workspace.
            base_branch: Optional base branch to compare against. If omitted,
                         auto-detects base branch (e.g. main/master).

        Returns:
            Dict containing diff (unified format), base_branch, current_branch,
            and files_changed (list of relative file paths).
        """
        workspace = Path(workspace_path).resolve()
        if not workspace.exists() or not (workspace / ".git").is_dir():
            raise FileNotFoundError(f"Valid git workspace not found at: {workspace}")

        current_branch = _get_current_branch(workspace)

        if not base_branch:
            for candidate in ("main", "master"):
                if candidate != current_branch:
                    try:
                        _run_git(["rev-parse", "--verify", candidate], cwd=workspace)
                        base_branch = candidate
                        break
                    except subprocess.CalledProcessError:
                        continue
            if not base_branch:
                base_branch = "HEAD~1"

        diff_res = _run_git(["diff", f"{base_branch}...HEAD"], cwd=workspace)
        diff_text = diff_res.stdout

        files_res = _run_git(["diff", "--name-only", f"{base_branch}...HEAD"], cwd=workspace)
        files_changed = [
            f.strip().replace("\\", "/")
            for f in files_res.stdout.splitlines()
            if f.strip()
        ]

        return {
            "diff": diff_text,
            "base_branch": base_branch,
            "current_branch": current_branch,
            "files_changed": files_changed,
        }

    @server.tool()
    def get_apply_commands(
        workspace_path: str,
        branch_name: str | None = None,
        remote_name: str = "migration-agent",
        original_path: str | None = None,
    ) -> dict[str, Any]:
        """Return copy-pasteable git commands for the user to apply changes from the workspace.

        Args:
            workspace_path: Path to the isolated Agent Workspace.
            branch_name: Optional migration branch name. If omitted, uses current branch.
            remote_name: Git remote name to use when adding workspace as remote. Defaults to 'migration-agent'.
            original_path: Optional path or URL to original user repository.

        Returns:
            Dict containing commands list, one_liner string, patch_command,
            workspace_path, branch_name, and remote_name.
        """
        workspace = Path(workspace_path).resolve()
        if not workspace.exists() or not (workspace / ".git").is_dir():
            raise FileNotFoundError(f"Valid git workspace not found at: {workspace}")

        if not branch_name:
            branch_name = _get_current_branch(workspace)

        clean_path = str(workspace).replace("\\", "/")

        commands = [
            f'git remote add {remote_name} "{clean_path}"',
            f"git fetch {remote_name}",
            f"git merge {remote_name}/{branch_name}",
        ]
        one_liner = " && ".join(commands)
        patch_command = f'git -C "{clean_path}" format-patch -1 HEAD --stdout | git apply --check'

        return {
            "workspace_path": str(workspace),
            "branch_name": branch_name,
            "remote_name": remote_name,
            "original_path": original_path,
            "commands": commands,
            "one_liner": one_liner,
            "patch_command": patch_command,
        }

    return server


def main() -> None:
    """Run the mcp-server-git server with stdio transport."""
    server = create_git_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
