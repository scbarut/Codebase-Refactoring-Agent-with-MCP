"""Integration tests at the HTTP boundary for the FastAPI REST + WebSocket API.

Verifies:
- All 6 endpoints and WebSocket streaming
- HTTP status codes (200, 201, 404, 409, 422)
- Structured Pydantic response schemas
- Error cases (409 conflict when job not in expected state, 404 when not found)
- Real-time WebSocket streaming (scan progress, current file, test results, healing attempts, completion)
- Injected / stubbed Orchestration Graph dependency
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command

from src.api.dependencies import (
    get_job_manager,
    get_orchestration_graph,
    reset_job_manager,
)
from src.api.main import app
from src.api.models import (
    FilePlanEntry,
    JobApproveResponse,
    JobCreateResponse,
    JobResponse,
    JobStatus,
    MigrationResult,
)


class StubbedOrchestrationGraph:
    """Deterministic stub of the Orchestration Graph for boundary testing."""

    def __init__(
        self,
        plan: list[dict[str, Any]] | None = None,
        fail_on_scan: bool = False,
        fail_on_migrate: bool = False,
    ) -> None:
        self.fail_on_scan = fail_on_scan
        self.fail_on_migrate = fail_on_migrate
        self.plan = plan or [
            {
                "file_path": "models.py",
                "matched_rules": [
                    {
                        "rule_id": "validator-to-field-validator",
                        "old_qualified_name": "pydantic.validator",
                        "new_qualified_name": "pydantic.field_validator",
                        "risk": "MEDIUM",
                        "transformer_class": None,
                    }
                ],
                "affected_nodes": [
                    {
                        "symbol_name": "validate_name",
                        "node_type": "function",
                        "start_line": 10,
                        "end_line": 15,
                    }
                ],
                "risk": "MEDIUM",
            }
        ]

    async def astream(self, input_data: Any, config: dict[str, Any] | None = None):
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "test-job-id")

        if isinstance(input_data, Command):
            if self.fail_on_migrate:
                raise RuntimeError("Simulated failure during migration phase")

            approved = input_data.resume
            yield {"resume_from_hitl": {"branch_name": "migrate/pydantic"}}
            yield {
                "dispatch_file_subgraphs": {
                    "file_results": [
                        {
                            "file_path": f,
                            "status": "SUCCESS",
                            "diff": f"--- a/{f}\n+++ b/{f}\n@@ -1 +1 @@\n-old\n+new",
                            "traceback": "",
                            "attempt_count": 1,
                        }
                        for f in approved
                    ]
                }
            }
            yield {
                "aggregate_results": {
                    "migration_result": {
                        "job_id": thread_id,
                        "target_library": "pydantic",
                        "total_files": len(approved),
                        "successful_files": [
                            {
                                "file_path": f,
                                "status": "SUCCESS",
                                "diff": f"--- a/{f}\n+++ b/{f}\n@@ -1 +1 @@\n-old\n+new",
                                "traceback": "",
                                "attempt_count": 1,
                            }
                            for f in approved
                        ],
                        "failed_files": [],
                        "success_count": len(approved),
                        "failure_count": 0,
                        "total_healing_attempts": 1,
                        "full_diff": "--- a/models.py\n+++ b/models.py\n@@ -1 +1 @@\n-old\n+new",
                        "git_commands": {
                            "commands": ["git merge migration-agent/migrate/pydantic"],
                            "one_liner": "git merge migration-agent/migrate/pydantic",
                            "patch_command": "git apply",
                        },
                    }
                }
            }
            yield {
                "commit_and_output": {
                    "migration_result": {
                        "job_id": thread_id,
                        "target_library": "pydantic",
                        "total_files": len(approved),
                        "successful_files": [
                            {
                                "file_path": f,
                                "status": "SUCCESS",
                                "diff": f"--- a/{f}\n+++ b/{f}\n@@ -1 +1 @@\n-old\n+new",
                                "traceback": "",
                                "attempt_count": 1,
                            }
                            for f in approved
                        ],
                        "failed_files": [],
                        "success_count": len(approved),
                        "failure_count": 0,
                        "total_healing_attempts": 1,
                        "full_diff": "--- a/models.py\n+++ b/models.py\n@@ -1 +1 @@\n-old\n+new",
                        "git_commands": {
                            "commands": ["git merge migration-agent/migrate/pydantic"],
                            "one_liner": "git merge migration-agent/migrate/pydantic",
                            "patch_command": "git apply",
                        },
                    }
                }
            }
        else:
            if self.fail_on_scan:
                raise RuntimeError("Simulated failure during scan phase")

            yield {
                "ingest": {
                    "workspace_path": "/workspaces/sample-repo-123",
                    "repo_name": "sample-repo",
                    "base_branch": "main",
                }
            }
            yield {"scan": {"scanned_files": ["models.py"]}}
            yield {"build_plan": {"migration_plan": self.plan}}
            yield {
                "__interrupt__": [
                    type(
                        "Interrupt",
                        (),
                        {
                            "value": {
                                "message": "Migration Plan ready for review",
                                "job_id": thread_id,
                                "plan": self.plan,
                            }
                        },
                    )()
                ]
            }


@pytest.fixture(autouse=True)
def setup_api_dependencies():
    """Reset the JobManager and override the graph dependency for each test."""
    manager = reset_job_manager()
    stub_graph = StubbedOrchestrationGraph()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_orchestration_graph] = lambda: stub_graph
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


# ── POST /api/jobs ─────────────────────────────────────────────────────


def test_create_job_success(client: TestClient):
    """POST /api/jobs returns 201 with job_id and status 'scanning'."""
    payload = {
        "path_or_url": "/path/to/my_repo",
        "target_library": "pydantic",
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201

    data = response.json()
    validated = JobCreateResponse(**data)
    assert validated.status == JobStatus.SCANNING
    assert len(validated.job_id) > 0


def test_create_job_validation_errors(client: TestClient):
    """POST /api/jobs enforces required fields and returns 422 on bad payload."""
    # Missing path_or_url
    resp1 = client.post("/api/jobs", json={"target_library": "pydantic"})
    assert resp1.status_code == 422

    # Missing target_library
    resp2 = client.post("/api/jobs", json={"path_or_url": "/path/to/repo"})
    assert resp2.status_code == 422

    # Extra disallowed field
    resp3 = client.post(
        "/api/jobs",
        json={"path_or_url": "/repo", "target_library": "pydantic", "unknown_field": True},
    )
    assert resp3.status_code == 422


# ── GET /api/jobs and GET /api/jobs/{id} ───────────────────────────────


def test_get_job_not_found(client: TestClient):
    """GET /api/jobs/{id} returns 404 for unknown job IDs."""
    response = client.get("/api/jobs/non-existent-id")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_list_and_get_job_metadata(client: TestClient):
    """GET /api/jobs lists all jobs; GET /api/jobs/{id} returns detailed metadata."""
    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    job_id = create_resp.json()["job_id"]

    # In TestClient, background_tasks have run the scan phase to HITL interrupt
    get_resp = client.get(f"/api/jobs/{job_id}")
    assert get_resp.status_code == 200

    job_data = get_resp.json()
    validated = JobResponse(**job_data)
    assert validated.job_id == job_id
    assert validated.source == "/code/app"
    assert validated.target_library == "pydantic"
    assert validated.status == JobStatus.AWAITING_APPROVAL
    assert validated.repo_name == "sample-repo"

    # Verify list endpoint includes this job
    list_resp = client.get("/api/jobs")
    assert list_resp.status_code == 200
    jobs_list = list_resp.json()
    assert len(jobs_list) >= 1
    assert any(j["job_id"] == job_id for j in jobs_list)


# ── GET /api/jobs/{id}/plan ────────────────────────────────────────────


def test_get_plan_409_when_still_scanning(client: TestClient):
    """GET /api/jobs/{id}/plan returns 409 Conflict if job has not reached HITL Gateway."""
    manager = get_job_manager()
    job = manager.create_job(source="/repo", target_library="pydantic")
    job.status = JobStatus.SCANNING
    job.migration_plan = None

    response = client.get(f"/api/jobs/{job.job_id}/plan")
    assert response.status_code == 409
    assert "has not reached the hitl gateway yet" in response.json()["detail"].lower()


def test_get_plan_404_for_unknown_job(client: TestClient):
    """GET /api/jobs/{id}/plan returns 404 if job does not exist."""
    response = client.get("/api/jobs/missing-job-id/plan")
    assert response.status_code == 404


def test_get_plan_200_at_hitl_gateway(client: TestClient):
    """GET /api/jobs/{id}/plan returns 200 with list of FilePlanEntry when paused at HITL."""
    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    job_id = create_resp.json()["job_id"]

    response = client.get(f"/api/jobs/{job_id}/plan")
    assert response.status_code == 200

    plan_data = response.json()
    assert isinstance(plan_data, list)
    assert len(plan_data) == 1

    entry = FilePlanEntry(**plan_data[0])
    assert entry.file_path == "models.py"
    assert entry.risk == "MEDIUM"
    assert len(entry.matched_rules) == 1
    assert entry.matched_rules[0].rule_id == "validator-to-field-validator"
    assert len(entry.affected_nodes) == 1
    assert entry.affected_nodes[0].symbol_name == "validate_name"


# ── POST /api/jobs/{id}/approve ────────────────────────────────────────


def test_approve_409_when_not_awaiting_approval(client: TestClient):
    """POST /api/jobs/{id}/approve returns 409 Conflict if job is not awaiting approval."""
    manager = get_job_manager()
    job = manager.create_job(source="/repo", target_library="pydantic")
    job.status = JobStatus.SCANNING

    response = client.post(
        f"/api/jobs/{job.job_id}/approve",
        json={"approved_files": ["models.py"]},
    )
    assert response.status_code == 409
    assert "is not awaiting approval" in response.json()["detail"].lower()


def test_approve_404_for_unknown_job(client: TestClient):
    """POST /api/jobs/{id}/approve returns 404 if job does not exist."""
    response = client.post(
        "/api/jobs/missing-job-id/approve",
        json={"approved_files": ["models.py"]},
    )
    assert response.status_code == 404


def test_approve_422_invalid_body(client: TestClient):
    """POST /api/jobs/{id}/approve validates body schema."""
    response = client.post("/api/jobs/some-id/approve", json={})
    assert response.status_code == 422


def test_approve_200_resumes_graph(client: TestClient):
    """POST /api/jobs/{id}/approve transitions status to 'migrating' and returns JobApproveResponse."""
    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    job_id = create_resp.json()["job_id"]

    approve_resp = client.post(
        f"/api/jobs/{job_id}/approve",
        json={"approved_files": ["models.py"]},
    )
    assert approve_resp.status_code == 200

    data = approve_resp.json()
    validated = JobApproveResponse(**data)
    assert validated.job_id == job_id
    assert validated.status == JobStatus.MIGRATING
    assert validated.approved_files == ["models.py"]


# ── GET /api/jobs/{id}/results ─────────────────────────────────────────


def test_get_results_409_when_not_complete(client: TestClient):
    """GET /api/jobs/{id}/results returns 409 Conflict if job is not complete."""
    manager = get_job_manager()
    job = manager.create_job(source="/repo", target_library="pydantic")
    job.status = JobStatus.AWAITING_APPROVAL

    response = client.get(f"/api/jobs/{job.job_id}/results")
    assert response.status_code == 409
    assert "is not complete" in response.json()["detail"].lower()


def test_get_results_404_for_unknown_job(client: TestClient):
    """GET /api/jobs/{id}/results returns 404 if job does not exist."""
    response = client.get("/api/jobs/unknown-id/results")
    assert response.status_code == 404


def test_get_results_200_when_complete(client: TestClient):
    """GET /api/jobs/{id}/results returns MigrationResult once job execution completes."""
    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    job_id = create_resp.json()["job_id"]

    # Approve files -> TestClient runs migration phase to completion
    approve_resp = client.post(
        f"/api/jobs/{job_id}/approve",
        json={"approved_files": ["models.py"]},
    )
    assert approve_resp.status_code == 200

    results_resp = client.get(f"/api/jobs/{job_id}/results")
    assert results_resp.status_code == 200

    result_data = results_resp.json()
    validated = MigrationResult(**result_data)
    assert validated.job_id == job_id
    assert validated.total_files == 1
    assert validated.success_count == 1
    assert validated.failure_count == 0
    assert len(validated.successful_files) == 1
    assert validated.successful_files[0].file_path == "models.py"
    assert "models.py" in validated.full_diff
    assert "commands" in validated.git_commands


# ── Full End-to-End HTTP Boundary Lifecycle ────────────────────────────


def test_full_job_lifecycle_end_to_end(client: TestClient):
    """Verify complete lifecycle: submit -> scan -> plan -> approve -> results."""
    # 1. Submit Migration Job
    submit_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/workspace/repo", "target_library": "pydantic"},
    )
    assert submit_resp.status_code == 201
    job_id = submit_resp.json()["job_id"]

    # 2. Check metadata at HITL Gateway
    status_resp = client.get(f"/api/jobs/{job_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "awaiting_approval"

    # 3. Inspect Migration Plan
    plan_resp = client.get(f"/api/jobs/{job_id}/plan")
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert len(plan) == 1
    assert plan[0]["file_path"] == "models.py"

    # 4. Results before approve should return 409
    early_results = client.get(f"/api/jobs/{job_id}/results")
    assert early_results.status_code == 409

    # 5. Approve Migration Plan
    approve_resp = client.post(
        f"/api/jobs/{job_id}/approve",
        json={"approved_files": ["models.py"]},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "migrating"

    # 6. Check final status
    final_status = client.get(f"/api/jobs/{job_id}")
    assert final_status.status_code == 200
    assert final_status.json()["status"] == "complete"

    # 7. Plan is still readable after migration
    plan_after = client.get(f"/api/jobs/{job_id}/plan")
    assert plan_after.status_code == 200

    # 8. Retrieve MigrationResult
    final_results = client.get(f"/api/jobs/{job_id}/results")
    assert final_results.status_code == 200
    res_data = final_results.json()
    assert res_data["success_count"] == 1
    assert res_data["total_files"] == 1


# ── WebSocket Real-Time Streaming ──────────────────────────────────────


def test_websocket_stream_events(client: TestClient):
    """WS /api/jobs/{id}/stream streams real-time JSON events."""
    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    job_id = create_resp.json()["job_id"]

    # Approve files to trigger migration events
    client.post(
        f"/api/jobs/{job_id}/approve",
        json={"approved_files": ["models.py"]},
    )

    # Connect to WebSocket stream
    with client.websocket_connect(f"/api/jobs/{job_id}/stream") as ws:
        received_events: list[dict[str, Any]] = []

        # Read available events from history and streaming
        for _ in range(25):
            try:
                ev = ws.receive_json()
                received_events.append(ev)
                if ev.get("type") == "completion":
                    break
            except Exception:  # noqa: BLE001
                break

    event_types = [e.get("type") for e in received_events]

    # Verify key event types required by spec
    assert "job_started" in event_types
    assert "scan_progress" in event_types
    assert "hitl_gateway" in event_types
    assert "job_resumed" in event_types
    assert "current_file" in event_types
    assert "test_result" in event_types
    assert "healing_attempt" in event_types
    assert "completion" in event_types

    # Verify event structure
    first_ev = received_events[0]
    assert "timestamp" in first_ev
    assert first_ev["job_id"] == job_id


def test_websocket_nonexistent_job_closes(client: TestClient):
    """WS /api/jobs/{id}/stream sends error event and closes when job does not exist."""
    with client.websocket_connect("/api/jobs/missing-job/stream") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "error"
        assert "not found" in ev["message"].lower()


# ── Job Failure Lifecycle ──────────────────────────────────────────────


def test_job_failure_during_scan(client: TestClient):
    """When graph fails during scan phase, job status is updated to 'failed'."""
    failing_graph = StubbedOrchestrationGraph(fail_on_scan=True)
    app.dependency_overrides[get_orchestration_graph] = lambda: failing_graph

    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/jobs/{job_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "failed"
    assert "scan phase" in status_resp.json()["error"].lower()


def test_job_failure_during_migration(client: TestClient):
    """When graph fails during migration phase, job status is updated to 'failed'."""
    failing_graph = StubbedOrchestrationGraph(fail_on_migrate=True)
    app.dependency_overrides[get_orchestration_graph] = lambda: failing_graph

    create_resp = client.post(
        "/api/jobs",
        json={"path_or_url": "/code/app", "target_library": "pydantic"},
    )
    job_id = create_resp.json()["job_id"]

    approve_resp = client.post(
        f"/api/jobs/{job_id}/approve",
        json={"approved_files": ["models.py"]},
    )
    assert approve_resp.status_code == 200

    status_resp = client.get(f"/api/jobs/{job_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "failed"
    assert "migration phase" in status_resp.json()["error"].lower()


# ── Integration with Real Compiled Orchestration Graph ─────────────────


def test_real_orchestration_graph_through_api(tmp_path, client: TestClient):
    """Exercise the REAL compiled Orchestration Graph through the FastAPI API endpoints."""
    from unittest.mock import patch

    from langgraph.checkpoint.memory import InMemorySaver

    from src.core.graph import compile_orchestration_graph
    from src.core.models import FileResult, FileStatus

    # Create dummy source repo
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "models.py").write_text(
        "from pydantic import BaseModel, validator\n\n"
        "class UserModel(BaseModel):\n"
        "    name: str\n\n"
        "    @validator('name')\n"
        "    def val(cls, v):\n"
        "        return v.strip()\n",
        encoding="utf-8",
    )
    tests_dir = source_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_models.py").write_text("def test_dummy(): pass\n", encoding="utf-8")

    real_graph = compile_orchestration_graph(checkpointer=InMemorySaver())
    app.dependency_overrides[get_orchestration_graph] = lambda: real_graph

    # 1. POST /api/jobs -> 201 Created
    create_resp = client.post(
        "/api/jobs",
        json={
            "path_or_url": str(source_dir),
            "target_library": "pydantic",
            "workspace_base_dir": str(tmp_path / "workspaces"),
        },
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["job_id"]

    # 2. GET /api/jobs/{id} -> status is awaiting_approval
    status_resp = client.get(f"/api/jobs/{job_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "awaiting_approval"

    # 3. GET /api/jobs/{id}/plan -> returns real plan containing models.py
    plan_resp = client.get(f"/api/jobs/{job_id}/plan")
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert len(plan) == 1
    assert plan[0]["file_path"] == "models.py"
    assert any(r["rule_id"] == "validator-to-field-validator" for r in plan[0]["matched_rules"])

    # 4. POST /api/jobs/{id}/approve -> stub the file subgraph runner to isolate from sandbox container
    async def stub_file_runner(file_path: str, **kwargs: Any) -> FileResult:
        return FileResult(
            file_path=file_path,
            status=FileStatus.SUCCESS,
            diff=f"--- a/{file_path}\n+++ b/{file_path}\n@@ -1 +1 @@\n-validator\n+field_validator",
            traceback="",
            attempt_count=0,
        )

    with patch("src.core.graph.run_file_subgraph", side_effect=stub_file_runner):
        approve_resp = client.post(
            f"/api/jobs/{job_id}/approve",
            json={"approved_files": ["models.py"]},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "migrating"

    # 5. GET /api/jobs/{id} -> status is complete
    final_status = client.get(f"/api/jobs/{job_id}")
    assert final_status.status_code == 200
    assert final_status.json()["status"] == "complete"

    # 6. GET /api/jobs/{id}/results -> returns full MigrationResult with diffs
    results_resp = client.get(f"/api/jobs/{job_id}/results")
    assert results_resp.status_code == 200
    result_data = results_resp.json()
    assert result_data["success_count"] == 1
    assert result_data["failure_count"] == 0
    assert "models.py" in result_data["full_diff"]

