"""Pydantic schemas and request/response models for the Migration Agent API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.models import (
    AffectedNode,
    FilePlanEntry,
    FileResult,
    FileStatus,
    GitCommands,
    MatchedRule,
    MigrationResult,
    RiskLevel,
)


class JobStatus(str, Enum):
    """Lifecycle status of a Migration Job."""

    SCANNING = "scanning"
    AWAITING_APPROVAL = "awaiting_approval"
    MIGRATING = "migrating"
    COMPLETE = "complete"
    FAILED = "failed"


class JobCreateRequest(BaseModel):
    """Request payload to initiate a new Migration Job."""

    model_config = ConfigDict(extra="forbid")

    path_or_url: str = Field(
        ...,
        description="Local directory path or remote Git URL of the target codebase.",
        examples=["/path/to/codebase", "https://github.com/org/repo.git"],
    )
    target_library: str = Field(
        ...,
        description="Target library being migrated (e.g. 'pydantic').",
        examples=["pydantic"],
    )
    workspace_base_dir: str | None = Field(
        default=None,
        description="Optional directory path to house the Agent Workspace.",
    )


class JobCreateResponse(BaseModel):
    """Response returned upon successfully creating a Migration Job."""

    job_id: str = Field(..., description="Unique identifier of the Migration Job.")
    status: JobStatus = Field(
        default=JobStatus.SCANNING,
        description="Initial status of the Migration Job (always 'scanning').",
    )


class JobApproveRequest(BaseModel):
    """Request payload to approve files at the HITL Gateway."""

    model_config = ConfigDict(extra="forbid")

    approved_files: list[str] = Field(
        ...,
        description="List of relative file paths approved for automated migration.",
        examples=[["models.py", "schemas.py"]],
    )
    branch_name: str | None = Field(
        default=None,
        description="Optional custom git branch name for the migration.",
        examples=["migrate/pydantic-v2"],
    )


class JobApproveResponse(BaseModel):
    """Response returned upon resuming a Migration Job from HITL Gateway."""

    job_id: str = Field(..., description="Unique identifier of the Migration Job.")
    status: JobStatus = Field(
        default=JobStatus.MIGRATING,
        description="Updated status of the Migration Job (always 'migrating').",
    )
    approved_files: list[str] = Field(
        default_factory=list,
        description="List of files approved for migration.",
    )


class JobResponse(BaseModel):
    """Detailed metadata and current execution status of a Migration Job."""

    job_id: str
    source: str
    target_library: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    workspace_path: str | None = None
    repo_name: str | None = None
    base_branch: str | None = None
    branch_name: str | None = None
    scanned_files_count: int = 0
    plan_files_count: int = 0
    approved_files_count: int = 0


class StreamEvent(BaseModel):
    """Structured event message streamed over WebSocket."""

    type: str = Field(..., description="Event type name.")
    job_id: str = Field(..., description="Associated Migration Job ID.")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 UTC timestamp of the event.",
    )
    message: str | None = Field(default=None, description="Human-readable event message.")
    status: str | None = Field(default=None, description="Job or file status.")
    step: str | None = Field(default=None, description="Current workflow step.")
    file_path: str | None = Field(default=None, description="Target file path if applicable.")
    files_found: int | None = Field(default=None, description="Scanned files count.")
    plan_count: int | None = Field(default=None, description="Total planned files count.")
    approved_files: list[str] | None = Field(default=None, description="Approved files list.")
    attempt: int | None = Field(default=None, description="Current healing attempt number.")
    max_attempts: int | None = Field(default=None, description="Maximum healing attempts allowed.")
    passed: bool | None = Field(default=None, description="Whether test passed.")
    exit_code: int | None = Field(default=None, description="Test execution exit code.")
    error: str | None = Field(default=None, description="Error message if failed.")
    result: dict[str, Any] | None = Field(default=None, description="Final migration result summary.")
    is_final: bool = Field(default=False, description="Whether this is the final event for the stream.")


__all__ = [
    "AffectedNode",
    "FilePlanEntry",
    "FileResult",
    "FileStatus",
    "GitCommands",
    "JobApproveRequest",
    "JobApproveResponse",
    "JobCreateRequest",
    "JobCreateResponse",
    "JobResponse",
    "JobStatus",
    "MatchedRule",
    "MigrationResult",
    "RiskLevel",
    "StreamEvent",
]
