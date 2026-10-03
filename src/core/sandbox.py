from __future__ import annotations

import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import docker.errors
from docker.models.containers import Container

import docker
from src.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SANDBOX_IMAGE_PREFIX = "ghcr.io/scbarut/migration-sandbox"
DEFAULT_DOCKERFILE_PATH = Path("docker/sandbox/Dockerfile")


@dataclass
class TestResult:
    """Structured result of running pytest inside the Sandbox."""

    __test__ = False
    passed: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    traceback: str = ""
    output: str = ""


@dataclass
class InstallResult:
    """Structured result of dependency installation inside the Sandbox."""

    has_deps: bool
    command: str | None = None
    exit_code: int = 0
    output: str = ""


WINDOWS_ONLY_PACKAGES: set[str] = {
    "pywin32",
    "pypiwin32",
    "pywinpty",
    "wexpect",
    "winloop",
    "pythonnet",
}


def _decode_requirements_bytes(raw_bytes: bytes) -> str:
    """Decode requirements.txt raw bytes handling UTF-8, UTF-16 LE/BE, and BOMs."""
    text: str
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw_bytes.decode("utf-16", errors="replace")
    elif raw_bytes.startswith(b"\xef\xbb\xbf"):
        text = raw_bytes.decode("utf-8-sig", errors="replace")
    elif b"\x00" in raw_bytes[:50]:
        try:
            text = raw_bytes.decode("utf-16", errors="replace")
        except UnicodeError:
            text = raw_bytes.decode("utf-8", errors="replace")
    else:
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin-1", errors="replace")
    return text.lstrip("\ufeff")


def sanitize_requirements_content(content: str) -> str:
    """Filter out Windows-only packages and Windows environment markers for Linux container."""
    clean_lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        # Strip Windows platform markers
        if ";" in line:
            marker = line.split(";", 1)[1].lower()
            if "win32" in marker or "windows" in marker:
                continue
        # Extract base package name
        pkg_match = re.match(r"^([a-zA-Z0-9_\-\.]+)", line)
        if pkg_match:
            pkg_name = pkg_match.group(1).lower().replace("-", "_")
            if pkg_name in WINDOWS_ONLY_PACKAGES:
                continue
        clean_lines.append(line)
    return "\n".join(clean_lines) + "\n" if clean_lines else ""


