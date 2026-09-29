import json
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_servers.git_server import create_git_server


@pytest.mark.asyncio
async def test_clone_repo_local_dir(tmp_path: Path):
    server = create_git_server()

    # Create dummy source directory
    source_dir = tmp_path / "my-test-project"
    source_dir.mkdir(parents=True)
    sample_file = source_dir / "app.py"
    sample_file.write_text("print('hello world')\n", encoding="utf-8")

    workspaces_dir = tmp_path / "workspaces"

    result = await server.call_tool(
        "clone_repo",
        {
            "source": str(source_dir),
            "workspace_base_dir": str(workspaces_dir),
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["repo_name"] == "my-test-project"
    workspace_path = Path(data["workspace_path"])
    assert workspace_path.exists()
    assert (workspace_path / "app.py").exists()
    assert (workspace_path / ".git").is_dir()
    assert data["base_branch"] in ("main", "master")


@pytest.mark.asyncio
async def test_create_branch(tmp_path: Path):
    server = create_git_server()

    # Setup a cloned repo first
    source_dir = tmp_path / "repo-for-branch"
    source_dir.mkdir(parents=True)
    (source_dir / "file.txt").write_text("initial", encoding="utf-8")

    clone_res = await server.call_tool(
        "clone_repo",
        {
            "source": str(source_dir),
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )
    workspace_path = json.loads(clone_res.content[0].text)["workspace_path"]

    # Call create_branch
    branch_res = await server.call_tool(
        "create_branch",
        {
            "workspace_path": workspace_path,
            "target_library": "pydantic-v2",
        },
    )

    assert not branch_res.is_error
    branch_data = json.loads(branch_res.content[0].text)
    assert branch_data["branch_name"].startswith("migrate/pydantic-v2-")
    assert branch_data["workspace_path"] == workspace_path
    assert branch_data["base_branch"] in ("main", "master")


@pytest.mark.asyncio
async def test_commit_file(tmp_path: Path):
    server = create_git_server()

    # Setup cloned repo and branch
    source_dir = tmp_path / "repo-for-commit"
    source_dir.mkdir(parents=True)
    file_to_modify = source_dir / "models.py"
    file_to_modify.write_text("class OldModel: pass\n", encoding="utf-8")

    clone_res = await server.call_tool(
        "clone_repo",
        {
            "source": str(source_dir),
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )
    workspace_path = json.loads(clone_res.content[0].text)["workspace_path"]

    await server.call_tool(
        "create_branch",
        {
            "workspace_path": workspace_path,
            "target_library": "pydantic-v2",
        },
    )

    # Modify the file in the workspace
    target_file = Path(workspace_path) / "models.py"
    target_file.write_text("class NewModel: pass\n", encoding="utf-8")

    # Call commit_file
    commit_res = await server.call_tool(
        "commit_file",
        {
            "workspace_path": workspace_path,
            "file_path": "models.py",
            "message": "Migrate OldModel to NewModel",
        },
    )

    assert not commit_res.is_error
    commit_data = json.loads(commit_res.content[0].text)
    assert "commit_hash" in commit_data
    assert len(commit_data["commit_hash"]) == 40
    assert commit_data["file_path"] == "models.py"
    assert commit_data["message"] == "Migrate OldModel to NewModel"


@pytest.mark.asyncio
async def test_generate_diff(tmp_path: Path):
    server = create_git_server()

    source_dir = tmp_path / "repo-for-diff"
    source_dir.mkdir(parents=True)
    file_to_modify = source_dir / "user.py"
    file_to_modify.write_text("class User:\n    name: str\n", encoding="utf-8")

    clone_res = await server.call_tool(
        "clone_repo",
        {
            "source": str(source_dir),
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )
    workspace_path = json.loads(clone_res.content[0].text)["workspace_path"]

    await server.call_tool(
        "create_branch",
        {
            "workspace_path": workspace_path,
            "target_library": "pydantic-v2",
        },
    )

    target_file = Path(workspace_path) / "user.py"
    target_file.write_text("class User:\n    name: str\n    age: int\n", encoding="utf-8")

    await server.call_tool(
        "commit_file",
        {
            "workspace_path": workspace_path,
            "file_path": "user.py",
            "message": "Add age field to User",
        },
    )

    diff_res = await server.call_tool(
        "generate_diff",
        {
            "workspace_path": workspace_path,
        },
    )

    assert not diff_res.is_error
    diff_data = json.loads(diff_res.content[0].text)
    assert "+    age: int" in diff_data["diff"]
    assert "user.py" in diff_data["files_changed"]
    assert diff_data["base_branch"] in ("main", "master")


@pytest.mark.asyncio
async def test_get_apply_commands(tmp_path: Path):
    server = create_git_server()

    source_dir = tmp_path / "repo-for-apply"
    source_dir.mkdir(parents=True)
    (source_dir / "init.py").write_text("# init\n", encoding="utf-8")

    clone_res = await server.call_tool(
        "clone_repo",
        {
            "source": str(source_dir),
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )
    workspace_path = json.loads(clone_res.content[0].text)["workspace_path"]

    branch_res = await server.call_tool(
        "create_branch",
        {
            "workspace_path": workspace_path,
            "target_library": "pydantic-v2",
        },
    )
    branch_name = json.loads(branch_res.content[0].text)["branch_name"]

    apply_res = await server.call_tool(
        "get_apply_commands",
        {
            "workspace_path": workspace_path,
            "branch_name": branch_name,
        },
    )

    assert not apply_res.is_error
    apply_data = json.loads(apply_res.content[0].text)
    assert "commands" in apply_data
    assert len(apply_data["commands"]) >= 3
    assert "git remote add migration-agent" in apply_data["commands"][0]
    assert "git fetch migration-agent" in apply_data["commands"][1]
    assert f"git merge migration-agent/{branch_name}" in apply_data["commands"][2]
    assert "one_liner" in apply_data


@pytest.mark.asyncio
async def test_tool_discovery():
    server = create_git_server()
    tools = await server.list_tools()
    tool_names = {t.name for t in tools}
    expected_tools = {
        "clone_repo",
        "create_branch",
        "commit_file",
        "generate_diff",
        "get_apply_commands",
    }
    assert expected_tools.issubset(tool_names)
    for tool in tools:
        assert tool.description


@pytest.mark.asyncio
async def test_clone_repo_remote_git(tmp_path: Path):
    server = create_git_server()

    # Create a bare git repo as our "remote"
    remote_bare = tmp_path / "remote.git"
    remote_bare.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote_bare)], check=True)  # noqa: ASYNC221

    # Clone bare to temp, add an initial commit, push to bare
    seed_dir = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote_bare), str(seed_dir)], check=True)  # noqa: ASYNC221
    (seed_dir / "README.md").write_text("# Remote Repo", encoding="utf-8")
    subprocess.run(  # noqa: ASYNC221
        ["git", "-c", "user.name=Test", "-c", "user.email=t@test.com", "add", "."],
        cwd=seed_dir,
        check=True,
    )
    subprocess.run(  # noqa: ASYNC221
        ["git", "-c", "user.name=Test", "-c", "user.email=t@test.com", "commit", "-m", "Seed"],
        cwd=seed_dir,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=seed_dir, check=True)  # noqa: ASYNC221

    # Now use clone_repo with remote URI
    file_url = remote_bare.as_uri()
    clone_res = await server.call_tool(
        "clone_repo",
        {
            "source": file_url,
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )

    assert not clone_res.is_error
    data = json.loads(clone_res.content[0].text)
    assert data["repo_name"] == "remote"
    assert (Path(data["workspace_path"]) / "README.md").exists()


@pytest.mark.asyncio
async def test_clone_repo_nonexistent_local_fails(tmp_path: Path):
    server = create_git_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "clone_repo",
            {
                "source": str(tmp_path / "does-not-exist"),
                "workspace_base_dir": str(tmp_path / "workspaces"),
            },
        )


@pytest.mark.asyncio
async def test_commit_file_outside_workspace_rejected(tmp_path: Path):
    server = create_git_server()
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("print(1)", encoding="utf-8")

    clone_res = await server.call_tool(
        "clone_repo",
        {
            "source": str(source_dir),
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )
    workspace_path = json.loads(clone_res.content[0].text)["workspace_path"]

    with pytest.raises(ToolError):
        await server.call_tool(
            "commit_file",
            {
                "workspace_path": workspace_path,
                "file_path": "../../../evil.py",
                "message": "Escape attempt",
            },
        )
