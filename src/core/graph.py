"""Orchestration Graph — scan, plan, HITL Gateway.

The first half of the LangGraph Orchestration Graph.  Given a
``MigrationJobConfig``, the graph:

1. **ingest** — clones/copies the codebase into an Agent Workspace via
   ``mcp-server-git``.
2. **scan** — scans all files for Target Library imports via
   ``mcp-server-ast``.
3. **build_plan** — loads matching Migration Rules, extracts affected AST
   nodes, calculates per-file risk, and assembles the Migration Plan.
4. **hitl_gateway** — calls ``interrupt()``, pausing execution for human
   review.  State is persisted via the configured checkpointer.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.core.config import load_config
from src.core.logging import get_logger
from src.core.models import (
    AffectedNode,
    FilePlanEntry,
    MatchedRule,
    MigrationGraphState,
    RiskLevel,
)
from src.mcp_servers.ast_server import create_ast_server
from src.mcp_servers.git_server import create_git_server
from src.rules.loader import load_rules

logger = get_logger(__name__)

# ── Rule set resolution ────────────────────────────────────────────────

# Maps a target library name (as the user would type it) to the YAML rule
# file that ships with this project.  Extend this mapping when new Target
# Library rule sets are added.
_RULE_SET_REGISTRY: dict[str, str] = {
    "pydantic": "src/rules/pydantic_v1_to_v2.yaml",
}


def resolve_rule_set(target_library: str) -> str:
    """Return the path to the rule-set YAML for a given Target Library.

    Raises:
        ValueError: If no rule set is registered for *target_library*.
    """
    key = target_library.lower().strip()
    if key not in _RULE_SET_REGISTRY:
        raise ValueError(
            f"No rule set registered for target library '{target_library}'. "
            f"Available: {sorted(_RULE_SET_REGISTRY)}"
        )
    return _RULE_SET_REGISTRY[key]


# ── Risk arithmetic ────────────────────────────────────────────────────

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
_ORDER_TO_RISK = {v: k for k, v in _RISK_ORDER.items()}


def _bump_risk(level: RiskLevel) -> RiskLevel:
    """Increase risk by one tier (capped at HIGH)."""
    idx = _RISK_ORDER[level]
    return _ORDER_TO_RISK[min(idx + 1, 2)]


def _max_risk(levels: list[RiskLevel]) -> RiskLevel:
    """Return the highest risk among *levels*, defaulting to LOW."""
    if not levels:
        return RiskLevel.LOW
    return max(levels, key=lambda r: _RISK_ORDER[r])


# ── Heuristic: detect test coverage ────────────────────────────────────


def _has_test_coverage(file_path: str, workspace_path: str) -> bool:
    """Return ``True`` if a plausible test file exists for *file_path*.

    Convention: ``foo.py`` is considered covered if
    ``tests/test_foo.py`` or ``test_foo.py`` (anywhere) exists.
    """
    ws = Path(workspace_path)
    stem = Path(file_path).stem
    test_name = f"test_{stem}.py"

    # Check tests/ directory
    if (ws / "tests" / test_name).exists():
        return True

    # Check anywhere in workspace
    for found in ws.rglob(test_name):
        rel_parts = found.relative_to(ws).parts
        if not any(
            part in {".venv", "venv", "__pycache__", ".git"} for part in rel_parts
        ):
            return True

    return False


# ── Graph node functions ───────────────────────────────────────────────


async def ingest(state: MigrationGraphState) -> dict[str, Any]:
    """Clone or copy the codebase into an Agent Workspace.

    Calls ``mcp-server-git`` ``clone_repo`` and populates workspace
    metadata into the graph state.
    """
    git_server = create_git_server()

    call_args: dict[str, Any] = {"source": state["source"]}
    # Prefer workspace_base_dir from state (useful for tests), then config
    if state.get("workspace_base_dir"):
        call_args["workspace_base_dir"] = state["workspace_base_dir"]
    elif not state.get("workspace_path"):
        config = load_config()
        call_args["workspace_base_dir"] = str(config.resolved_workspace_path)

    result = await git_server.call_tool("clone_repo", call_args)
    data = json.loads(result.content[0].text)

    job_id = state.get("job_id") or str(uuid.uuid4())

    logger.info(
        "Ingest complete",
        job_id=job_id,
        workspace_path=data["workspace_path"],
        repo_name=data["repo_name"],
    )

    # Resolve rule set path for the target library
    rule_set_path = resolve_rule_set(state["target_library"])

    return {
        "job_id": job_id,
        "source": state["source"],
        "target_library": state["target_library"],
        "workspace_path": data["workspace_path"],
        "repo_name": data["repo_name"],
        "base_branch": data["base_branch"],
        "rule_set_path": rule_set_path,
    }


async def scan(state: MigrationGraphState) -> dict[str, Any]:
    """Scan the workspace for files importing the Target Library.

    Calls ``mcp-server-ast`` ``scan_imports`` and stores the list of
    relative file paths that matched.
    """
    ast_server = create_ast_server()

    result = await ast_server.call_tool(
        "scan_imports",
        {
            "directory_path": state["workspace_path"],
            "target_library": state["target_library"],
        },
    )
    data = json.loads(result.content[0].text)

    logger.info(
        "Scan complete",
        job_id=state.get("job_id"),
        files_found=data["count"],
    )

    return {"scanned_files": data["relative_files"]}


async def build_plan(state: MigrationGraphState) -> dict[str, Any]:
    """Build the Migration Plan from scanned files and loaded rules.

    For each affected file:
    - loads the Migration Rules from the resolved rule set,
    - extracts affected AST nodes via ``mcp-server-ast`` ``extract_signatures``,
    - matches rules against the imported symbols,
    - calculates a per-file risk (max rule risk, bumped +1 if no test coverage).
    """
    ast_server = create_ast_server()
    rules = load_rules(state["rule_set_path"])
    workspace_path = state["workspace_path"]
    scanned_files = state["scanned_files"]

    plan_entries: list[dict[str, Any]] = []

    for rel_file in scanned_files:
        abs_path = str((Path(workspace_path) / rel_file).resolve()).replace("\\", "/")

        # Extract signatures to find affected nodes
        try:
            sig_result = await ast_server.call_tool(
                "extract_signatures", {"file_path": abs_path}
            )
            sig_data = json.loads(sig_result.content[0].text)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not extract signatures",
                file=rel_file,
                job_id=state.get("job_id"),
            )
            sig_data = {"classes": [], "functions": []}

        # Match rules against this file
        matched_rules: list[MatchedRule] = []
        for rule in rules:
            matched_rules.append(
                MatchedRule(
                    rule_id=rule["id"],
                    old_qualified_name=rule["old_qualified_name"],
                    new_qualified_name=rule["new_qualified_name"],
                    risk=RiskLevel(rule["risk"]),
                    transformer_class=rule.get("transformer_class"),
                )
            )

        # Collect affected nodes (all classes and functions in the file)
        affected_nodes: list[AffectedNode] = []
        for cls in sig_data.get("classes", []):
            affected_nodes.append(
                AffectedNode(
                    symbol_name=cls["name"],
                    node_type="class",
                    start_line=cls["start_line"],
                    end_line=cls["end_line"],
                )
            )
        for fn in sig_data.get("functions", []):
            affected_nodes.append(
                AffectedNode(
                    symbol_name=fn["name"],
                    node_type="function",
                    start_line=fn["start_line"],
                    end_line=fn["end_line"],
                )
            )

        # Compute per-file risk
        rule_risks = [r.risk for r in matched_rules]
        file_risk = _max_risk(rule_risks)
        if not _has_test_coverage(rel_file, workspace_path):
            file_risk = _bump_risk(file_risk)

        entry = FilePlanEntry(
            file_path=rel_file,
            matched_rules=matched_rules,
            affected_nodes=affected_nodes,
            risk=file_risk,
        )
        plan_entries.append(entry.model_dump())

    logger.info(
        "Migration Plan built",
        job_id=state.get("job_id"),
        files_in_plan=len(plan_entries),
    )

    return {"migration_plan": plan_entries}


def hitl_gateway(state: MigrationGraphState) -> dict[str, Any]:
    """Pause execution at the HITL Gateway.

    Calls ``interrupt()`` with the full Migration Plan, persisting
    state through the checkpointer.  The graph will not advance past
    this node until a human resumes it via ``Command(resume=...)``.
    """
    plan = state.get("migration_plan", [])

    logger.info(
        "HITL Gateway reached — waiting for human approval",
        job_id=state.get("job_id"),
        plan_files=len(plan),
    )

    # The interrupt value is surfaced to the client and contains
    # enough context for the human to review and approve/prune files.
    approved = interrupt(
        {
            "message": "Migration Plan ready for review",
            "job_id": state.get("job_id"),
            "plan": plan,
        }
    )

    # When resumed, ``approved`` contains the list of approved file paths
    # (or the full plan if the human approved everything).
    if isinstance(approved, list):
        return {"approved_files": approved}

    # If the human sent back a dict with an ``approved_files`` key
    if isinstance(approved, dict) and "approved_files" in approved:
        return {"approved_files": approved["approved_files"]}

    # Default: approve everything in the plan
    return {"approved_files": [e["file_path"] for e in plan]}


# ── Graph assembly ─────────────────────────────────────────────────────


def build_orchestration_graph() -> StateGraph:
    """Construct the Orchestration Graph (uncompiled).

    Returns the ``StateGraph`` so the caller can attach a checkpointer
    before compiling.
    """
    builder = StateGraph(MigrationGraphState)

    builder.add_node("ingest", ingest)
    builder.add_node("scan", scan)
    builder.add_node("build_plan", build_plan)
    builder.add_node("hitl_gateway", hitl_gateway)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "scan")
    builder.add_edge("scan", "build_plan")
    builder.add_edge("build_plan", "hitl_gateway")
    builder.add_edge("hitl_gateway", END)

    return builder


def compile_orchestration_graph(
    checkpointer: Any | None = None,
) -> Any:
    """Build and compile the Orchestration Graph.

    Args:
        checkpointer: A LangGraph checkpointer instance (e.g.
            ``PostgresSaver`` or ``InMemorySaver``).  Required for
            the HITL ``interrupt()`` to persist state.

    Returns:
        A compiled LangGraph ``CompiledGraph``.
    """
    builder = build_orchestration_graph()
    return builder.compile(checkpointer=checkpointer)


# ── Checkpointer factories ─────────────────────────────────────────────


@contextmanager
def get_postgres_saver(
    database_url: str | None = None,
) -> Iterator[PostgresSaver]:
    """Context manager yielding a connected ``PostgresSaver``.

    Creates the checkpoint tables via ``saver.setup()`` if they do not exist.
    Uses ``load_config().database_url`` by default.
    """
    url = database_url or load_config().database_url
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver


@asynccontextmanager
async def get_async_postgres_saver(
    database_url: str | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Async context manager yielding a connected ``AsyncPostgresSaver``.

    Creates the checkpoint tables via ``await saver.setup()`` if they do not exist.
    Uses ``load_config().database_url`` by default.
    """
    url = database_url or load_config().database_url
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        yield saver
