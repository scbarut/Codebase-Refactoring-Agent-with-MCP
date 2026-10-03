from __future__ import annotations

from unittest.mock import MagicMock

import docker.errors
import pytest

import docker
from src.core.sandbox import InstallResult, SandboxManager, TestResult


@pytest.fixture
def mock_docker_client():
    return MagicMock()


def test_sandbox_manager_init(mock_docker_client):
    manager = SandboxManager(docker_client=mock_docker_client)
    assert manager.client == mock_docker_client
    assert manager.dockerfile_path.name == "Dockerfile"


def test_ensure_image_already_exists_locally(mock_docker_client):
    mock_docker_client.images.get.return_value = MagicMock()

    manager = SandboxManager(docker_client=mock_docker_client)
    image_tag = manager.ensure_image(python_version="3.11")

    assert image_tag == "ghcr.io/scbarut/migration-sandbox:py3.11"
    mock_docker_client.images.get.assert_called_once_with("ghcr.io/scbarut/migration-sandbox:py3.11")
    mock_docker_client.images.pull.assert_not_called()
    mock_docker_client.images.build.assert_not_called()


def test_ensure_image_pulls_when_not_local(mock_docker_client):
    mock_docker_client.images.get.side_effect = docker.errors.ImageNotFound("Not local")
    mock_docker_client.images.pull.return_value = MagicMock()

    manager = SandboxManager(docker_client=mock_docker_client)
    image_tag = manager.ensure_image(python_version="3.11")

    assert image_tag == "ghcr.io/scbarut/migration-sandbox:py3.11"
    mock_docker_client.images.get.assert_called_once()
    mock_docker_client.images.pull.assert_called_once_with("ghcr.io/scbarut/migration-sandbox:py3.11")
    mock_docker_client.images.build.assert_not_called()


def test_ensure_image_builds_when_pull_fails(mock_docker_client, tmp_path):
    mock_docker_client.images.get.side_effect = docker.errors.ImageNotFound("Not local")
    mock_docker_client.images.pull.side_effect = docker.errors.APIError("Registry unavailable")
    mock_docker_client.images.build.return_value = (MagicMock(), ["build log"])

    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

    manager = SandboxManager(docker_client=mock_docker_client, dockerfile_path=dockerfile)
    image_tag = manager.ensure_image(python_version="3.11")

    assert image_tag == "ghcr.io/scbarut/migration-sandbox:py3.11"
    mock_docker_client.images.build.assert_called_once()
    call_kwargs = mock_docker_client.images.build.call_args[1]
    assert call_kwargs["buildargs"] == {"PYTHON_VERSION": "3.11"}
    assert call_kwargs["tag"] == "ghcr.io/scbarut/migration-sandbox:py3.11"


def test_create_sandbox(mock_docker_client, tmp_path):
    mock_docker_client.images.get.return_value = MagicMock()
    mock_container = MagicMock()
    mock_docker_client.containers.run.return_value = mock_container

    manager = SandboxManager(docker_client=mock_docker_client)
    container = manager.create_sandbox(workspace_path=tmp_path, python_version="3.11")

    assert container == mock_container
    mock_docker_client.containers.run.assert_called_once()
    call_args, call_kwargs = mock_docker_client.containers.run.call_args
    assert call_args[0] == "ghcr.io/scbarut/migration-sandbox:py3.11"
    assert call_kwargs["detach"] is True
    assert call_kwargs["working_dir"] == "/workspace"
    volumes = call_kwargs["volumes"]
    assert str(tmp_path.resolve()) in volumes or tmp_path.as_posix() in volumes
    mount_info = next(v for k, v in volumes.items() if str(tmp_path.resolve()) in k or tmp_path.as_posix() in k)
    assert mount_info["bind"] == "/workspace"
    assert mount_info["mode"] == "rw"


def test_install_deps_with_requirements_txt(mock_docker_client, tmp_path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("pydantic==2.6.0\n", encoding="utf-8")

    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"Successfully installed", b""))

    manager = SandboxManager(docker_client=mock_docker_client)
    result = manager.install_deps(mock_container, workspace_path=tmp_path)

    assert isinstance(result, InstallResult)
    assert result.has_deps is True
    assert result.exit_code == 0
    assert "requirements.txt" in (result.command or "")
    mock_container.exec_run.assert_called_once()


def test_install_deps_with_utf16_and_windows_packages(mock_docker_client, tmp_path):
    req_file = tmp_path / "requirements.txt"
    # Write UTF-16 with BOM (PowerShell pip freeze output)
    content = "pywin32==311\nlangchain==0.1.0\npywinpty==2.0.10\n"
    req_file.write_bytes(content.encode("utf-16"))

    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"Successfully installed", b""))

    manager = SandboxManager(docker_client=mock_docker_client)
    result = manager.install_deps(mock_container, workspace_path=tmp_path)

    assert isinstance(result, InstallResult)
    assert result.has_deps is True
    assert result.exit_code == 0
    clean_file = tmp_path / ".migration_agent_requirements.txt"
    assert clean_file.exists()
    clean_text = clean_file.read_text(encoding="utf-8")
    assert "pywin32" not in clean_text
    assert "pywinpty" not in clean_text
    assert "langchain==0.1.0" in clean_text