class SandboxManager:
    """Manages the per-job Docker Sandbox container lifecycle.

    Responsibilities:
    - Checks for (and pulls or builds) the multi-version Python Sandbox base image.
    - Spins up a fresh container per Migration Job with Agent Workspace mounted.
    - Detects and installs project dependencies (requirements.txt, pyproject.toml, setup.py).
    - Runs scoped pytest executions targeting specific modules.
    - Extracts structured test outputs and tracebacks.
    - Destroys containers cleanly when jobs complete.
    """

    def __init__(
        self,
        docker_client: docker.DockerClient | None = None,
        dockerfile_path: Path | str | None = None,
        image_prefix: str = DEFAULT_SANDBOX_IMAGE_PREFIX,
    ) -> None:
        self._client = docker_client
        self.image_prefix = image_prefix
        if dockerfile_path is not None:
            self.dockerfile_path = Path(dockerfile_path).resolve()
        else:
            self.dockerfile_path = Path(DEFAULT_DOCKERFILE_PATH).resolve()

    @property
    def client(self) -> docker.DockerClient:
        """Lazily initialize Docker client if not injected."""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def get_image_tag(self, python_version: str = "3.11") -> str:
        """Return the fully-qualified image tag for the specified Python version."""
        cleaned_version = python_version.strip().lstrip("v")
        return f"{self.image_prefix}:py{cleaned_version}"

    def ensure_image(self, python_version: str = "3.11") -> str:
        """Check for image locally, pull if available from registry, or build from Dockerfile.

        Returns the confirmed image tag.
        """
        image_tag = self.get_image_tag(python_version)

        # 1. Check if image exists locally
        try:
            self.client.images.get(image_tag)
            logger.info("sandbox_image_found_locally", image=image_tag)
            return image_tag
        except docker.errors.ImageNotFound:
            logger.info("sandbox_image_not_local_attempting_pull", image=image_tag)
        except docker.errors.DockerException as e:
            logger.warning("sandbox_image_check_failed", image=image_tag, error=str(e))

        # 2. Try pulling from remote registry (GHCR)
        try:
            logger.info("sandbox_image_pulling", image=image_tag)
            self.client.images.pull(image_tag)
            logger.info("sandbox_image_pulled_successfully", image=image_tag)
            return image_tag
        except (docker.errors.ImageNotFound, docker.errors.APIError, docker.errors.DockerException) as pull_err:
            logger.info(
                "sandbox_image_pull_failed_building_local",
                image=image_tag,
                reason=str(pull_err),
            )

        # 3. Build from Dockerfile
        cleaned_version = python_version.strip().lstrip("v")
        context_dir = self.dockerfile_path.parent
        dockerfile_rel_or_name = self.dockerfile_path.name

        logger.info(
            "sandbox_image_building",
            dockerfile=str(self.dockerfile_path),
            python_version=cleaned_version,
            tag=image_tag,
        )

        build_args = {"PYTHON_VERSION": cleaned_version}
        self.client.images.build(
            path=str(context_dir),
            dockerfile=dockerfile_rel_or_name,
            buildargs=build_args,
            tag=image_tag,
            rm=True,
        )
        logger.info("sandbox_image_built_successfully", image=image_tag)
        return image_tag

    def _translate_to_host_path(self, container_path: Path | str) -> str:
        """Translate a container-internal path to the corresponding host path for sibling container volume mounts (DooD)."""
        c_path = str(Path(container_path).resolve()).replace("\\", "/")

        try:
            current_container = None
            hostname = socket.gethostname()
            for identifier in (hostname, "migration-agent-api"):
                try:
                    current_container = self.client.containers.get(identifier)
                    break
                except Exception:  # noqa: BLE001, S112
                    continue

            if current_container is not None:
                mounts = current_container.attrs.get("Mounts", [])
                for mount in mounts:
                    dest = mount.get("Destination", "").replace("\\", "/")
                    source = mount.get("Source", "")
                    if dest and (c_path == dest or c_path.startswith(dest + "/")):
                        rel = c_path[len(dest):].lstrip("/")
                        # If the host source is a Windows path (e.g. C:\Users\...)
                        if "\\" in source or (len(source) > 1 and source[1] == ":"):
                            host_rel = rel.replace("/", "\\")
                            sep = "\\"
                            clean_src = source.rstrip(sep)
                            return f"{clean_src}{sep}{host_rel}" if host_rel else source
                        return f"{source.rstrip('/')}/{rel}" if rel else source
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to translate container path to host path", error=str(exc))

        return str(container_path)

    def create_sandbox(
        self,
        workspace_path: Path | str,
        python_version: str = "3.11",
    ) -> Container:
        """Spins up a fresh container per Migration Job with Agent Workspace mounted read-write.

        Args:
            workspace_path: Path to the Agent Workspace on the host.
            python_version: Target Python version (e.g. '3.11', '3.10').

        Returns:
            The started Docker Container instance.
        """
        image_tag = self.ensure_image(python_version=python_version)
        resolved_workspace = Path(workspace_path).resolve()

        if not resolved_workspace.exists():
            raise FileNotFoundError(f"Workspace path does not exist: {resolved_workspace}")

        host_workspace = self._translate_to_host_path(resolved_workspace)

        volume_mount = {
            host_workspace: {
                "bind": "/workspace",
                "mode": "rw",
            }
        }

        logger.info(
            "creating_sandbox_container",
            workspace=host_workspace,
            image=image_tag,
        )

        container = self.client.containers.run(
            image_tag,
            command=["tail", "-f", "/dev/null"],
            detach=True,
            working_dir="/workspace",
            volumes=volume_mount,
            labels={
                "managed-by": "migration-agent",
                "python-version": python_version,
                "workspace": str(resolved_workspace),
            },
        )

        logger.info("sandbox_container_created", container_id=getattr(container, "id", "")[:12])
        return container

    @asynccontextmanager
    async def managed_sandbox(
        self,
        workspace_path: Path | str,
        target_library: str | None = None,
        python_version: str = "3.11",
    ) -> AsyncIterator[Container | None]:
        """Async context manager that provisions, configures, and safely tears down a Sandbox container.

        Gracefully degrades by yielding None if Docker is unavailable or errors occur during startup.
        Guarantees container cleanup upon exit.
        """
        container: Container | None = None
        try:
            container = self.create_sandbox(
                workspace_path=workspace_path,
                python_version=python_version,
            )
            self.install_deps(
                container,
                workspace_path=workspace_path,
                target_library=target_library,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("sandbox_lifecycle_init_failed", error=str(exc))
            if container is not None:
                self.destroy_sandbox(container)
                container = None

        try:
            yield container
        finally:
            if container is not None:
                self.destroy_sandbox(container)

    def install_deps(
        self,
        container: Container,
        workspace_path: Path | str | None = None,
        target_library: str | None = None,
    ) -> InstallResult:
        """Detects requirements.txt, pyproject.toml, or setup.py and runs pip install inside the container.

        Args:
            container: Running Sandbox container.
            workspace_path: Optional host path to check for dependency files. If not provided,
                           checks inside the container's /workspace.
            target_library: Optional target library specifier (e.g. 'pydantic>=2.0' or 'pydantic')
                            to upgrade inside the sandbox for migration verification.

        Returns:
            InstallResult detailing executed command and output.
        """
        cmd = None
        has_reqs = False
        has_pyproj = False
        has_setup = False
        sanitized_req_path = None
        sanitized_lines: list[str] = []

        if workspace_path is not None:
            wpath = Path(workspace_path).resolve()
            has_reqs = (wpath / "requirements.txt").is_file()
            has_pyproj = (wpath / "pyproject.toml").is_file()
            has_setup = (wpath / "setup.py").is_file()
        else:
            # Check inside container
            res_reqs = container.exec_run("test -f /workspace/requirements.txt")
            has_reqs = (res_reqs.exit_code == 0)
            if not has_reqs:
                res_pyproj = container.exec_run("test -f /workspace/pyproject.toml")
                has_pyproj = (res_pyproj.exit_code == 0)
            if not has_reqs and not has_pyproj:
                res_setup = container.exec_run("test -f /workspace/setup.py")
                has_setup = (res_setup.exit_code == 0)

        if has_reqs:
            # Prepare sanitized UTF-8 requirements without Windows-only packages
            if workspace_path is not None:
                wpath = Path(workspace_path).resolve()
                raw_bytes = (wpath / "requirements.txt").read_bytes()
                decoded_text = _decode_requirements_bytes(raw_bytes)
                clean_text = sanitize_requirements_content(decoded_text)
                sanitized_file = wpath / ".migration_agent_requirements.txt"
                sanitized_file.write_text(clean_text, encoding="utf-8")
                sanitized_req_path = ".migration_agent_requirements.txt"
                sanitized_lines = [l.strip() for l in clean_text.splitlines() if l.strip()]
            else:
                # Inside container: decode and write sanitized version
                sanitize_script = (
                    "import re\n"
                    "WINDOWS_ONLY = {'pywin32', 'pypiwin32', 'pywinpty', 'wexpect', 'winloop', 'pythonnet'}\n"
                    "raw = open('/workspace/requirements.txt', 'rb').read()\n"
                    "if raw.startswith(b'\\xff\\xfe'): text = raw.decode('utf-16-le', errors='replace')\n"
                    "elif raw.startswith(b'\\xfe\\xff'): text = raw.decode('utf-16-be', errors='replace')\n"
                    "elif raw.startswith(b'\\xef\\xbb\\xbf'): text = raw.decode('utf-8-sig', errors='replace')\n"
                    "elif b'\\x00' in raw[:50]: text = raw.decode('utf-16', errors='replace')\n"
                    "else:\n"
                    "    try: text = raw.decode('utf-8')\n"
                    "    except: text = raw.decode('latin-1', errors='replace')\n"
                    "clean = []\n"
                    "for l in text.splitlines():\n"
                    "    s = l.strip()\n"
                    "    if not s or s.startswith('#'): continue\n"
                    "    if ';' in s and ('win32' in s.lower() or 'windows' in s.lower()): continue\n"
                    "    m = re.match(r'^([a-zA-Z0-9_\\-\\.]+)', s)\n"
                    "    if m and m.group(1).lower().replace('-', '_') in WINDOWS_ONLY: continue\n"
                    "    clean.append(s)\n"
                    "open('/workspace/.migration_agent_requirements.txt', 'w', encoding='utf-8').write('\\n'.join(clean) + '\\n')\n"
                )
                container.exec_run(f"python3 -c \"{sanitize_script}\"")
                sanitized_req_path = ".migration_agent_requirements.txt"

            req_target = sanitized_req_path or "requirements.txt"
            cmd = f"pip install --no-cache-dir -r {req_target}"
        elif has_pyproj or has_setup:
            cmd = "pip install --no-cache-dir ."

        combined_output = ""
        exit_code = 0

        if cmd is not None:
            logger.info("sandbox_installing_deps", command=cmd, container_id=container.id[:12])
            exec_res = container.exec_run(
                cmd,
                workdir="/workspace",
                environment={"PYTHONPATH": "/workspace"},
                demux=True,
            )
            stdout_bytes, stderr_bytes = (
                exec_res.output if isinstance(exec_res.output, tuple) else (exec_res.output, b"")
            )
            stdout_str = (stdout_bytes or b"").decode("utf-8", errors="replace")
            stderr_str = (stderr_bytes or b"").decode("utf-8", errors="replace")
            combined_output = f"{stdout_str}\n{stderr_str}".strip()
            exit_code = exec_res.exit_code

            # Resilient fallback: if batch pip install failed on requirements.txt, try line-by-line
            if exit_code != 0 and has_reqs:
                logger.warning(
                    "sandbox_batch_install_failed_attempting_line_by_line",
                    error=stderr_str[-300:] if stderr_str else stdout_str[-300:],
                )
                if not sanitized_lines and sanitized_req_path and workspace_path is not None:
                    san_f = Path(workspace_path).resolve() / sanitized_req_path
                    if san_f.is_file():
                        sanitized_lines = [l.strip() for l in san_f.read_text(encoding="utf-8").splitlines() if l.strip()]

                succeeded_pkgs: list[str] = []
                failed_pkgs: list[str] = []
                for pkg_line in sanitized_lines:
                    line_cmd = f"pip install --no-cache-dir {pkg_line}"
                    res_line = container.exec_run(
                        line_cmd,
                        workdir="/workspace",
                        environment={"PYTHONPATH": "/workspace"},
                        demux=True,
                    )
                    if res_line.exit_code == 0:
                        succeeded_pkgs.append(pkg_line)
                    else:
                        failed_pkgs.append(pkg_line)

                logger.info(
                    "sandbox_line_by_line_install_completed",
                    succeeded_count=len(succeeded_pkgs),
                    failed_count=len(failed_pkgs),
                )
                if succeeded_pkgs:
                    # Partial installation succeeded, allowing tests to proceed with available packages
                    exit_code = 0
                    combined_output += f"\nLine-by-line installed {len(succeeded_pkgs)} packages ({len(failed_pkgs)} failed)."

        # Upgrade target library if requested
        if target_library:
            target_cmd = f"pip install --no-cache-dir --upgrade {target_library}"
            logger.info("sandbox_upgrading_target_library", command=target_cmd, container_id=container.id[:12])
            exec_target = container.exec_run(
                target_cmd,
                workdir="/workspace",
                environment={"PYTHONPATH": "/workspace"},
                demux=True,
            )
            t_out, t_err = (
                exec_target.output if isinstance(exec_target.output, tuple) else (exec_target.output, b"")
            )
            t_out_str = (t_out or b"").decode("utf-8", errors="replace")
            t_err_str = (t_err or b"").decode("utf-8", errors="replace")
            target_output = f"{t_out_str}\n{t_err_str}".strip()

            combined_output = f"{combined_output}\n{target_output}".strip() if combined_output else target_output
            cmd = f"{cmd} && {target_cmd}" if cmd else target_cmd
            if exit_code == 0:
                exit_code = exec_target.exit_code

        if not cmd:
            logger.info("sandbox_no_deps_detected")
            return InstallResult(has_deps=False, command=None, exit_code=0, output="No dependencies found.")

        logger.info(
            "sandbox_deps_installed",
            exit_code=exit_code,
            command=cmd,
        )

        return InstallResult(
            has_deps=True,
            command=cmd,
            exit_code=exit_code,
            output=combined_output,
        )

    def run_tests(
        self,
        container: Container,
        module_path: str | Path | None = None,
    ) -> TestResult:
        """Executes pytest targeting the specified module with structured output.

        Args:
            container: Running Sandbox container.
            module_path: Path to module or test file (e.g. 'tests/test_rules.py').
                        If None, runs pytest on all tests.

        Returns:
            TestResult containing pass/fail status, exit code, and extracted traceback.
        """
        if module_path:
            posix_path = Path(module_path).as_posix()
            cmd = f"python -m pytest {posix_path} --tb=long -q"
        else:
            cmd = "python -m pytest --tb=long -q"

        logger.info("sandbox_running_tests", command=cmd, container_id=container.id[:12])
        exec_res = container.exec_run(
            cmd,
            workdir="/workspace",
            environment={"PYTHONPATH": "/workspace"},
            demux=True,
        )

        stdout_bytes, stderr_bytes = exec_res.output if isinstance(exec_res.output, tuple) else (exec_res.output, b"")
        stdout_str = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_str = (stderr_bytes or b"").decode("utf-8", errors="replace")
        combined_output = f"{stdout_str}\n{stderr_str}".strip()

        passed = (exec_res.exit_code == 0)
        traceback_text = ""
        # Pytest exit code 5 means NO_TESTS_COLLECTED.
        # If no tests exist for this target, verify python syntax via py_compile.
        if exec_res.exit_code == 5:
            if module_path:
                posix_target = Path(module_path).as_posix()
                compile_res = container.exec_run(
                    f"python -m py_compile {posix_target}",
                    workdir="/workspace",
                    environment={"PYTHONPATH": "/workspace"},
                )
                passed = (compile_res.exit_code == 0)
                if not passed:
                    c_out = compile_res.output if isinstance(compile_res.output, bytes) else b""
                    traceback_text = c_out.decode("utf-8", errors="replace").strip()
            else:
                passed = True
        elif not passed:
            traceback_text = self._extract_traceback(stdout_str, stderr_str)

        logger.info(
            "sandbox_tests_completed",
            passed=passed,
            exit_code=exec_res.exit_code,
            command=cmd,
        )

        return TestResult(
            passed=passed,
            exit_code=exec_res.exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            traceback=traceback_text,
            output=combined_output,
        )

    def _extract_traceback(self, stdout: str, stderr: str) -> str:
        """Extracts the traceback section from pytest output for the Self-Healing Loop."""
        combined = f"{stdout}\n{stderr}"

        # Look for the FAILURES section in pytest
        if "=== FAILURES ===" in combined:
            parts = combined.split("=== FAILURES ===", 1)
            failure_section = parts[1]
            return f"=== FAILURES ===\n{failure_section.strip()}"

        # Look for standard Python traceback
        if "Traceback (most recent call last):" in combined:
            idx = combined.find("Traceback (most recent call last):")
            return combined[idx:].strip()

        # If pytest failed during collection or syntax error
        match = re.search(r"(ERROR|FAILED).*$", combined, re.MULTILINE)
        if match:
            return combined[match.start() :].strip()

        return combined.strip()

    def destroy_sandbox(self, container: Container) -> None:
        """Stops and removes the Sandbox container cleanly."""
        try:
            container_id = getattr(container, "id", "unknown")[:12]
            logger.info("sandbox_destroying_container", container_id=container_id)
            try:
                container.stop(timeout=5)
            except docker.errors.DockerException as e:
                logger.debug("sandbox_container_stop_warning", error=str(e))

            try:
                container.remove(force=True)
            except docker.errors.DockerException as e:
                logger.debug("sandbox_container_remove_warning", error=str(e))

            logger.info("sandbox_container_destroyed", container_id=container_id)
        except docker.errors.DockerException as e:
            logger.warning("sandbox_destroy_failed", error=str(e))
