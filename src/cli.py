from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from typing import Any

import click
import httpx

from src.core.config import Settings, load_config
from src.core.logging import get_logger, setup_logging

logger = get_logger("cli")


def get_compose_cmd() -> list[str]:
    """Find docker compose executable command."""
    if shutil.which("docker"):
        # Check if 'docker compose' works
        try:
            res = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return ["docker", "compose"]
        except (subprocess.SubprocessError, OSError):
            pass

    if shutil.which("docker-compose"):
        return ["docker-compose"]

    raise RuntimeError(
        "Docker Compose is not installed. "
        "Please install Docker Desktop or ensure 'docker compose' is in your PATH."
    )


def _is_docker_daemon_error(stderr: str) -> bool:
    """Check if error output indicates Docker daemon is not running."""
    err_lower = stderr.lower()
    return any(
        term in err_lower
        for term in [
            "cannot connect",
            "error during connect",
            "daemon is not running",
            "is the docker daemon running",
        ]
    )


def run_compose_up(build: bool = True, compose_file: str | None = None) -> bool:
    """Run docker compose up -d."""
    cmd = get_compose_cmd()
    if compose_file:
        cmd.extend(["-f", compose_file])
    cmd.extend(["up", "-d"])
    if build:
        cmd.append("--build")

    click.echo(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        err_msg = (result.stderr or result.stdout or "").strip()
        if _is_docker_daemon_error(err_msg):
            raise RuntimeError(
                "Docker daemon is not running. Please start Docker Desktop or the Docker service."
            )
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {err_msg}"
        )
    return True


