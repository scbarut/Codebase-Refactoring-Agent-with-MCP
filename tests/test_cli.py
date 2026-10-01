from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from src.cli import (
    cli,
    format_stream_event,
    get_compose_cmd,
    run_compose_down,
    run_compose_up,
)


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Autonomous Codebase Refactoring & Migration Agent" in result.output
    assert "start" in result.output
    assert "stop" in result.output
    assert "run" in result.output
    assert "status" in result.output
    assert "--config" in result.output
    assert "--env-file" in result.output
    assert "--workspace-path" in result.output
    assert "--model-lite" in result.output
    assert "--model-default" in result.output


def test_cli_global_flags_override():
    runner = CliRunner()
    # Invoke status command with global flags to inspect ctx.obj["settings"]
    with (
        patch("src.cli.get_compose_cmd") as mock_compose,
        patch("subprocess.run") as mock_run,
    ):
        mock_compose.return_value = ["docker", "compose"]
        mock_run.return_value = MagicMock(returncode=0)

        result = runner.invoke(
            cli,
            [
                "--workspace-path",
                "/custom/cli/workspaces",
                "--model-lite",
                "custom-lite-model",
                "--model-default",
                "custom-default-model",
                "status",
            ],
        )
        assert result.exit_code == 0
        mock_run.assert_called_once()


def test_cli_start_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "Start the full infrastructure stack" in result.output
    assert "--build" in result.output
    assert "--timeout" in result.output
    assert "--open" in result.output


@patch("src.cli.run_compose_up")
@patch("src.cli.check_all_services_healthy")
@patch("webbrowser.open")
def test_cli_start_success(mock_browser, mock_check_health, mock_compose_up):
    mock_compose_up.return_value = True
    mock_check_health.return_value = {
        "postgres": True,
        "api": True,
        "web": True,
    }

    runner = CliRunner()
    result = runner.invoke(cli, ["start"])
    assert result.exit_code == 0
    assert "All services are healthy" in result.output
    assert "Web Dashboard:" in result.output
    mock_compose_up.assert_called_once()
    mock_check_health.assert_called_once()
    mock_browser.assert_called_once()


@patch("src.cli.run_compose_up")
def test_cli_start_docker_daemon_error(mock_compose_up):
    mock_compose_up.side_effect = RuntimeError(
        "Docker daemon is not running. Please start Docker Desktop or the Docker service."
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["start"])
    assert result.exit_code != 0
    assert "Docker daemon is not running" in result.output


@patch("src.cli.run_compose_up")
def test_cli_start_docker_not_installed(mock_compose_up):
    mock_compose_up.side_effect = RuntimeError(
        "Docker Compose is not installed. Please install Docker Desktop or ensure 'docker compose' is in your PATH."
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["start"])
    assert result.exit_code != 0
    assert "Docker Compose is not installed" in result.output


@patch("src.cli.run_compose_up")
@patch("src.cli.check_all_services_healthy")
def test_cli_start_unhealthy_services(mock_check_health, mock_compose_up):
    mock_compose_up.return_value = True
    mock_check_health.return_value = {
        "postgres": True,
        "api": False,
        "web": False,
    }

    runner = CliRunner()
    result = runner.invoke(cli, ["start"])
    assert result.exit_code == 1
    assert "Some services did not report healthy" in result.output
    assert "API is unreachable" in result.output


def test_cli_stop_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["stop", "--help"])
    assert result.exit_code == 0
    assert "Stop the full infrastructure stack" in result.output


@patch("src.cli.run_compose_down")
def test_cli_stop_success(mock_compose_down):
    mock_compose_down.return_value = True

    runner = CliRunner()
    result = runner.invoke(cli, ["stop"])
    assert result.exit_code == 0
    assert "All services stopped cleanly" in result.output
    mock_compose_down.assert_called_once()


