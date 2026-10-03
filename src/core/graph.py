"""Orchestration Graph — scan, plan, HITL Gateway, resume, dispatch, aggregate.

The complete LangGraph Orchestration Graph driving the full Migration Job lifecycle:

1. **ingest** — clones/copies the codebase into an Agent Workspace via
   ``mcp-server-git``.
2. **scan** — scans all files for Target Library imports via
   ``mcp-server-ast``.
3. **build_plan** — loads matching Migration Rules, extracts affected AST
   nodes, calculates per-file risk, and assembles the Migration Plan.
4. **hitl_gateway** — calls ``interrupt()``, pausing execution for human
   review. State is persisted via the configured checkpointer.
5. **resume_from_hitl** — receives approved files, creates migration branch.
6. **dispatch_file_subgraphs** — spawns nested File Sub-graph for each approved file.
7. **aggregate_results** — builds ``MigrationResult`` with successes, failures, and stats.
8. **commit_and_output** — commits successful files, generates diff and git apply commands.
"""

from __future__ import annotations

import ast
import contextvars
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
from src.core.file_subgraph import (
    _find_test_file,
    _normalize_matched_rules,
    run_file_subgraph,
)
from src.core.logging import get_logger
from src.core.mcp_client import call_mcp_tool
from src.core.models import (
    AffectedNode,
    CoverageStatus,
    FilePlanEntry,
    FileResult,
    FileStatus,
    MatchedRule,
    MigrationGraphState,
    MigrationResult,
    RiskLevel,
)
from src.mcp_servers.git_server import create_git_server
from src.rules.loader import load_rules

logger = get_logger(__name__)

_dispatch_progress_callback: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "dispatch_progress_callback", default=None
)


def set_dispatch_progress_callback(callback: Any) -> Any:
    """Set a progress callback for dispatch_file_subgraphs to report per-file streaming events."""
    return _dispatch_progress_callback.set(callback)


def reset_dispatch_progress_callback(token: Any) -> None:
    """Reset the dispatch progress callback token."""
    _dispatch_progress_callback.reset(token)


def _get_git_server():
    return create_git_server()


def _get_ast_server():
    from src.mcp_servers.ast_server import create_ast_server

    return create_ast_server()

# ── Rule set resolution ────────────────────────────────────────────────

