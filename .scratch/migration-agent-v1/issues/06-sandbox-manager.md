# 06: Sandbox manager — per-job Docker container lifecycle

**What to build:** A module that manages the per-job Docker Sandbox lifecycle. It checks for (and pulls or builds) the Sandbox base image, spins up a fresh container per Migration Job, mounts the Agent Workspace as a volume, detects and installs the project's dependencies (`requirements.txt` / `pyproject.toml` / `setup.py`), runs scoped `pytest` targeting only the modified module, captures stdout/stderr (test results + tracebacks), and destroys the container when done. Unit tests stub the Docker client; one integration test (marked, requires Docker) exercises the real container lifecycle.

**Blocked by:** 01 (project scaffold — needs Sandbox Dockerfile and docker-compose with socket mount)

**Status:** done

- [x] `SandboxManager` class with methods: `ensure_image()`, `create_sandbox(workspace_path, python_version)`, `install_deps(container)`, `run_tests(container, module_path)`, `destroy_sandbox(container)`
- [x] `ensure_image()` checks for `ghcr.io/scbarut/migration-sandbox:py<version>`, pulls if available, builds from `docker/sandbox/Dockerfile` if not
- [x] `create_sandbox()` spins up a container with the Agent Workspace mounted as a read-write volume
- [x] `install_deps()` detects `requirements.txt`, `pyproject.toml`, or `setup.py` and runs `pip install` inside the container
- [x] `run_tests()` executes `pytest <module_path> --tb=long -q` and captures stdout/stderr as structured output (pass/fail + traceback text)
- [x] `destroy_sandbox()` stops and removes the container
- [x] Unit tests with a stubbed Docker client verify the full lifecycle (create → install → test → destroy)
- [x] One Docker-required integration test (marked with `@pytest.mark.docker`) exercises the real lifecycle against a simple test project

## Implementation Notes

- Implemented `SandboxManager`, `TestResult`, and `InstallResult` in `src/core/sandbox.py`.
- Re-exported classes in `src/core/__init__.py`.
- Added `docker` marker configuration to `pyproject.toml`.
- Provided unit tests with stubbed Docker client and verified full lifecycle in `tests/test_sandbox.py`.
- Verified live integration test (`test_docker_sandbox_real_lifecycle`) against local Docker daemon.
- All 87 unit and integration tests passing; ruff lint checks passing.