@patch("src.cli.run_compose_down")
def test_cli_stop_docker_error(mock_compose_down):
    mock_compose_down.side_effect = RuntimeError("Docker daemon is not running")

    runner = CliRunner()
    result = runner.invoke(cli, ["stop"])
    assert result.exit_code != 0
    assert "Docker daemon is not running" in result.output


@patch("src.cli.run_compose_down")
def test_cli_stop_failure(mock_compose_down):
    mock_compose_down.return_value = False

    runner = CliRunner()
    result = runner.invoke(cli, ["stop"])
    assert result.exit_code == 1
    assert "Failed to stop all services cleanly" in result.output


def test_cli_run_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "Submit a Migration Job directly via the REST API" in result.output
    assert "--target" in result.output
    assert "--wait" in result.output
    assert "--api-url" in result.output
    assert "--workspace-base-dir" in result.output


@patch("httpx.Client.post")
def test_cli_run_api_unreachable(mock_post):
    mock_post.side_effect = httpx.ConnectError("Connection refused")

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "./my_repo", "--target", "pydantic"])
    assert result.exit_code != 0
    assert "Migration Agent API is unreachable" in result.output
    assert "Start it with 'migration-agent start'" in result.output


@patch("httpx.Client.post")
def test_cli_run_api_error_response(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request: Target library unsupported"
    mock_post.return_value = mock_resp

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "./my_repo", "--target", "unknown_lib"])
    assert result.exit_code != 0
    assert "Error submitting Migration Job (400)" in result.output


@patch("httpx.Client.post")
def test_cli_run_success_no_wait(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "job_id": "test-job-42",
        "status": "scanning",
    }
    mock_post.return_value = mock_resp

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "./my_repo", "--target", "pydantic"])
    assert result.exit_code == 0
    assert "Migration Job submitted successfully!" in result.output
    assert "Job ID:   test-job-42" in result.output
    assert "Status:   scanning" in result.output
    assert "/jobs/test-job-42" in result.output


@patch("httpx.Client.post")
@patch("src.cli.stream_job_progress")
def test_cli_run_success_with_wait(mock_stream, mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "job_id": "test-job-wait",
        "status": "scanning",
    }
    mock_post.return_value = mock_resp
    mock_stream.return_value = True

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "./my_repo", "--target", "pydantic", "--wait"])
    assert result.exit_code == 0
    assert "Job ID:   test-job-wait" in result.output
    mock_stream.assert_called_once()


@patch("httpx.Client.post")
@patch("src.cli.stream_job_progress")
def test_cli_run_wait_failure(mock_stream, mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "job_id": "test-job-fail",
        "status": "scanning",
    }
    mock_post.return_value = mock_resp
    mock_stream.return_value = False

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "./my_repo", "--target", "pydantic", "--wait"])
    assert result.exit_code == 1


def test_cli_status_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "Query status of a specific Migration Job" in result.output


@patch("src.cli.get_compose_cmd")
@patch("subprocess.run")
def test_cli_status_no_job_id_stack(mock_run, mock_compose):
    mock_compose.return_value = ["docker", "compose"]
    mock_run.return_value = MagicMock(returncode=0)

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    mock_run.assert_called_once_with(["docker", "compose", "ps"], check=False)


@patch("src.cli.get_compose_cmd")
def test_cli_status_no_job_id_docker_error(mock_compose):
    mock_compose.side_effect = RuntimeError("Docker Compose is not installed.")

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code != 0
    assert "Docker Compose is not installed" in result.output


@patch("httpx.Client.get")
def test_cli_status_job_id_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "job_id": "job-12345",
        "status": "awaiting_approval",
        "target_library": "pydantic",
        "source": "./tests/fixtures/sample_repo",
        "created_at": "2026-10-01T12:00:00Z",
        "updated_at": "2026-10-01T12:05:00Z",
        "scanned_files_count": 5,
        "plan_files_count": 3,
        "approved_files_count": 0,
        "branch_name": "migrate/pydantic-123",
        "workspace_path": "/workspaces/sample_repo",
        "error": None,
    }
    mock_get.return_value = mock_resp

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "job-12345"])
    assert result.exit_code == 0
    assert "Migration Job Status:" in result.output
    assert "job-12345" in result.output
    assert "awaiting_approval" in result.output
    assert "pydantic" in result.output
    assert "migrate/pydantic-123" in result.output
    assert "/jobs/job-12345" in result.output


