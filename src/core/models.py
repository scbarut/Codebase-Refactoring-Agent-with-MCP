"""Pydantic state schemas for the Orchestration Graph."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """Risk categories for migration rules and file-level assessments."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class MigrationJobConfig(BaseModel):
    """Input configuration for a Migration Job.

    Attributes:
        source: Local directory path or remote Git URL of the codebase.
        target_library: The library to migrate (e.g. ``pydantic``).
        workspace_base_dir: Optional override for the Agent Workspace root.
    """

    source: str
    target_library: str
    workspace_base_dir: str | None = None


class MatchedRule(BaseModel):
    """A migration rule matched to a specific file.

    Attributes:
        rule_id: Identifier from the YAML rule set (e.g. ``validator-to-field-validator``).
        old_qualified_name: The old API surface being replaced.
        new_qualified_name: The new replacement API surface.
        risk: Base risk category from the rule definition.
        transformer_class: Dotted path to the libcst transformer, if any.
    """

    rule_id: str
    old_qualified_name: str
    new_qualified_name: str
    risk: RiskLevel
    transformer_class: str | None = None


class AffectedNode(BaseModel):
    """An AST node within a file that a matched rule will modify.

    Attributes:
        symbol_name: The function/class name (e.g. ``UserModel``).
        node_type: ``class`` or ``function``.
        start_line: First line of the node in the source file.
        end_line: Last line of the node in the source file.
    """

    symbol_name: str
    node_type: str
    start_line: int
    end_line: int


class FilePlanEntry(BaseModel):
    """One entry in the Migration Plan: a single file to be rewritten.

    Attributes:
        file_path: Path relative to the Agent Workspace root.
        matched_rules: Rules that apply to this file.
        affected_nodes: AST nodes that will be modified.
        risk: Computed per-file risk (max rule risk, bumped +1 if no test coverage).
    """

    file_path: str
    matched_rules: list[MatchedRule] = Field(default_factory=list)
    affected_nodes: list[AffectedNode] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW


class FileStatus(str, Enum):
    """Execution status for a file rewritten by the File Sub-graph."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class FileResult(BaseModel):
    """Final result of processing a single file in the File Sub-graph.

    Attributes:
        file_path: Relative path to the file in the workspace.
        status: SUCCESS or FAILED.
        diff: Unified diff of modifications if successful.
        traceback: Last failure traceback if tests failed.
        attempt_count: Number of self-healing attempts performed (0 to 3).
    """

    file_path: str
    status: FileStatus
    diff: str = ""
    traceback: str = ""
    attempt_count: int = 0


from typing_extensions import TypedDict


class MigrationGraphState(TypedDict, total=False):
    """Full state carried through the Orchestration Graph.

    Every node reads from and writes into this state dictionary,
    which is merged and persisted via the LangGraph checkpointer.
    """

    # Identity
    job_id: str
    source: str
    target_library: str

    # Workspace
    workspace_path: str
    workspace_base_dir: str
    repo_name: str
    base_branch: str

    # Scan results
    scanned_files: list[str]

    # Plan
    migration_plan: list[dict[str, Any]]

    # HITL
    approved_files: list[str]

    # Rule set path (resolved at ingest time)
    rule_set_path: str
