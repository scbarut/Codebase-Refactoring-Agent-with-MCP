"""FastAPI dependencies for injecting JobManager and Orchestration Graph."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from src.api.service import JobManager
from src.core.graph import compile_orchestration_graph

_job_manager: JobManager | None = None
_default_graph: Any = None


def get_job_manager() -> JobManager:
    """Return the shared JobManager singleton instance."""
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager


def reset_job_manager() -> JobManager:
    """Reset the JobManager singleton (primarily for test isolation)."""
    global _job_manager
    _job_manager = JobManager()
    return _job_manager


def get_orchestration_graph() -> Any:
    """Return the compiled Orchestration Graph.

    By default, uses an InMemorySaver checkpointer so state is preserved
    across HITL pause and resume. Overridable in tests via
    ``app.dependency_overrides[get_orchestration_graph]``.
    """
    global _default_graph
    if _default_graph is None:
        _default_graph = compile_orchestration_graph(checkpointer=InMemorySaver())
    return _default_graph


def set_default_orchestration_graph(graph: Any) -> None:
    """Set the default compiled Orchestration Graph."""
    global _default_graph
    _default_graph = graph