@patch("httpx.Client.get")
def test_cli_status_job_id_not_found(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    mock_get.return_value = mock_resp

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "missing-job"])
    assert result.exit_code == 1
    assert "Migration Job 'missing-job' not found" in result.output


@patch("httpx.Client.get")
def test_cli_status_job_id_api_unreachable(mock_get):
    mock_get.side_effect = httpx.ConnectError("Connection refused")

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "job-12345"])
    assert result.exit_code != 0
    assert "Migration Agent API is unreachable" in result.output


def test_format_stream_event_types():
    track_url = "http://localhost:3000/jobs/test-id"

    # hitl_gateway / awaiting approval
    is_done, is_success = format_stream_event(
        {
            "type": "hitl_gateway",
            "status": "awaiting_approval",
            "message": "Plan ready",
        },
        track_url,
    )
    assert not is_done and not is_success

    # healing_attempt
    is_done, is_success = format_stream_event(
        {
            "type": "healing_attempt",
            "file_path": "models.py",
            "attempt": 1,
            "max_attempts": 3,
        },
        track_url,
    )
    assert not is_done and not is_success

    # test_result
    is_done, is_success = format_stream_event(
        {"type": "test_result", "file_path": "models.py", "passed": True},
        track_url,
    )
    assert not is_done and not is_success

    # file_completed
    is_done, is_success = format_stream_event(
        {"type": "file_completed", "file_path": "models.py", "status": "SUCCESS"},
        track_url,
    )
    assert not is_done and not is_success

    # completion
    is_done, is_success = format_stream_event(
        {"type": "completion", "status": "complete", "message": "All done!"},
        track_url,
    )
    assert is_done and is_success

    # failed
    is_done, is_success = format_stream_event(
        {"type": "failed", "status": "failed", "error": "Refactor crashed"},
        track_url,
    )
    assert is_done and not is_success


@patch("shutil.which")
def test_get_compose_cmd_not_installed(mock_which):
    mock_which.return_value = None
    with pytest.raises(RuntimeError, match="Docker Compose is not installed"):
        get_compose_cmd()


@patch("shutil.which")
@patch("subprocess.run")
def test_run_compose_up_daemon_not_running(mock_run, mock_which):
    mock_which.return_value = "docker"
    # First call: docker compose version -> returncode 0
    # Second call: docker compose up -d --build -> failed because daemon is down
    mock_version = MagicMock(returncode=0)
    mock_up = MagicMock(
        returncode=1,
        stderr="error during connect: This error may indicate that the docker daemon is not running.",
        stdout="",
    )
    mock_run.side_effect = [mock_version, mock_up]

    with pytest.raises(RuntimeError, match="Docker daemon is not running"):
        run_compose_up()


@patch("shutil.which")
@patch("subprocess.run")
def test_run_compose_down_daemon_not_running(mock_run, mock_which):
    mock_which.return_value = "docker"
    mock_version = MagicMock(returncode=0)
    mock_down = MagicMock(
        returncode=1,
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        stdout="",
    )
    mock_run.side_effect = [mock_version, mock_down]

    with pytest.raises(RuntimeError, match="Docker daemon is not running"):
        run_compose_down()


def test_cli_mcp_git_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-git", "--help"])
    assert result.exit_code == 0
    assert "Start the mcp-server-git server" in result.output


def test_cli_mcp_ast_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-ast", "--help"])
    assert result.exit_code == 0
    assert "Start the mcp-server-ast server" in result.output


def test_cli_mcp_docs_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["mcp-docs", "--help"])
    assert result.exit_code == 0
    assert "Start the mcp-server-docs server" in result.output