def test_install_deps_resilient_line_by_line_fallback(mock_docker_client, tmp_path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("good-pkg==1.0\nbad-pkg==2.0\n", encoding="utf-8")

    mock_container = MagicMock()

    def side_effect(cmd, **kwargs):
        if "-r" in cmd:
            return MagicMock(exit_code=1, output=(b"", b"ERROR: No matching distribution for bad-pkg"))
        if "good-pkg" in cmd:
            return MagicMock(exit_code=0, output=(b"Successfully installed good-pkg", b""))
        return MagicMock(exit_code=1, output=(b"", b"ERROR: No matching distribution for bad-pkg"))

    mock_container.exec_run.side_effect = side_effect

    manager = SandboxManager(docker_client=mock_docker_client)
    result = manager.install_deps(mock_container, workspace_path=tmp_path)

    assert isinstance(result, InstallResult)
    assert result.has_deps is True
    assert mock_container.exec_run.call_count >= 2


def test_install_deps_with_pyproject_toml(mock_docker_client, tmp_path):
    pyproj = tmp_path / "pyproject.toml"
    pyproj.write_text("[project]\nname='demo'\n", encoding="utf-8")

    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"Successfully installed", b""))

    manager = SandboxManager(docker_client=mock_docker_client)
    result = manager.install_deps(mock_container, workspace_path=tmp_path)

    assert result.has_deps is True
    assert result.exit_code == 0
    assert "pip install" in (result.command or "")
    mock_container.exec_run.assert_called_once()


def test_install_deps_with_setup_py(mock_docker_client, tmp_path):
    setup_file = tmp_path / "setup.py"
    setup_file.write_text("from setuptools import setup; setup(name='demo')\n", encoding="utf-8")

    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"Successfully installed", b""))

    manager = SandboxManager(docker_client=mock_docker_client)
    result = manager.install_deps(mock_container, workspace_path=tmp_path)

    assert result.has_deps is True
    assert result.exit_code == 0
    assert "pip install" in (result.command or "")


def test_install_deps_no_dependency_files(mock_docker_client, tmp_path):
    mock_container = MagicMock()
    manager = SandboxManager(docker_client=mock_docker_client)
    result = manager.install_deps(mock_container, workspace_path=tmp_path)

    assert result.has_deps is False
    assert result.exit_code == 0
    mock_container.exec_run.assert_not_called()


def test_run_tests_success(mock_docker_client):
    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(
        exit_code=0,
        output=(b"1 passed in 0.05s\n", b"")
    )

    manager = SandboxManager(docker_client=mock_docker_client)
    res = manager.run_tests(mock_container, module_path="tests/test_model.py")

    assert isinstance(res, TestResult)
    assert res.passed is True
    assert res.exit_code == 0
    assert "1 passed" in res.stdout
    assert res.traceback == ""
    mock_container.exec_run.assert_called_once()
    cmd = mock_container.exec_run.call_args[0][0]
    assert "pytest tests/test_model.py --tb=long -q" in cmd


def test_run_tests_failure_with_traceback(mock_docker_client):
    stdout_failure = (
        b"============================= FAILURES =============================\n"
        b"___________________________ test_validation ___________________________\n"
        b"    def test_validation():\n"
        b">       validate_input()\n"
        b"E       ValueError: Invalid field\n"
        b"tests/test_model.py:10: ValueError\n"
        b"=========================== short test summary ===========================\n"
        b"FAILED tests/test_model.py::test_validation - ValueError: Invalid field\n"
        b"1 failed in 0.12s\n"
    )
    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(
        exit_code=1,
        output=(stdout_failure, b"")
    )

    manager = SandboxManager(docker_client=mock_docker_client)
    res = manager.run_tests(mock_container, module_path="tests/test_model.py")

    assert res.passed is False
    assert res.exit_code == 1
    assert "FAILURES" in res.traceback
    assert "ValueError: Invalid field" in res.traceback


def test_destroy_sandbox(mock_docker_client):
    mock_container = MagicMock()
    manager = SandboxManager(docker_client=mock_docker_client)

    manager.destroy_sandbox(mock_container)

    mock_container.stop.assert_called_once_with(timeout=5)
    mock_container.remove.assert_called_once_with(force=True)


def test_destroy_sandbox_handles_exceptions_gracefully(mock_docker_client):
    mock_container = MagicMock()
    mock_container.stop.side_effect = docker.errors.NotFound("Already stopped")
    mock_container.remove.side_effect = docker.errors.APIError("Already removed")

    manager = SandboxManager(docker_client=mock_docker_client)
    # Should not raise
    manager.destroy_sandbox(mock_container)


