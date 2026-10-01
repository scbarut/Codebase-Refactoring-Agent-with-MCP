"""FastAPI router for Migration Job management and real-time streaming."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from src.api.dependencies import get_job_manager, get_orchestration_graph
from src.api.models import (
    FilePlanEntry,
    JobApproveRequest,
    JobApproveResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobResponse,
    JobStatus,
    MigrationResult,
)
from src.api.service import JobManager
from src.core.logging import get_logger

logger = get_logger("api.jobs")

router = APIRouter()


@router.post(
    "",
    response_model=JobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Migration Job",
    description="Accepts codebase source and target library, creates a Migration Job, and begins scanning in a background task.",
)
async def create_job(
    request: JobCreateRequest,
    background_tasks: BackgroundTasks,
    manager: Annotated[JobManager, Depends(get_job_manager)],
    graph: Annotated[Any, Depends(get_orchestration_graph)],
) -> JobCreateResponse:
    """Create a new Migration Job and launch graph scan in background."""
    job = manager.create_job(
        source=request.path_or_url,
        target_library=request.target_library,
        workspace_base_dir=request.workspace_base_dir,
    )
    # Launch scan phase in background task
    background_tasks.add_task(manager.run_scan_phase, job.job_id, graph)

    return JobCreateResponse(
        job_id=job.job_id,
        status=JobStatus.SCANNING,
    )


@router.get(
    "",
    response_model=list[JobResponse],
    summary="List Migration Jobs",
    description="Returns metadata and status for all active and past Migration Jobs.",
)
async def list_jobs(
    manager: Annotated[JobManager, Depends(get_job_manager)],
) -> list[JobResponse]:
    """List all Migration Jobs in reverse chronological order."""
    jobs = manager.list_jobs()
    return [job.to_response() for job in jobs]


@router.get(
    "/{id}",
    response_model=JobResponse,
    summary="Get Migration Job",
    description="Returns job metadata and current status (scanning, awaiting_approval, migrating, complete, failed).",
)
async def get_job(
    id: str,
    manager: Annotated[JobManager, Depends(get_job_manager)],
) -> JobResponse:
    """Get metadata and current status for a specific Migration Job."""
    job = manager.get_job(id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{id}' not found",
        )
    return job.to_response()


@router.get(
    "/{id}/plan",
    response_model=list[FilePlanEntry],
    summary="Get Migration Plan",
    description="Returns the Migration Plan with risk levels, AST nodes, and matched rules. Returns 409 if not at HITL Gateway.",
)
async def get_job_plan(
    id: str,
    manager: Annotated[JobManager, Depends(get_job_manager)],
) -> list[FilePlanEntry]:
    """Retrieve the Migration Plan generated for the job."""
    return manager.get_plan(id)


@router.post(
    "/{id}/approve",
    response_model=JobApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Migration Plan",
    description="Resumes the Orchestration Graph from the HITL Gateway with the list of approved files. Returns 409 if not awaiting approval.",
)
async def approve_job(
    id: str,
    request: JobApproveRequest,
    background_tasks: BackgroundTasks,
    manager: Annotated[JobManager, Depends(get_job_manager)],
    graph: Annotated[Any, Depends(get_orchestration_graph)],
) -> JobApproveResponse:
    """Approve a subset or full list of files to proceed with migration rewrite."""
    job = manager.approve_job(id, request.approved_files, request.branch_name)
    # Launch migration phase in background task
    background_tasks.add_task(
        manager.run_migrate_phase,
        id,
        request.approved_files,
        graph,
        request.branch_name,
    )

    return JobApproveResponse(
        job_id=job.job_id,
        status=JobStatus.MIGRATING,
        approved_files=request.approved_files,
    )


@router.get(
    "/{id}/results",
    response_model=MigrationResult,
    summary="Get Migration Results",
    description="Returns the aggregated MigrationResult (diffs, statuses, tracebacks, git commands). Returns 409 if not complete.",
)
async def get_job_results(
    id: str,
    manager: Annotated[JobManager, Depends(get_job_manager)],
) -> MigrationResult:
    """Retrieve full MigrationResult once the job is complete."""
    return manager.get_results(id)


@router.websocket(
    "/{id}/stream",
)
async def stream_job_events(
    websocket: WebSocket,
    id: str,
    manager: Annotated[JobManager, Depends(get_job_manager)],
) -> None:
    """WebSocket endpoint that streams real-time events for a Migration Job."""
    await websocket.accept()

    job = manager.get_job(id)
    if job is None:
        await websocket.send_json({
            "type": "error",
            "job_id": id,
            "message": f"Job '{id}' not found",
        })
        await websocket.close(code=1008)
        return

    queue = await manager.subscribe(id)
    if queue is None:
        await websocket.close(code=1008)
        return

    # Background task to listen for client disconnect or incoming ping
    async def client_reader() -> None:
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.strip().lower() == "ping":
                    await websocket.send_json({"type": "pong", "job_id": id})
        except Exception:  # noqa: BLE001, S110
            pass

    reader_task = asyncio.create_task(client_reader())

    try:
        # 1. Replay historical events recorded so far
        for event in manager.get_history(id):
            await websocket.send_json(event)

        # 2. Stream real-time events as they occur
        while not reader_task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                if event is None:
                    break
                await websocket.send_json(event)
            except TimeoutError:
                continue

    except (WebSocketDisconnect, ConnectionResetError):
        pass
    finally:
        reader_task.cancel()
        await manager.unsubscribe(id, queue)