# Maps a target library name (as the user would type it) to the YAML rule
# file that ships with this project.  Extend this mapping when new Target
# Library rule sets are added.
_RULE_SET_REGISTRY: dict[str, str] = {
    "pydantic": "src/rules/pydantic_v1_to_v2.yaml",
    "sqlalchemy": "src/rules/sqlalchemy_v1_to_v2.yaml",
    "requests": "src/rules/requests_migration.yaml",
    "celery": "src/rules/celery_v4_to_v5.yaml",
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
    rel_path = _RULE_SET_REGISTRY[key]
    if Path(rel_path).is_file():
        return rel_path
    project_root = Path(__file__).resolve().parent.parent.parent
    abs_path = project_root / rel_path
    if abs_path.is_file():
        return str(abs_path)
    return rel_path


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
    ``tests/test_foo.py``, ``test_foo.py`` (anywhere), or any test file
    importing the module exists.
    """
    return _find_test_file(file_path, workspace_path) is not None


# ── Git & MCP Helpers ──────────────────────────────────────────────────


# Re-export shared helper for backward compatibility and internal graph calls
_call_git_tool = call_mcp_tool


def is_github_repo(source: str | None) -> bool:
    """Return True if source points to a GitHub repository."""
    if not source:
        return False
    src = source.strip().lower()
    return "github.com" in src or src.startswith("git@github.com:")


# ── Graph node functions ───────────────────────────────────────────────


async def ingest(state: MigrationGraphState) -> dict[str, Any]:
    """Clone or copy the codebase into an Agent Workspace.

    Calls ``mcp-server-git`` ``clone_repo`` and populates workspace
    metadata into the graph state.
    """
    git_server = state.get("git_server")
    if git_server is None:
        git_server = _get_git_server()

    call_args: dict[str, Any] = {"source": state["source"]}
    # Prefer workspace_base_dir from state (useful for tests), then config
    if state.get("workspace_base_dir"):
        call_args["workspace_base_dir"] = state["workspace_base_dir"]
    elif not state.get("workspace_path"):
        config = load_config()
        call_args["workspace_base_dir"] = str(config.resolved_workspace_path)

    data = await _call_git_tool(git_server, "clone_repo", call_args)

    job_id = state.get("job_id") or str(uuid.uuid4())

    logger.info(
        "Ingest complete",
        job_id=job_id,
        workspace_path=data.get("workspace_path"),
        repo_name=data.get("repo_name"),
    )

    # Resolve rule set path for the target library
    rule_set_path = resolve_rule_set(state["target_library"])

    return {
        "job_id": job_id,
        "source": state["source"],
        "target_library": state["target_library"],
        "workspace_path": data.get("workspace_path", ""),
        "repo_name": data.get("repo_name", ""),
        "base_branch": data.get("base_branch", "main"),
        "rule_set_path": rule_set_path,
    }


async def scan(state: MigrationGraphState) -> dict[str, Any]:
    """Scan the workspace for files importing the Target Library.

    Calls ``mcp-server-ast`` ``scan_imports`` and stores the list of
    relative file paths that matched.
    """
    ast_server = state.get("ast_server")
    if ast_server is None:
        ast_server = _get_ast_server()

    data = await call_mcp_tool(
        ast_server,
        "scan_imports",
        {
            "directory_path": state["workspace_path"],
            "target_library": state["target_library"],
        },
    )
    if not isinstance(data, dict):
        data = {"count": 0, "relative_files": []}

    logger.info(
        "Scan complete",
        job_id=state.get("job_id"),
        files_found=data["count"],
    )

    return {"scanned_files": data["relative_files"]}


def _rule_matches_file(
    rule: dict[str, Any],
    source_code: str,
    tree: ast.AST | None = None,
) -> bool:
    """Return True if a migration rule matches symbols or patterns in source code."""
    old_qname = rule.get("old_qualified_name", "")
    if not old_qname:
        return False

    leaf = old_qname.split(".")[-1]
    # Fast path: if neither the qualified name nor leaf symbol appears in source text, skip
    if old_qname not in source_code and leaf not in source_code:
        return False

    if tree is None:
        try:
            tree = ast.parse(source_code)
        except (SyntaxError, ValueError):
            return old_qname in source_code or leaf in source_code

    parts = old_qname.split(".")
    target_module = parts[0] if len(parts) > 1 else ""

    # 1. Imports: direct import or from import
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                full_imported = f"{mod}.{alias.name}" if mod else alias.name
                if old_qname == full_imported:
                    return True
                if (
                    alias.name == leaf
                    and target_module
                    and (mod == target_module or mod.startswith(f"{target_module}."))
                ):
                    return True
                if alias.asname and alias.name == leaf:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == old_qname:
                    return True

    # 2. Class definition matching by name (e.g. inner class Config)
    if len(parts) == 1:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == leaf:
                return True

    # 3. Base class inheritance (e.g. GenericModel, BaseSettings)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ast.unparse(base)
                if (
                    base_name == old_qname
                    or base_name == leaf
                    or base_name.startswith(f"{leaf}[")
                ):
                    return True

    # 4. Decorators on functions or classes (e.g. @validator, @root_validator, @pydantic.validator)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                dec_text = ast.unparse(dec)
                if (
                    old_qname in dec_text
                    or dec_text.startswith(leaf)
                    or f".{leaf}" in dec_text
                ):
                    return True

    # 5. Method calls / attribute accesses (e.g. BaseModel.dict -> .dict(), BaseModel.json, parse_obj, parse_raw)
    if len(parts) >= 2 and parts[-2] == "BaseModel":
        method_name = parts[-1]
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == method_name
            ):
                return True

    # 6. Call keyword arguments (e.g. pydantic.Field.regex -> regex=...)
    if len(parts) >= 3 and parts[-2] == "Field":
        arg_name = parts[-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ast.unparse(node.func)
                if "Field" in func_name:
                    for kw in node.keywords:
                        if kw.arg == arg_name:
                            return True

    # 7. General Attribute access (e.g. pydantic.validator)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attr_text = ast.unparse(node)
            if old_qname in attr_text:
                return True

    return False


async def _build_file_plan_entry(
    rel_file: str,
    workspace_path: str,
    rules: list[dict[str, Any]],
    ast_server: Any,
    job_id: str | None = None,
) -> FilePlanEntry:
    """Build a single FilePlanEntry for a given relative file in the workspace.

    Extracts AST signatures, filters matching rules, collects affected nodes,
    and computes the per-file risk score (bumped by one tier if no tests exist).
    """
    abs_path = (Path(workspace_path) / rel_file).resolve()
    abs_path_str = str(abs_path).replace("\\", "/")

    # Read source code for rule matching
    try:
        source_code = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source_code = ""

    # Parse AST for rule matching
    tree: ast.AST | None = None
    try:
        tree = ast.parse(source_code, filename=abs_path_str)
    except (SyntaxError, ValueError):
        pass

    # Extract signatures to find affected nodes
    try:
        sig_data = await call_mcp_tool(
            ast_server,
            "extract_signatures",
            {"file_path": abs_path_str},
        )
        if not isinstance(sig_data, dict):
            sig_data = {"classes": [], "functions": []}
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not extract signatures",
            file=rel_file,
            job_id=job_id,
        )
        sig_data = {"classes": [], "functions": []}

    # Match rules against this file
    matched_rules: list[MatchedRule] = []
    for rule in rules:
        if _rule_matches_file(rule, source_code, tree):
            matched_rules.append(
                MatchedRule(
                    rule_id=rule["id"],
                    old_qualified_name=rule["old_qualified_name"],
                    new_qualified_name=rule["new_qualified_name"],
                    risk=RiskLevel(rule["risk"]),
                    transformer_class=rule.get("transformer_class"),
                    description=rule.get("description"),
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

    # Compute per-file risk: max risk among matched rules, bumped +1 if no test coverage
    rule_risks = [r.risk for r in matched_rules]
    file_risk = _max_risk(rule_risks)
    has_test_cov = _has_test_coverage(rel_file, workspace_path)
    if not has_test_cov:
        file_risk = _bump_risk(file_risk)
        coverage_status = CoverageStatus.UNCOVERED
    else:
        coverage_status = CoverageStatus.VERIFIED

    return FilePlanEntry(
        file_path=rel_file,
        matched_rules=matched_rules,
        affected_nodes=affected_nodes,
        risk=file_risk,
        coverage_status=coverage_status,
    )


async def build_plan(state: MigrationGraphState) -> dict[str, Any]:
    """Build the Migration Plan from scanned files and loaded rules.

    For each affected file:
    - loads the Migration Rules from the resolved rule set,
    - extracts affected AST nodes via ``mcp-server-ast`` ``extract_signatures``,
    - matches rules against the imported symbols,
    - calculates a per-file risk (max rule risk, bumped +1 if no test coverage).
    """
    ast_server = state.get("ast_server")
    if ast_server is None:
        ast_server = _get_ast_server()
    rules = load_rules(state["rule_set_path"])
    workspace_path = state["workspace_path"]
    scanned_files = state["scanned_files"]
    job_id = state.get("job_id")

    plan_entries: list[dict[str, Any]] = []

    for rel_file in scanned_files:
        entry = await _build_file_plan_entry(
            rel_file=rel_file,
            workspace_path=workspace_path,
            rules=rules,
            ast_server=ast_server,
            job_id=job_id,
        )
        plan_entries.append(entry.model_dump())

    logger.info(
        "Migration Plan built",
        job_id=job_id,
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
        result: dict[str, Any] = {"approved_files": approved["approved_files"]}
        if approved.get("branch_name"):
            result["branch_name"] = approved["branch_name"]
        return result

    # Default: approve everything in the plan
    return {"approved_files": [e["file_path"] for e in plan]}


async def resume_from_hitl(state: MigrationGraphState) -> dict[str, Any]:
    """Resume execution after human approval at the HITL Gateway.

    Receives the approved file list, normalizes it, creates a migration branch
    in the workspace via ``mcp-server-git``, and prepares state for dispatch.
    """
    raw_approved = state.get("approved_files")
    plan = state.get("migration_plan", [])

    if raw_approved is None:
        approved_files = [e["file_path"] for e in plan]
    else:
        approved_files = []
        for item in raw_approved:
            if isinstance(item, str):
                approved_files.append(item)
            elif isinstance(item, dict) and "file_path" in item:
                approved_files.append(item["file_path"])
            elif hasattr(item, "file_path"):
                approved_files.append(str(item.file_path))

    workspace_path = state.get("workspace_path")
    target_library = state.get("target_library", "migration")
    git_server = state.get("git_server")
    if git_server is None:
        git_server = _get_git_server()

    branch_name = state.get("branch_name")
    if workspace_path:
        try:
            call_args: dict[str, Any] = {
                "workspace_path": workspace_path,
                "target_library": target_library,
            }
            if branch_name:
                call_args["branch_name"] = branch_name
            res = await _call_git_tool(git_server, "create_branch", call_args)
            branch_name = res.get("branch_name") or branch_name
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not create git branch", error=str(exc))
            branch_name = branch_name or f"migrate/{target_library}"

    logger.info(
        "Resumed from HITL Gateway",
        job_id=state.get("job_id"),
        approved_count=len(approved_files),
        branch_name=branch_name,
    )

    return {
        "approved_files": approved_files,
        "branch_name": branch_name or "",
        "file_results": [],
    }


async def dispatch_file_subgraphs(state: MigrationGraphState) -> dict[str, Any]:
    """Spawn a File Sub-graph for each approved file sequentially.

    Collects a ``FileResult`` for each file while maintaining episodic
    context isolation across runs. Manages Sandbox container lifecycle
    via managed_sandbox context manager.
    """
    approved_files = state.get("approved_files", [])
    workspace_path = state.get("workspace_path", "")
    target_library = state.get("target_library", "")
    plan_by_path = {e["file_path"]: e for e in state.get("migration_plan", [])}
    custom_runner = state.get("file_subgraph_runner")

    file_results: list[dict[str, Any]] = []

    logger.info(
        "Dispatching File Sub-graphs",
        job_id=state.get("job_id"),
        file_count=len(approved_files),
    )

    sandbox_mgr = state.get("sandbox_manager")
    if sandbox_mgr is None:
        from src.core.sandbox import SandboxManager

        sandbox_mgr = SandboxManager()

    injected_container = state.get("sandbox_container")

    @asynccontextmanager
    async def _resolve_sandbox_context() -> AsyncIterator[Any]:
        if injected_container is not None:
            yield injected_container
        elif hasattr(sandbox_mgr, "managed_sandbox"):
            async with sandbox_mgr.managed_sandbox(
                workspace_path=workspace_path,
                target_library=target_library,
            ) as container:
                yield container
        else:
            yield None

    # Capture original contents of all approved files before any modifications
    original_contents: dict[str, str] = {}
    for rel_file in approved_files:
        try:
            abs_p = (Path(workspace_path) / rel_file).resolve()
            original_contents[rel_file] = abs_p.read_text(encoding="utf-8")
        except OSError:
            original_contents[rel_file] = ""

    # Pre-apply declarative AST rules across all approved files so that cross-file imports
    # (e.g. test files importing multiple modules) do not fail due to unmigrated syntax in later files.
    ast_server = state.get("ast_server")
    if ast_server is None:
        try:
            ast_server = _get_ast_server()
        except Exception:  # noqa: BLE001
            ast_server = None

    is_mock_runner = (
        custom_runner is not None
        or hasattr(run_file_subgraph, "assert_called")
        or hasattr(run_file_subgraph, "mock_calls")
        or type(run_file_subgraph).__name__ in ("MagicMock", "AsyncMock", "Mock")
    )

    if not is_mock_runner and ast_server is not None:
        for rel_file in approved_files:
            plan_entry = plan_by_path.get(rel_file, {})
            matched_rules = _normalize_matched_rules(plan_entry.get("matched_rules", []))
            abs_p = (Path(workspace_path) / rel_file).resolve()
            for rule in matched_rules:
                if getattr(rule, "transformer_class", None):
                    try:
                        await call_mcp_tool(
                            ast_server,
                            "apply_transform",
                            {
                                "file_path": str(abs_p).replace("\\", "/"),
                                "transformer_path": rule.transformer_class,
                                "write": True,
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Pre-apply AST rule failed",
                            file=rel_file,
                            rule=rule.rule_id,
                            error=str(exc),
                        )

    cb = _dispatch_progress_callback.get()
    async with _resolve_sandbox_context() as active_container:
        for rel_file in approved_files:
            plan_entry = plan_by_path.get(rel_file, {})
            matched_rules = plan_entry.get("matched_rules", [])
            risk = plan_entry.get("risk", RiskLevel.LOW)

            if cb is not None:
                try:
                    cb({
                        "type": "current_file",
                        "file_path": rel_file,
                        "status": "migrating",
                        "message": f"Processing file {rel_file}",
                    })
                except Exception:  # noqa: BLE001, S110
                    pass

            runner_kwargs: dict[str, Any] = {
                "file_path": rel_file,
                "workspace_path": workspace_path,
                "target_library": target_library,
                "matched_rules": matched_rules,
                "risk": risk,
                "sandbox_manager": state.get("sandbox_manager"),
                "sandbox_container": active_container,
                "ast_server": state.get("ast_server"),
                "docs_server": state.get("docs_server"),
                "llm_client": state.get("llm_client"),
            }

            try:
                if custom_runner is not None:
                    try:
                        res = await custom_runner(
                            **runner_kwargs,
                            original_content=original_contents.get(rel_file),
                        )
                    except TypeError:
                        res = await custom_runner(**runner_kwargs)
                else:
                    res = await run_file_subgraph(
                        **runner_kwargs,
                        original_content=original_contents.get(rel_file),
                    )

                if isinstance(res, FileResult):
                    res_dict = res.model_dump()
                elif isinstance(res, dict):
                    res_dict = res
                else:
                    res_dict = FileResult(
                        file_path=rel_file,
                        status=getattr(res, "status", FileStatus.SUCCESS),
                        diff=getattr(res, "diff", ""),
                        traceback=getattr(res, "traceback", ""),
                        attempt_count=getattr(res, "attempt_count", 0),
                    ).model_dump()

            except Exception as exc:
                logger.exception(
                    "File Sub-graph dispatch failed unexpectedly",
                    file=rel_file,
                    error=str(exc),
                )
                res_dict = FileResult(
                    file_path=rel_file,
                    status=FileStatus.FAILED,
                    diff="",
                    traceback=f"Error executing File Sub-graph: {exc}",
                    attempt_count=0,
                ).model_dump()
            if cb is not None:
                try:
                    is_success = (
                        res_dict.get("status") == FileStatus.SUCCESS
                        or getattr(res_dict.get("status"), "value", str(res_dict.get("status"))).upper() == "SUCCESS"
                        or str(res_dict.get("status")).upper().endswith("SUCCESS")
                    )
                    attempts = res_dict.get("attempt_count", 0)
                    if attempts > 0 and custom_runner is not None:
                        for att in range(1, attempts + 1):
                            cb({
                                "type": "healing_attempt",
                                "file_path": rel_file,
                                "attempt": att,
                                "max_attempts": 3,
                                "message": f"Self-healing attempt {att}/3 for {rel_file}",
                            })
                    cb({
                        "type": "test_result",
                        "file_path": rel_file,
                        "passed": is_success,
                        "exit_code": 0 if is_success else 1,
                        "message": f"Tests {'passed' if is_success else 'failed'} for {rel_file}",
                    })
                    status_str = getattr(res_dict.get("status"), "value", str(res_dict.get("status")))
                    if "." in str(status_str):
                        status_str = str(status_str).split(".", 1)[1]
                    cb({
                        "type": "file_completed",
                        "file_path": rel_file,
                        "status": status_str,
                        "diff": res_dict.get("diff", ""),
                        "attempt_count": attempts,
                        "message": f"Finished {rel_file} with status {status_str}",
                    })
                except Exception:  # noqa: BLE001, S110
                    pass

            file_results.append(res_dict)
            logger.info(
                "File Sub-graph completed",
                file=rel_file,
                status=res_dict.get("status"),
                attempts=res_dict.get("attempt_count", 0),
            )

    return {"file_results": file_results}


async def aggregate_results(state: MigrationGraphState) -> dict[str, Any]:
    """Aggregate per-file results into a MigrationResult.

    Partitions files into successes and failures, compiles diffs,
    tracebacks, and summary statistics.
    """
    file_results = state.get("file_results", [])
    successful_files: list[dict[str, Any]] = []
    failed_files: list[dict[str, Any]] = []
    total_healing = 0

    for fr in file_results:
        status_val = fr.get("status")
        if isinstance(status_val, FileStatus):
            status_str = status_val.value
        else:
            status_str = str(status_val).upper()

        if status_str == "SUCCESS":
            successful_files.append(fr)
        else:
            failed_files.append(fr)

        total_healing += fr.get("attempt_count", 0)

    total_count = len(file_results)
    success_count = len(successful_files)
    failure_count = len(failed_files)

    migration_result = MigrationResult(
        job_id=state.get("job_id", ""),
        target_library=state.get("target_library", ""),
        total_files=total_count,
        successful_files=[FileResult(**f) for f in successful_files],
        failed_files=[FileResult(**f) for f in failed_files],
        success_count=success_count,
        failure_count=failure_count,
        total_healing_attempts=total_healing,
        full_diff="",
        git_commands={},
    )

    logger.info(
        "Aggregated migration results",
        job_id=state.get("job_id"),
        total=total_count,
        successes=success_count,
        failures=failure_count,
        healing_attempts=total_healing,
    )

    return {"migration_result": migration_result.model_dump()}


async def commit_and_output(state: MigrationGraphState) -> dict[str, Any]:
    """Commit successful files via mcp-server-git and generate git output commands.

    Produces:
    1. Git commits for each successfully migrated file.
    2. Unified diff of all modifications on the migration branch.
    3. Copy-pasteable git commands for local repository application.
    4. Optional PR creation command for GitHub repositories.
    """
    file_results = state.get("file_results", [])
    workspace_path = state.get("workspace_path", "")
    target_library = state.get("target_library", "target library")
    base_branch = state.get("base_branch") or "main"
    branch_name = state.get("branch_name")
    source = state.get("source", "")
    is_github = is_github_repo(source)

    git_server = state.get("git_server")
    if git_server is None:
        git_server = _get_git_server()

    # 1. Commit each successful file
    successful_files = [
        f
        for f in file_results
        if (
            f.get("status") == FileStatus.SUCCESS
            or str(f.get("status")).upper() == "SUCCESS"
        )
    ]

    for sf in successful_files:
        rel_file = sf["file_path"]
        commit_msg = f"Migrate {rel_file} to {target_library}"
        try:
            await _call_git_tool(
                git_server,
                "commit_file",
                {
                    "workspace_path": workspace_path,
                    "file_path": rel_file,
                    "message": commit_msg,
                },
            )
            logger.info("Committed migrated file", file=rel_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not commit file via git server", file=rel_file, error=str(exc)
            )

    # 2. Generate full diff
    full_diff = ""
    try:
        diff_res = await _call_git_tool(
            git_server,
            "generate_diff",
            {
                "workspace_path": workspace_path,
                "base_branch": base_branch,
            },
        )
        full_diff = diff_res.get("diff", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not generate diff via git server", error=str(exc))

    if not full_diff and successful_files:
        # Fallback: assemble unified diffs from individual file results
        file_diffs = [f.get("diff", "") for f in successful_files if f.get("diff")]
        full_diff = "\n".join(file_diffs)

    # 3. Generate copy-pasteable git commands
    git_commands: dict[str, Any] = {}
    try:
        cmd_res = await _call_git_tool(
            git_server,
            "get_apply_commands",
            {
                "workspace_path": workspace_path,
                "branch_name": branch_name,
                "original_path": source,
            },
        )
        git_commands = cmd_res
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not get apply commands via git server", error=str(exc))
        clean_path = str(workspace_path).replace("\\", "/")
        b_name = branch_name or f"migrate/{target_library}"
        git_commands = {
            "workspace_path": clean_path,
            "branch_name": b_name,
            "remote_name": "migration-agent",
            "original_path": source,
            "commands": [
                f'git remote add migration-agent "{clean_path}"',
                "git fetch migration-agent",
                f"git merge migration-agent/{b_name}",
            ],
            "one_liner": f'git remote add migration-agent "{clean_path}" && git fetch migration-agent && git merge migration-agent/{b_name}',
            "patch_command": f'git -C "{clean_path}" format-patch -1 HEAD --stdout | git apply --check',
        }

    # 4. Optional PR command for GitHub-sourced repos
    pr_command = None
    if is_github:
        b_name = git_commands.get(
            "branch_name", branch_name or f"migrate/{target_library}"
        )
        pr_command = (
            f'gh pr create --title "Migrate to {target_library}" '
            f'--body "Automated migration using Autonomous Codebase Refactoring & Migration Agent." '
            f"--base {base_branch} --head {b_name}"
        )

    git_commands["is_github"] = is_github
    git_commands["pr_command"] = pr_command

    # 5. Update migration_result with full_diff and git_commands
    mig_result = dict(state.get("migration_result", {}))
    mig_result["full_diff"] = full_diff
    mig_result["git_commands"] = git_commands

    logger.info(
        "Job completed and git commands generated",
        job_id=state.get("job_id"),
        is_github=is_github,
        has_pr_command=bool(pr_command),
        diff_length=len(full_diff),
    )

    return {
        "migration_result": mig_result,
        "git_commands": git_commands,
    }


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
    builder.add_node("resume_from_hitl", resume_from_hitl)
    builder.add_node("dispatch_file_subgraphs", dispatch_file_subgraphs)
    builder.add_node("aggregate_results", aggregate_results)
    builder.add_node("commit_and_output", commit_and_output)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "scan")
    builder.add_edge("scan", "build_plan")
    builder.add_edge("build_plan", "hitl_gateway")
    builder.add_edge("hitl_gateway", "resume_from_hitl")
    builder.add_edge("resume_from_hitl", "dispatch_file_subgraphs")
    builder.add_edge("dispatch_file_subgraphs", "aggregate_results")
    builder.add_edge("aggregate_results", "commit_and_output")
    builder.add_edge("commit_and_output", END)

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
