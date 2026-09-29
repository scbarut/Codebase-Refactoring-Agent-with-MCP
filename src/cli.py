from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import webbrowser

import click
import httpx

from src.core.config import load_config
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
        "Docker Compose is not installed or Docker is not running. "
        "Please install Docker Desktop or ensure 'docker compose' is in your PATH."
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
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return True


def run_compose_down(compose_file: str | None = None) -> bool:
    """Run docker compose down."""
    cmd = get_compose_cmd()
    if compose_file:
        cmd.extend(["-f", compose_file])
    cmd.append("down")

    click.echo(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode == 0


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


def check_all_services_healthy(timeout: int = 60) -> dict[str, bool]:
    """Poll services until healthy or timeout."""
    start_time = time.time()
    statuses = {"postgres": False, "api": False, "web": False}

    click.echo(f"Waiting for services to become healthy (timeout: {timeout}s)...")
    while time.time() - start_time < timeout:
        if not statuses["postgres"]:
            statuses["postgres"] = check_postgres_health()
        if not statuses["api"]:
            statuses["api"] = check_api_health()
        if not statuses["web"]:
            statuses["web"] = check_web_health()

        if all(statuses.values()):
            return statuses

        time.sleep(2)

    return statuses


@click.group()
@click.option(
    "--config", "config_file", type=click.Path(exists=False), help="Path to config.yaml"
)
@click.pass_context
def cli(ctx: click.Context, config_file: str | None) -> None:
    """Autonomous Codebase Refactoring & Migration Agent CLI."""
    ctx.ensure_object(dict)
    settings = load_config(config_path=config_file)
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
    settings = ctx.obj["settings"]
    click.echo("Starting Migration Agent infrastructure stack...")

    try:
        run_compose_up(build=build)
    except (RuntimeError, OSError) as e:
        click.echo(f"Error starting services: {e}", err=True)
        sys.exit(1)

    statuses = check_all_services_healthy(timeout=timeout)
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
        click.echo("Check container logs with: docker compose logs", err=True)
        sys.exit(1)


@cli.command()
def stop() -> None:
    """Stop the full infrastructure stack."""
    click.echo("Stopping Migration Agent stack...")
    if run_compose_down():
        click.echo("All services stopped cleanly.")
    else:
        click.echo("Failed to stop all services cleanly.", err=True)
        sys.exit(1)


@cli.command()
def status() -> None:
    """Check the status of all stack services."""
    cmd = get_compose_cmd() + ["ps"]
    subprocess.run(cmd, check=False)


@cli.command("mcp-git")
def mcp_git() -> None:
    """Start the mcp-server-git server with stdio transport."""
    from src.mcp_servers.git_server import main as git_server_main
    git_server_main()


def main() -> None:
    """CLI entry point for pyproject.toml script."""
    cli(obj={})


if __name__ == "__main__":
    main()