def run_compose_down(compose_file: str | None = None) -> bool:
    """Run docker compose down cleanly."""
    cmd = get_compose_cmd()
    if compose_file:
        cmd.extend(["-f", compose_file])
    cmd.append("down")

    click.echo(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        err_msg = (result.stderr or result.stdout or "").strip()
        if _is_docker_daemon_error(err_msg):
            raise RuntimeError(
                "Docker daemon is not running. Please start Docker Desktop or the Docker service."
            )
        return False
    return True


def check_api_health(api_url: str = "http://localhost:8000/health") -> bool:
    """Check if FastAPI is healthy."""
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(api_url)
            return resp.status_code == 200 and resp.json().get("status") == "ok"
    except (httpx.HTTPError, OSError):
        return False


def check_web_health(web_url: str = "http://localhost:3000") -> bool:
    """Check if Next.js frontend is accessible."""
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(web_url)
            return resp.status_code in (200, 304, 307, 308)
    except (httpx.HTTPError, OSError):
        return False


def check_postgres_health() -> bool:
    """Check postgres container status via docker compose."""
    try:
        cmd = get_compose_cmd() + ["ps", "--format", "json", "postgres"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    state = data.get("State", "").lower()
                    health = data.get("Health", "").lower()
                    if state == "running" and ("healthy" in health or not health):
                        return True
                except json.JSONDecodeError:
                    if "running" in res.stdout.lower() or "up" in res.stdout.lower():
                        return True
        return False
    except (subprocess.SubprocessError, OSError):
        return False


def check_all_services_healthy(
    timeout: int = 60,
    api_url: str = "http://localhost:8000/health",
    web_url: str = "http://localhost:3000",
) -> dict[str, bool]:
    """Poll services until healthy or timeout."""
    start_time = time.time()
    statuses = {"postgres": False, "api": False, "web": False}

    click.echo(f"Waiting for services to become healthy (timeout: {timeout}s)...")
    while time.time() - start_time < timeout:
        if not statuses["postgres"]:
            statuses["postgres"] = check_postgres_health()
        if not statuses["api"]:
            statuses["api"] = check_api_health(api_url)
        if not statuses["web"]:
            statuses["web"] = check_web_health(web_url)

        if all(statuses.values()):
            return statuses

        time.sleep(2)

    return statuses


def handle_api_error(exc: Exception, api_url: str) -> None:
    """Print helpful error message when API is unreachable or fails."""
    if isinstance(
        exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
    ):
        click.echo(
            f"Error: Migration Agent API is unreachable at {api_url}.\n"
            "Is the service running? Start it with 'migration-agent start'.",
            err=True,
        )
    else:
        click.echo(f"Error communicating with Migration Agent API: {exc}", err=True)


def _resolve_api_url(settings: Settings, api_url: str | None) -> str:
    """Resolve API base URL from parameter or settings."""
    return api_url.rstrip("/") if api_url else f"http://localhost:{settings.api_port}"


def format_stream_event(event: dict[str, Any], track_url: str) -> tuple[bool, bool]:
    """Format and print stream event. Returns (is_terminal, is_successful)."""
    event_type = event.get("type", "")
    status_str = event.get("status", "")
    msg = event.get("message")

    if event_type == "hitl_gateway" or status_str == "awaiting_approval":
        click.secho(
            f"  [AWAITING_APPROVAL] {msg or 'Migration plan ready for human review.'}",
            fg="yellow",
            bold=True,
        )
        click.secho(f"  Review and approve files in Web UI: {track_url}", fg="cyan")
        return False, False

    if event_type == "healing_attempt":
        attempt = event.get("attempt")
        max_attempts = event.get("max_attempts", 3)
        file_path = event.get("file_path", "")
        click.echo(f"  [HEALING] Attempt {attempt}/{max_attempts} for {file_path}")
        return False, False

    if event_type == "test_result":
        file_path = event.get("file_path", "")
        passed = event.get("passed", False)
        symbol = "✓" if passed else "✗"
        fg_col = "green" if passed else "red"
        click.secho(
            f"  [TEST] [{symbol}] {file_path} - {'Passed' if passed else 'Failed'}",
            fg=fg_col,
        )
        return False, False

    if event_type == "file_completed":
        file_path = event.get("file_path", "")
        f_status = event.get("status", "")
        click.echo(f"  [COMPLETED] {file_path} (status: {f_status})")
        return False, False

    if status_str == "complete" or event_type == "completion":
        click.secho(
            f"\n[COMPLETE] {msg or 'Migration completed successfully!'}",
            fg="green",
            bold=True,
        )
        return True, True

    if status_str == "failed" or event_type == "failed":
        err = event.get("error") or msg or "Unknown error"
        click.secho(f"\n[FAILED] {err}", fg="red", bold=True)
        return True, False

    if msg:
        tag = (status_str or event_type or "PROGRESS").upper()
        click.echo(f"  [{tag}] {msg}")

    if event.get("is_final"):
        return True, status_str == "complete"

    return False, False


async def poll_job_progress(
    api_base: str, job_id: str, track_url: str, poll_interval: float = 2.0
) -> bool:
    """Poll GET /api/jobs/{id} until completion or failure."""
    last_status = None
    with httpx.Client(timeout=5.0) as client:
        while True:
            try:
                resp = client.get(f"{api_base}/api/jobs/{job_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    current_status = data.get("status")
                    if current_status != last_status:
                        last_status = current_status
                        click.echo(
                            f"  [{current_status.upper()}] Status: {current_status}"
                        )
                        if current_status == "awaiting_approval":
                            click.secho(
                                f"  [AWAITING_APPROVAL] Plan ready! Approve files in Web UI: {track_url}",
                                fg="yellow",
                                bold=True,
                            )
                    if current_status == "complete":
                        click.secho(
                            "\nMigration completed successfully!", fg="green", bold=True
                        )
                        return True
                    if current_status == "failed":
                        click.secho(
                            f"\nMigration failed: {data.get('error')}",
                            fg="red",
                            bold=True,
                        )
                        return False
            except (httpx.HTTPError, OSError) as e:
                click.echo(f"  [WARN] Polling connection error: {e}")
            await asyncio.sleep(poll_interval)


async def stream_job_progress(api_base: str, job_id: str, track_url: str) -> bool:
    """Stream real-time status updates via WebSocket with fallback to HTTP polling."""
    click.echo(f"Waiting for completion of Migration Job {job_id}...")
    ws_url = (
        api_base.replace("http://", "ws://").replace("https://", "wss://")
        + f"/api/jobs/{job_id}/stream"
    )

    try:
        import websockets

        async with websockets.connect(ws_url, close_timeout=2) as ws:
            while True:
                msg = await ws.recv()
                event = json.loads(msg)
                is_done, is_success = format_stream_event(event, track_url)
                if is_done:
                    return is_success
    except Exception:  # noqa: BLE001
        # Fall back to HTTP polling if WebSocket is unavailable or disconnects
        return await poll_job_progress(api_base, job_id, track_url)


@click.group()
@click.option(
    "--config", "config_file", type=click.Path(exists=False), help="Path to config.yaml"
)
@click.option(
    "--env-file", "env_file", type=click.Path(exists=False), help="Path to .env file"
)
@click.option(
    "--workspace-path",
    "workspace_path",
    type=str,
    help="Override workspace directory path",
)
@click.option(
    "--model-lite", "model_lite", type=str, help="Override lite model identifier"
)
@click.option(
    "--model-default",
    "model_default",
    type=str,
    help="Override default model identifier",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config_file: str | None = None,
    env_file: str | None = None,
    workspace_path: str | None = None,
    model_lite: str | None = None,
    model_default: str | None = None,
) -> None:
    """Autonomous Codebase Refactoring & Migration Agent CLI."""
    ctx.ensure_object(dict)
    settings = load_config(
        config_path=config_file,
        env_file=env_file,
        workspace_path=workspace_path,
        model_lite=model_lite,
        model_default=model_default,
    )
    ctx.obj["settings"] = settings
    setup_logging(json_format=settings.json_logs, log_level=settings.log_level)


@cli.command()
@click.option(
    "--build/--no-build", default=True, help="Build Docker images before starting"
)
@click.option("--timeout", default=60, type=int, help="Health check timeout in seconds")
@click.option(
    "--open/--no-open", "open_browser", default=True, help="Open browser after starting"
)
@click.pass_context
def start(ctx: click.Context, build: bool, timeout: int, open_browser: bool) -> None:
    """Start the full infrastructure stack (PostgreSQL, FastAPI API, Next.js web)."""
    settings: Settings = ctx.obj["settings"]
    click.echo("Starting Migration Agent infrastructure stack...")

    try:
        run_compose_up(build=build)
    except (RuntimeError, OSError) as e:
        click.echo(f"Error starting services: {e}", err=True)
        sys.exit(1)

    api_url = f"http://localhost:{settings.api_port}/health"
    web_url = settings.web_url
    statuses = check_all_services_healthy(
        timeout=timeout, api_url=api_url, web_url=web_url
    )
    click.echo("\nService Health Status:")
    for service, healthy in statuses.items():
        symbol = "✓" if healthy else "✗"
        status_text = "Healthy" if healthy else "Unhealthy/Timeout"
        click.echo(f"  [{symbol}] {service:<10}: {status_text}")

    if all(statuses.values()):
        click.echo("\nAll services are healthy and running!")
        click.echo(f"  • Web Dashboard: {settings.web_url}")
        click.echo(f"  • API Docs:      http://localhost:{settings.api_port}/docs")
        click.echo(f"  • Database:      {settings.database_url}")

        if open_browser:
            click.echo(f"Opening browser to {settings.web_url}...")
            webbrowser.open(settings.web_url)
    else:
        click.echo(
            "\nWarning: Some services did not report healthy within the timeout.",
            err=True,
        )
        if not statuses.get("api", False):
            click.echo(
                f"Error: Migration Agent API is unreachable at http://localhost:{settings.api_port}. "
                "Check container logs with: docker compose logs api",
                err=True,
            )
        click.echo("Check container logs with: docker compose logs", err=True)
        sys.exit(1)


@cli.command()
def stop() -> None:
    """Stop the full infrastructure stack cleanly."""
    click.echo("Stopping Migration Agent stack...")
    try:
        if run_compose_down():
            click.echo("All services stopped cleanly.")
        else:
            click.echo("Failed to stop all services cleanly.", err=True)
            sys.exit(1)
    except (RuntimeError, OSError) as e:
        click.echo(f"Error stopping services: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("path_or_url")
@click.option(
    "--target",
    "-t",
    required=True,
    help="Target library being migrated (e.g., 'pydantic').",
)
@click.option(
    "--wait/--no-wait",
    "-w",
    default=False,
    help="Wait for completion, printing streaming status updates to the terminal.",
)
@click.option(
    "--api-url",
    default=None,
    help="Base URL of the Migration Agent API.",
)
@click.option(
    "--workspace-base-dir",
    default=None,
    help="Optional directory path to house the Agent Workspace.",
)
@click.option(
    "--workspace-path",
    default=None,
    help="Override workspace directory path.",
)
@click.option(
    "--model-lite",
    default=None,
    help="Override lite model identifier.",
)
@click.option(
    "--model-default",
    default=None,
    help="Override default model identifier.",
)
@click.pass_context
def run(
    ctx: click.Context,
    path_or_url: str,
    target: str,
    wait: bool,
    api_url: str | None,
    workspace_base_dir: str | None,
    workspace_path: str | None,
    model_lite: str | None,
    model_default: str | None,
) -> None:
    """Submit a Migration Job directly via the REST API for scriptable/automated use."""
    settings: Settings = ctx.obj["settings"]
    if workspace_path:
        settings.workspace_path = workspace_path
    if model_lite:
        settings.model_lite = model_lite
    if model_default:
        settings.model_default = model_default

    api_base = _resolve_api_url(settings, api_url)
    payload: dict[str, Any] = {
        "path_or_url": path_or_url,
        "target_library": target,
    }
    if workspace_base_dir:
        payload["workspace_base_dir"] = workspace_base_dir

    click.echo(f"Submitting Migration Job for '{path_or_url}' (target: {target})...")

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_base}/api/jobs", json=payload)
    except Exception as exc:  # noqa: BLE001
        handle_api_error(exc, api_base)
        sys.exit(1)

    if resp.status_code != 201:
        click.echo(
            f"Error submitting Migration Job ({resp.status_code}): {resp.text}",
            err=True,
        )
        sys.exit(1)

    data = resp.json()
    job_id = data["job_id"]
    initial_status = data.get("status", "scanning")
    web_url = settings.web_url.rstrip("/")
    track_url = f"{web_url}/jobs/{job_id}"

    click.echo("\nMigration Job submitted successfully!")
    click.echo(f"  • Job ID:   {job_id}")
    click.echo(f"  • Status:   {initial_status}")
    click.echo(f"  • Track UI: {track_url}\n")

    if wait:
        success = asyncio.run(stream_job_progress(api_base, job_id, track_url))
        if not success:
            sys.exit(1)


@cli.command()
@click.argument("job_id", required=False)
@click.option(
    "--api-url",
    default=None,
    help="Base URL of the Migration Agent API.",
)
@click.pass_context
def status(ctx: click.Context, job_id: str | None, api_url: str | None) -> None:
    """Query status of a specific Migration Job, or inspect stack containers."""
    if not job_id:
        try:
            cmd = get_compose_cmd() + ["ps"]
            res = subprocess.run(cmd, check=False)
            if res.returncode != 0:
                click.echo("Error: Failed to query docker compose status.", err=True)
                sys.exit(1)
        except RuntimeError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        return

    settings: Settings = ctx.obj["settings"]
    api_base = _resolve_api_url(settings, api_url)

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{api_base}/api/jobs/{job_id}")
    except Exception as exc:  # noqa: BLE001
        handle_api_error(exc, api_base)
        sys.exit(1)

    if resp.status_code == 404:
        click.echo(f"Error: Migration Job '{job_id}' not found.", err=True)
        sys.exit(1)

    if resp.status_code != 200:
        click.echo(
            f"Error retrieving job status ({resp.status_code}): {resp.text}", err=True
        )
        sys.exit(1)

    data = resp.json()
    web_url = settings.web_url.rstrip("/")

    click.echo("\nMigration Job Status:")
    click.echo(f"  • Job ID:               {data.get('job_id')}")
    click.echo(f"  • Status:               {data.get('status')}")
    click.echo(f"  • Target Library:       {data.get('target_library')}")
    click.echo(f"  • Source:               {data.get('source')}")
    click.echo(f"  • Created At:           {data.get('created_at')}")
    click.echo(f"  • Updated At:           {data.get('updated_at')}")
    click.echo(f"  • Scanned Files Count:  {data.get('scanned_files_count', 0)}")
    click.echo(f"  • Planned Files Count:  {data.get('plan_files_count', 0)}")
    click.echo(f"  • Approved Files Count: {data.get('approved_files_count', 0)}")
    if data.get("branch_name"):
        click.echo(f"  • Branch Name:          {data.get('branch_name')}")
    if data.get("workspace_path"):
        click.echo(f"  • Workspace Path:       {data.get('workspace_path')}")
    if data.get("error"):
        click.secho(f"  • Error:                {data.get('error')}", fg="red")
    click.echo(f"  • Web UI Track URL:     {web_url}/jobs/{job_id}\n")


@cli.command("mcp-git")
def mcp_git() -> None:
    """Start the mcp-server-git server with stdio transport."""
    from src.mcp_servers.git_server import main as git_server_main

    git_server_main()


@cli.command("mcp-ast")
def mcp_ast() -> None:
    """Start the mcp-server-ast server with stdio transport."""
    from src.mcp_servers.ast_server import main as ast_server_main

    ast_server_main()


@cli.command("mcp-docs")
def mcp_docs() -> None:
    """Start the mcp-server-docs server with stdio transport."""
    from src.mcp_servers.docs_server import main as docs_server_main

    docs_server_main()


def main() -> None:
    """CLI entry point for pyproject.toml script."""
    cli(obj={})


if __name__ == "__main__":
    main()
