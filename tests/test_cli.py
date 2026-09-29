from unittest.mock import patch

from click.testing import CliRunner

from src.cli import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Autonomous Codebase Refactoring & Migration Agent" in result.output
    assert "start" in result.output


def test_cli_start_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "Start the full infrastructure stack" in result.output


@patch("src.cli.run_compose_up")
@patch("src.cli.check_all_services_healthy")
def test_cli_start_success(mock_check_health, mock_compose_up):
    mock_compose_up.return_value = True
    mock_check_health.return_value = {
        "postgres": True,
        "api": True,
        "web": True,
    }

    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--no-open"])
    assert result.exit_code == 0
    assert "All services are healthy" in result.output
    mock_compose_up.assert_called_once()
    mock_check_health.assert_called_once()


@patch("src.cli.run_compose_up")
def test_cli_start_docker_error(mock_compose_up):
    mock_compose_up.side_effect = RuntimeError("Docker daemon is not running")

    runner = CliRunner()
    result = runner.invoke(cli, ["start"])
    assert result.exit_code != 0
    assert "Docker daemon is not running" in result.output


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