def test_full_lifecycle_stubbed(mock_docker_client, tmp_path):
    mock_docker_client.images.get.return_value = MagicMock()
    mock_container = MagicMock()
    mock_docker_client.containers.run.return_value = mock_container

    # Fake requirements.txt
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    # Mock pip install
    mock_container.exec_run.side_effect = [
        MagicMock(exit_code=0, output=(b"pip installed", b"")),  # install_deps
        MagicMock(exit_code=0, output=(b"1 passed in 0.01s", b"")),  # run_tests
    ]

    manager = SandboxManager(docker_client=mock_docker_client)

    # 1. create
    container = manager.create_sandbox(workspace_path=tmp_path, python_version="3.11")
    assert container == mock_container

    # 2. install
    install_res = manager.install_deps(container, workspace_path=tmp_path)
    assert install_res.exit_code == 0

    # 3. test
    test_res = manager.run_tests(container, module_path="tests/test_sample.py")
    assert test_res.passed is True

    # 4. destroy
    manager.destroy_sandbox(container)
    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()


@pytest.mark.docker
def test_docker_sandbox_real_lifecycle(tmp_path):
    """Exercises real container lifecycle against a simple test project."""
    try:
        real_client = docker.from_env()
        real_client.ping()
    except (docker.errors.DockerException, OSError) as exc:
        pytest.skip(f"Docker daemon not available: {exc}")

    # Create a minimal test project
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "def test_passes():\n"
        "    assert 1 + 1 == 2\n\n"
        "def test_fails():\n"
        "    assert 2 + 2 == 5, 'Math breakdown'\n",
        encoding="utf-8",
    )

    manager = SandboxManager(docker_client=real_client)
    container = manager.create_sandbox(workspace_path=tmp_path, python_version="3.11")

    try:
        # Install deps (none declared, should return gracefully)
        install_res = manager.install_deps(container, workspace_path=tmp_path)
        assert install_res.has_deps is False
        assert install_res.exit_code == 0

        # Run passing test
        pass_res = manager.run_tests(container, module_path="test_sample.py::test_passes")
        assert pass_res.passed is True
        assert pass_res.exit_code == 0
        assert "1 passed" in pass_res.stdout
        assert pass_res.traceback == ""

        # Run failing test
        fail_res = manager.run_tests(container, module_path="test_sample.py::test_fails")
        assert fail_res.passed is False
        assert fail_res.exit_code != 0
        assert "Math breakdown" in fail_res.traceback

    finally:
        container_id = container.id
        manager.destroy_sandbox(container)
        with pytest.raises(docker.errors.NotFound):
            real_client.containers.get(container_id)


@pytest.mark.asyncio
async def test_managed_sandbox_success(mock_docker_client, tmp_path):
    mock_docker_client.images.get.return_value = MagicMock()
    mock_container = MagicMock()
    mock_docker_client.containers.run.return_value = mock_container
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"ok", b""))

    manager = SandboxManager(docker_client=mock_docker_client)

    async with manager.managed_sandbox(
        workspace_path=tmp_path, target_library="pydantic>=2.0"
    ) as container:
        assert container == mock_container
        mock_docker_client.containers.run.assert_called_once()

    # Verify destroyed after context exit
    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()


@pytest.mark.asyncio
async def test_managed_sandbox_cleans_up_on_exception(mock_docker_client, tmp_path):
    mock_docker_client.images.get.return_value = MagicMock()
    mock_container = MagicMock()
    mock_docker_client.containers.run.return_value = mock_container

    manager = SandboxManager(docker_client=mock_docker_client)

    with pytest.raises(RuntimeError, match="something blew up"):
        async with manager.managed_sandbox(workspace_path=tmp_path) as container:
            assert container == mock_container
            raise RuntimeError("something blew up")

    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()


@pytest.mark.asyncio
async def test_managed_sandbox_graceful_fallback_when_docker_fails(mock_docker_client, tmp_path):
    mock_docker_client.images.get.return_value = MagicMock()
    mock_docker_client.containers.run.side_effect = docker.errors.DockerException("Daemon unreachable")

    manager = SandboxManager(docker_client=mock_docker_client)

    # Should not raise DockerException; yields None
    async with manager.managed_sandbox(workspace_path=tmp_path) as container:
        assert container is None


def test_install_deps_with_target_library_upgrade(mock_docker_client, tmp_path):
    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=(b"upgraded pydantic", b""))

    manager = SandboxManager(docker_client=mock_docker_client)
    res = manager.install_deps(
        mock_container, workspace_path=tmp_path, target_library="pydantic>=2.0"
    )

    assert res.has_deps is True
    assert res.exit_code == 0
    # Container exec_run should be called for pip install --upgrade
    assert mock_container.exec_run.called
    calls = [str(call) for call in mock_container.exec_run.call_args_list]
    assert any("pydantic>=2.0" in c for c in calls)

