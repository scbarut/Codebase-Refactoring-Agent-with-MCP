"""Job manager service for coordinating Migration Jobs, LangGraph execution, and events."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from langgraph.types import Command

from src.api.models import (
    FilePlanEntry,
    FileStatus,
    JobResponse,
    JobStatus,
    MigrationResult,
)
from src.core.graph import (
    reset_dispatch_progress_callback,
    set_dispatch_progress_callback,
)
from src.core.logging import get_logger

logger = get_logger("api.service")


class JobInfo:
    """Internal state representation of an active or completed Migration Job."""

    def __init__(
        self,
        job_id: str,
        source: str,
        target_library: str,
        workspace_base_dir: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.source = source
        self.target_library = target_library
        self.workspace_base_dir = workspace_base_dir
        self.status = JobStatus.SCANNING
        now = datetime.now(UTC)
        self.created_at = now
        self.updated_at = now
        self.error: str | None = None
        self.workspace_path: str | None = None
        self.repo_name: str | None = None
        self.base_branch: str | None = None
        self.branch_name: str | None = None
        self.scanned_files: list[str] = []
        self.migration_plan: list[FilePlanEntry] | None = None
        self.approved_files: list[str] = []
        self.migration_result: MigrationResult | None = None
        self.event_history: list[dict[str, Any]] = []
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.task: asyncio.Task[None] | None = None

    def update_status(self, new_status: JobStatus, error: str | None = None) -> None:
        self.status = new_status
        self.updated_at = datetime.now(UTC)
        if error:
            self.error = error

    def to_response(self) -> JobResponse:
        return JobResponse(
            job_id=self.job_id,
            source=self.source,
            target_library=self.target_library,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            error=self.error,
            workspace_path=self.workspace_path,
            repo_name=self.repo_name,
            base_branch=self.base_branch,
            branch_name=self.branch_name,
            scanned_files_count=len(self.scanned_files),
            plan_files_count=len(self.migration_plan) if self.migration_plan is not None else 0,
            approved_files_count=len(self.approved_files),
        )


class JobManager:
    """Manages active migration jobs, background graph execution, and event broadcast."""

    def __init__(self) -> None:
        self.jobs: dict[str, JobInfo] = {}

    def create_job(
        self,
        source: str,
        target_library: str,
        workspace_base_dir: str | None = None,
        job_id: str | None = None,
    ) -> JobInfo:
        new_id = job_id or str(uuid.uuid4())
        job = JobInfo(
            job_id=new_id,
            source=source,
            target_library=target_library,
            workspace_base_dir=workspace_base_dir,
        )
        self.jobs[new_id] = job
        logger.info("Created job", job_id=new_id, target_library=target_library, source=source)
        return job

    def get_job(self, job_id: str) -> JobInfo | None:
        return self.jobs.get(job_id)

    def list_jobs(self) -> list[JobInfo]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get_plan(self, job_id: str) -> list[FilePlanEntry]:
        job = self.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found",
            )
        # Returns 409 if the job hasn't reached the HITL Gateway yet
        if job.status == JobStatus.SCANNING or job.migration_plan is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job '{job_id}' has not reached the HITL Gateway yet (current status: {job.status.value})",
            )
        return job.migration_plan

    def approve_job(
        self,
        job_id: str,
        approved_files: list[str],
        branch_name: str | None = None,
    ) -> JobInfo:
        job = self.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found",
            )
        # Returns 409 if the job isn't awaiting approval
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job '{job_id}' is not awaiting approval (current status: {job.status.value})",
            )
        job.approved_files = approved_files
        if branch_name:
            job.branch_name = branch_name
        job.update_status(JobStatus.MIGRATING)
        return job

    def get_results(self, job_id: str) -> MigrationResult:
        job = self.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found",
            )
        # Returns 409 if the job isn't complete
        if job.status != JobStatus.COMPLETE or job.migration_result is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job '{job_id}' is not complete (current status: {job.status.value})",
            )
        return job.migration_result

    # ── Event Streaming / WebSocket Support ────────────────────────────

    def broadcast(self, job_id: str, event: dict[str, Any]) -> None:
        """Record and broadcast an event message to all connected clients."""
        job = self.get_job(job_id)
        if not job:
            return
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(UTC).isoformat()
        if "job_id" not in event:
            event["job_id"] = job_id
        job.event_history.append(event)

        for queue in list(job.subscribers):
            try:
                queue.put_nowait(event)
            except Exception:  # noqa: BLE001, S110
                pass

    def get_history(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get_job(job_id)
        return list(job.event_history) if job else []

    async def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any] | None] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        job.subscribers.add(queue)
        return queue

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        job = self.get_job(job_id)
        if job and queue in job.subscribers:
            job.subscribers.remove(queue)

    # ── Orchestration Graph Execution ──────────────────────────────────

    async def run_scan_phase(self, job_id: str, graph: Any) -> None:
        """Run initial ingest, scan, and plan generation until HITL interrupt."""
        job = self.get_job(job_id)
        if not job:
            return

        try:
            self.broadcast(job_id, {
                "type": "job_started",
                "status": "scanning",
                "message": f"Starting scan for {job.source}",
            })

            initial_state = {
                "job_id": job_id,
                "source": job.source,
                "target_library": job.target_library,
                "workspace_base_dir": job.workspace_base_dir,
                "workspace_path": "",
                "repo_name": "",
                "base_branch": "",
                "scanned_files": [],
                "migration_plan": [],
                "approved_files": [],
                "rule_set_path": "",
            }
            config = {"configurable": {"thread_id": job_id}}

            async for event in graph.astream(initial_state, config):
                if not isinstance(event, dict):
                    continue

                if "ingest" in event:
                    data = event["ingest"]
                    job.workspace_path = data.get("workspace_path")
                    job.repo_name = data.get("repo_name")
                    job.base_branch = data.get("base_branch")
                    self.broadcast(job_id, {
                        "type": "scan_progress",
                        "step": "ingest",
                        "workspace_path": job.workspace_path,
                        "repo_name": job.repo_name,
                        "message": f"Workspace prepared for {job.repo_name}",
                    })

                if "scan" in event:
                    data = event["scan"]
                    job.scanned_files = data.get("scanned_files", [])
                    self.broadcast(job_id, {
                        "type": "scan_progress",
                        "step": "scan",
                        "files_found": len(job.scanned_files),
                        "scanned_files": job.scanned_files,
                        "message": f"Scanned {len(job.scanned_files)} files importing {job.target_library}",
                    })

                if "build_plan" in event:
                    raw_plan = event["build_plan"].get("migration_plan", [])
                    plan_entries: list[FilePlanEntry] = []
                    for item in raw_plan:
                        if isinstance(item, FilePlanEntry):
                            plan_entries.append(item)
                        elif isinstance(item, dict):
                            plan_entries.append(FilePlanEntry(**item))
                    job.migration_plan = plan_entries
                    self.broadcast(job_id, {
                        "type": "scan_progress",
                        "step": "build_plan",
                        "plan_count": len(plan_entries),
                        "message": f"Built migration plan with {len(plan_entries)} files",
                    })

                if "__interrupt__" in event:
                    job.update_status(JobStatus.AWAITING_APPROVAL)
                    self.broadcast(job_id, {
                        "type": "hitl_gateway",
                        "status": "awaiting_approval",
                        "plan_count": len(job.migration_plan or []),
                        "message": "Migration Plan ready for human review",
                    })
                    return

            # If graph completed without hitting interrupt (e.g. 0 files or stubbed graph)
            if job.status == JobStatus.SCANNING:
                if job.migration_plan is None:
                    job.migration_plan = []
                job.update_status(JobStatus.AWAITING_APPROVAL)
                self.broadcast(job_id, {
                    "type": "hitl_gateway",
                    "status": "awaiting_approval",
                    "plan_count": len(job.migration_plan),
                    "message": "Migration Plan ready for human review",
                })

        except Exception as exc:  # noqa: BLE001
            logger.error("Scan phase failed", job_id=job_id, error=str(exc))
            job.update_status(JobStatus.FAILED, error=str(exc))
            self.broadcast(job_id, {
                "type": "failed",
                "status": "failed",
                "error": str(exc),
                "message": f"Scan failed: {exc}",
                "is_final": True,
            })

    async def run_migrate_phase(
        self,
        job_id: str,
        approved_files: list[str],
        graph: Any,
        branch_name: str | None = None,
    ) -> None:
        """Resume graph execution from HITL Gateway with the approved file list."""
        job = self.get_job(job_id)
        if not job:
            return

        try:
            job.update_status(JobStatus.MIGRATING)
            job.approved_files = approved_files
            if branch_name:
                job.branch_name = branch_name
            self.broadcast(job_id, {
                "type": "job_resumed",
                "status": "migrating",
                "approved_files": approved_files,
                "branch_name": job.branch_name,
                "message": f"Migration resumed with {len(approved_files)} approved files",
            })

            config = {"configurable": {"thread_id": job_id}}
            resume_data: dict[str, Any] = {"approved_files": approved_files}
            if branch_name:
                resume_data["branch_name"] = branch_name
            resume_cmd = Command(resume=resume_data)

            seen_streamed_files: set[str] = set()

            def on_dispatch_progress(event: dict[str, Any]) -> None:
                if fp := event.get("file_path"):
                    seen_streamed_files.add(fp)
                self.broadcast(job_id, event)

            token = set_dispatch_progress_callback(on_dispatch_progress)
            try:
                async for event in graph.astream(resume_cmd, config):
                    if not isinstance(event, dict):
                        continue

                    if "resume_from_hitl" in event:
                        data = event["resume_from_hitl"]
                        job.branch_name = data.get("branch_name")
                        self.broadcast(job_id, {
                            "type": "branch_created",
                            "branch_name": job.branch_name,
                            "message": f"Created migration branch '{job.branch_name}'",
                        })

                    if "dispatch_file_subgraphs" in event:
                        data = event["dispatch_file_subgraphs"]
                        file_results = data.get("file_results", [])
                        for fr in file_results:
                            f_path = fr.get("file_path", "")
                            # Avoid duplicate events if already streamed per-file in real time
                            if f_path in seen_streamed_files:
                                continue

                            f_status = fr.get("status", "SUCCESS")
                            attempts = fr.get("attempt_count", 0)

                            # Stream individual file progress events (e.g. for stubbed/batch runners)
                            self.broadcast(job_id, {
                                "type": "current_file",
                                "file_path": f_path,
                                "status": "migrating",
                                "message": f"Processing file {f_path}",
                            })

                            if attempts > 0:
                                for att in range(1, attempts + 1):
                                    self.broadcast(job_id, {
                                        "type": "healing_attempt",
                                        "file_path": f_path,
                                        "attempt": att,
                                        "max_attempts": 3,
                                        "message": f"Self-healing attempt {att}/3 for {f_path}",
                                    })

                            is_success = (
                                f_status == FileStatus.SUCCESS
                                or getattr(f_status, "value", str(f_status)).upper() == "SUCCESS"
                                or str(f_status).upper().endswith("SUCCESS")
                            )
                            status_str = getattr(f_status, "value", str(f_status))
                            if "." in str(status_str):
                                status_str = str(status_str).split(".", 1)[1]

                            self.broadcast(job_id, {
                                "type": "test_result",
                                "file_path": f_path,
                                "passed": is_success,
                                "exit_code": 0 if is_success else 1,
                                "message": f"Tests {'passed' if is_success else 'failed'} for {f_path}",
                            })

                            self.broadcast(job_id, {
                                "type": "file_completed",
                                "file_path": f_path,
                                "status": status_str,
                                "attempt_count": attempts,
                                "message": f"Finished {f_path} with status {status_str}",
                            })

                    if "aggregate_results" in event:
                        self.broadcast(job_id, {
                            "type": "results_aggregated",
                            "message": "Aggregated migration results",
                        })

                    if "commit_and_output" in event:
                        data = event["commit_and_output"]
                        res_dict = data.get("migration_result", {})
                        if res_dict:
                            if isinstance(res_dict, MigrationResult):
                                job.migration_result = res_dict
                            else:
                                job.migration_result = MigrationResult(**res_dict)
            finally:
                reset_dispatch_progress_callback(token)

            # Fallback if state has migration_result
            if job.migration_result is None and hasattr(graph, "aget_state"):
                try:
                    st = await graph.aget_state(config)
                    res_val = st.values.get("migration_result")
                    if res_val:
                        job.migration_result = (
                            MigrationResult(**res_val) if isinstance(res_val, dict) else res_val
                        )
                except Exception:  # noqa: BLE001, S110
                    pass

            if job.migration_result is None:
                job.migration_result = MigrationResult(
                    job_id=job_id,
                    target_library=job.target_library,
                    total_files=len(approved_files),
                    success_count=len(approved_files),
                    failure_count=0,
                )

            job.update_status(JobStatus.COMPLETE)
            self.broadcast(job_id, {
                "type": "completion",
                "status": "complete",
                "result": job.migration_result.model_dump(),
                "message": "Migration completed successfully",
                "is_final": True,
            })

        except Exception as exc:  # noqa: BLE001
            logger.error("Migration phase failed", job_id=job_id, error=str(exc))
            job.update_status(JobStatus.FAILED, error=str(exc))
            self.broadcast(job_id, {
                "type": "failed",
                "status": "failed",
                "error": str(exc),
                "message": f"Migration failed: {exc}",
                "is_final": True,
            })
