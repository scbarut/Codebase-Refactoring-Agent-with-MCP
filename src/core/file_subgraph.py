"""File Sub-graph — rewrite, test, Self-Healing Loop.

A per-file LangGraph sub-graph that processes a single file:
1. applies declarative rules via ``mcp-server-ast`` (or flags unmatched for LLM fallback),
2. falls back to LiteLLM for unmatched patterns using ``mcp-server-docs`` context,
3. executes scoped tests in the Sandbox container,
4. if tests fail, enters the Self-Healing Loop:
   extract traceback -> query docs -> patch code -> re-test (capped at 3 attempts),
5. finalizes and returns a ``FileResult`` with status, diff, traceback, and attempt count.

Maintains episodic context isolation: state is self-contained per file.
"""

from __future__ import annotations

import difflib
import inspect
import json
import os
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.core.config import Settings, load_config
from src.core.logging import get_logger
from src.core.models import FileResult, FileStatus, MatchedRule, RiskLevel
from src.core.sandbox import SandboxManager
from src.mcp_servers.ast_server import create_ast_server
from src.mcp_servers.docs_server import create_docs_server

logger = get_logger(__name__)


# ── State Schema ───────────────────────────────────────────────────────


class FileSubgraphState(TypedDict, total=False):
    """Episodic state carried through the File Sub-graph for a single file."""

    # File identity & context
    file_path: str
    workspace_path: str
    target_library: str
    risk: RiskLevel | str

    # Matched rules & AST context
    matched_rules: list[MatchedRule] | list[Any]
    unmatched_rules: list[dict[str, Any]]
    affected_nodes: list[dict[str, Any]]

    # Content tracking
    original_content: str
    current_content: str

    # Execution dependencies (injectable / stubbable)
    sandbox_manager: Any
    sandbox_container: Any
    ast_server: Any
    docs_server: Any
    llm_client: Any

    # Test & Self-Healing state
    test_result: Any
    passed: bool
    exit_code: int
    output: str
    traceback: str
    doc_context: str
    healing_attempts: int
    max_healing_attempts: int

    # Final result
    status: str
    diff: str
    attempt_count: int
    file_result: dict[str, Any]
    error: str | None


# ── Helpers ────────────────────────────────────────────────────────────


def route_model(risk: RiskLevel | str | None, config: Settings | None = None) -> str:
    """Return model identifier based on risk level according to ADR-0001.

    Routes ``gemini-flash-lite`` for LOW-risk rewrites, and
    ``gemini-flash`` for MEDIUM/HIGH or no-rule-match rewrites.
    """
    if config is None:
        config = load_config()
    if risk is None:
        return config.model_default
    risk_val = risk.value if isinstance(risk, RiskLevel) else str(risk).upper()
    if risk_val == "LOW":
        return config.model_lite
    return config.model_default


def _clean_code_fence(text: str) -> str:
    """Strip markdown code block fences (```python ... ```) from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop leading ``` or ```python
        lines = lines[1:]
        # Drop trailing ```
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _compute_unified_diff(original: str, current: str, file_path: str) -> str:
    """Produce unified diff text between original and modified file contents."""
    if original == current:
        return ""
    orig_lines = original.splitlines(keepends=True)
    curr_lines = current.splitlines(keepends=True)
    norm_path = file_path.replace("\\", "/")
    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            curr_lines,
            fromfile=f"a/{norm_path}",
            tofile=f"b/{norm_path}",
        )
    )
    return "".join(diff_lines)


def _resolve_file_path(workspace_path: str, file_path: str) -> Path:
    """Resolve a relative workspace file path to an absolute Path."""
    return (Path(workspace_path) / file_path).resolve()


def _normalize_matched_rules(rules: list[Any] | None) -> list[MatchedRule]:
    """Normalize input rules so they are always MatchedRule instances."""
    if not rules:
        return []
    normalized: list[MatchedRule] = []
    for r in rules:
        if isinstance(r, MatchedRule):
            normalized.append(r)
        elif isinstance(r, dict):
            normalized.append(MatchedRule(**r))
        else:
            normalized.append(
                MatchedRule(
                    rule_id=r.rule_id,
                    old_qualified_name=r.old_qualified_name,
                    new_qualified_name=r.new_qualified_name,
                    risk=RiskLevel(r.risk),
                    transformer_class=getattr(r, "transformer_class", None),
                )
            )
    return normalized


def _find_test_file(file_path: str, workspace_path: str) -> str | None:
    """Find the path of a test file corresponding to file_path, relative to workspace."""
    ws = Path(workspace_path)
    stem = Path(file_path).stem
    test_name = f"test_{stem}.py"

    # 1. Check tests/ directory
    if (ws / "tests" / test_name).exists():
        return f"tests/{test_name}"

    # 2. Check anywhere in workspace (excluding ignored dirs)
    for found in ws.rglob(test_name):
        rel_parts = found.relative_to(ws).parts
        if not any(
            part in {".venv", "venv", "__pycache__", ".git"} for part in rel_parts
        ):
            return str(found.relative_to(ws)).replace("\\", "/")

    # 3. If file_path itself is a test file
    if "test" in stem.lower() and (ws / file_path).exists():
        return str(file_path).replace("\\", "/")

    return None


def _extract_query_from_traceback(tb: str) -> str:
    """Extract key error lines from a pytest traceback to use as doc search query."""
    lines = tb.strip().splitlines()
    error_lines = [
        line.strip().lstrip("E ").strip()
        for line in lines
        if line.strip().startswith("E ") or "Error:" in line or "Exception:" in line
    ]
    if error_lines:
        return error_lines[-1]
    # Fallback to last non-empty line
    for line in reversed(lines):
        if line.strip() and not line.strip().startswith("="):
            return line.strip()
    return tb[:200]


async def _call_mcp_tool(
    server: Any, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Call an MCP server tool and parse result cleanly into a dict."""
    if hasattr(server, "call_tool"):
        res = server.call_tool(tool_name, arguments)
        if inspect.iscoroutine(res):
            res = await res
        if hasattr(res, "content") and res.content:
            text = res.content[0].text
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
            return text
        if isinstance(res, dict):
            return res
        return {}
    if hasattr(server, tool_name):
        fn = getattr(server, tool_name)
        res = fn(**arguments)
        if inspect.iscoroutine(res):
            res = await res
        return res if isinstance(res, dict) else {}
    raise ValueError(f"Server does not support tool '{tool_name}'")


async def _call_llm(
    model: str,
    messages: list[dict[str, str]],
    llm_client: Any = None,
) -> str:
    """Call an LLM client or LiteLLM."""
    if llm_client is not None:
        if inspect.iscoroutinefunction(llm_client):
            res = await llm_client(model=model, messages=messages)
        elif callable(llm_client):
            res = llm_client(model=model, messages=messages)
            if inspect.iscoroutine(res):
                res = await res
        elif hasattr(llm_client, "acompletion"):
            res = await llm_client.acompletion(model=model, messages=messages)
        elif hasattr(llm_client, "completion"):
            res = llm_client.completion(model=model, messages=messages)
            if inspect.iscoroutine(res):
                res = await res
        else:
            raise ValueError(f"Unsupported llm_client type: {type(llm_client)}")

        if isinstance(res, str):
            return res
        if hasattr(res, "choices") and res.choices:
            choice = res.choices[0]
            if hasattr(choice, "message"):
                return getattr(choice.message, "content", "") or ""
            if isinstance(choice, dict):
                return choice.get("message", {}).get("content", "")
        if isinstance(res, dict):
            if "content" in res:
                return res["content"]
            if "choices" in res:
                return res["choices"][0]["message"]["content"]
        return str(res)

    import litellm

    config = load_config()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    # Pass explicit Gemini API key if using Gemini provider; otherwise LiteLLM auto-detects from environment
    if model.startswith("gemini") and (
        config.gemini_api_key or os.environ.get("GEMINI_API_KEY")
    ):
        kwargs["api_key"] = config.gemini_api_key or os.environ.get("GEMINI_API_KEY")

    response = await litellm.acompletion(**kwargs)
    return response.choices[0].message.content or ""


# ── Graph Node Functions ───────────────────────────────────────────────


async def apply_rules(state: FileSubgraphState) -> dict[str, Any]:
    """Apply declarative rules via libcst CSTTransformers.

    For each matched Migration Rule:
    - If a ``transformer_class`` exists, applies it via ``mcp-server-ast`` ``apply_transform``.
    - If no transformer exists, or if no rules matched at all, flags pattern for LLM fallback.
    """
    file_path = state["file_path"]
    workspace_path = state["workspace_path"]
    abs_path = _resolve_file_path(workspace_path, file_path)

    try:
        content = abs_path.read_text(encoding="utf-8")
    except OSError as err:
        logger.error(
            "Failed to read file for rewrite", file_path=file_path, error=str(err)
        )
        return {
            "original_content": "",
            "current_content": "",
            "unmatched_rules": [],
            "error": str(err),
        }

    original_content = state.get("original_content") or content
    matched_rules = _normalize_matched_rules(state.get("matched_rules"))

    ast_server = state.get("ast_server")
    if ast_server is None:
        ast_server = create_ast_server()

    unmatched_rules: list[dict[str, Any]] = []

    # If no matched rules at all, flag file for LLM fallback
    if not matched_rules:
        unmatched_rules.append(
            {
                "rule_id": "unmatched-general",
                "risk": state.get("risk", RiskLevel.MEDIUM),
            }
        )

    for rule in matched_rules:
        if rule.transformer_class:
            try:
                res = await _call_mcp_tool(
                    ast_server,
                    "apply_transform",
                    {
                        "file_path": str(abs_path).replace("\\", "/"),
                        "transformer_path": rule.transformer_class,
                        "write": True,
                    },
                )
                logger.info(
                    "Applied AST transform rule",
                    rule_id=rule.rule_id,
                    transformer=rule.transformer_class,
                    modified=res.get("modified", False),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "AST transform failed, flagging for LLM fallback",
                    rule_id=rule.rule_id,
                    transformer=rule.transformer_class,
                    error=str(e),
                )
                unmatched_rules.append(rule.model_dump())
        else:
            # Declarative transformer not defined for this rule
            unmatched_rules.append(rule.model_dump())

    try:
        current_content = abs_path.read_text(encoding="utf-8")
    except OSError:
        current_content = original_content

    return {
        "original_content": original_content,
        "current_content": current_content,
        "unmatched_rules": unmatched_rules,
    }


def route_after_apply(state: FileSubgraphState) -> str:
    """Conditional router after apply_rules."""
    if state.get("unmatched_rules"):
        return "llm_fallback"
    return "run_tests"


async def llm_fallback(state: FileSubgraphState) -> dict[str, Any]:
    """LLM fallback node for patterns without declarative transformers.

    Retrieves AST node context and Doc Corpus chunks, selects model via
    tiered routing (gemini-flash-lite for LOW risk, gemini-flash for MEDIUM/HIGH/unmatched),
    prompts the model, and updates the file on disk.
    """
    unmatched = state.get("unmatched_rules", [])
    if not unmatched:
        return {}

    workspace_path = state["workspace_path"]
    file_path = state["file_path"]
    abs_path = _resolve_file_path(workspace_path, file_path)
    current_content = state.get("current_content") or abs_path.read_text(
        encoding="utf-8"
    )
    target_library = state.get("target_library", "target library")

    docs_server = state.get("docs_server")
    if docs_server is None:
        docs_server = create_docs_server()

    # Determine highest risk among unmatched rules
    rule_risks = [
        r.get("risk") for r in unmatched if isinstance(r, dict) and r.get("risk")
    ]
    file_risk = state.get("risk")
    if file_risk:
        rule_risks.append(file_risk)

    highest_risk = RiskLevel.LOW
    for r in rule_risks:
        r_str = r.value if isinstance(r, RiskLevel) else str(r).upper()
        if r_str in ("HIGH", "MEDIUM"):
            highest_risk = RiskLevel(r_str)
            if r_str == "HIGH":
                break

    model = route_model(highest_risk)
    logger.info(
        "Routing LLM fallback", model=model, risk=highest_risk.value, file=file_path
    )

    # Query docs for unmatched rules
    doc_sections: list[str] = []
    for r in unmatched:
        query = (
            r.get("old_qualified_name")
            or r.get("description")
            or r.get("rule_id")
            or target_library
        )
        doc_ref = r.get("doc_ref")
        if doc_ref:
            try:
                res = await _call_mcp_tool(
                    docs_server, "get_doc_section", {"doc_ref": doc_ref}
                )
                if res.get("content"):
                    doc_sections.append(
                        f"### {res.get('title', doc_ref)}\n{res['content']}"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Doc section lookup failed", doc_ref=doc_ref, error=str(exc)
                )
        if not doc_sections:
            try:
                res = await _call_mcp_tool(
                    docs_server,
                    "search_corpus",
                    {"query": query, "target_library": target_library, "top_k": 2},
                )
                for c in res.get("results", []):
                    doc_sections.append(
                        f"### {c.get('title', 'Doc Chunk')}\n{c.get('content', '')}"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("Docs query failed during LLM fallback", error=str(e))

    doc_context = "\n\n".join(doc_sections)

    prompt = (
        f"You are migrating the Python file '{file_path}' to '{target_library}'.\n"
        f"The following patterns require migration and do not have an automated rule:\n"
        f"{json.dumps(unmatched, indent=2)}\n\n"
        f"Relevant migration documentation:\n{doc_context}\n\n"
        f"Current file source code:\n```python\n{current_content}\n```\n\n"
        f"Please rewrite the file to complete the migration to {target_library}. "
        f"Preserve all other logic, docstrings, and formatting. Return ONLY the complete updated Python code."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Python codebase migration engineer. "
                "Return ONLY python code in ```python ... ``` fences."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    llm_client = state.get("llm_client")
    rewritten_code_raw = await _call_llm(
        model=model, messages=messages, llm_client=llm_client
    )
    rewritten_code = _clean_code_fence(rewritten_code_raw)

    if rewritten_code and rewritten_code != current_content:
        abs_path.write_text(rewritten_code, encoding="utf-8")
        current_content = rewritten_code

    return {"current_content": current_content}


async def run_tests(state: FileSubgraphState) -> dict[str, Any]:
    """Run scoped pytest targeting the modified module in the Docker Sandbox."""
    file_path = state["file_path"]
    workspace_path = state["workspace_path"]
    sandbox_manager = state.get("sandbox_manager")
    if sandbox_manager is None:
        sandbox_manager = SandboxManager()

    sandbox_container = state.get("sandbox_container")
    test_target = _find_test_file(file_path, workspace_path)

    logger.info(
        "Running scoped tests in sandbox", file=file_path, test_target=test_target
    )

    run_fn = sandbox_manager.run_tests
    if inspect.iscoroutinefunction(run_fn):
        test_res = await run_fn(container=sandbox_container, module_path=test_target)
    else:
        res = run_fn(container=sandbox_container, module_path=test_target)
        if inspect.iscoroutine(res):
            test_res = await res
        else:
            test_res = res

    passed = getattr(test_res, "passed", False)
    exit_code = getattr(test_res, "exit_code", 1 if not passed else 0)
    output = getattr(test_res, "output", "")
    traceback_val = getattr(test_res, "traceback", "") or ""

    return {
        "test_result": test_res,
        "passed": passed,
        "exit_code": exit_code,
        "output": output,
        "traceback": traceback_val,
    }


def route_after_test(state: FileSubgraphState) -> str:
    """Conditional router after run_tests: loop to self-healing or finalize."""
    if state.get("passed"):
        return "finalize"

    max_attempts = state.get("max_healing_attempts", 3)
    attempts = state.get("healing_attempts", 0)

    if attempts < max_attempts:
        return "extract_traceback"
    return "finalize"


async def extract_traceback(state: FileSubgraphState) -> dict[str, Any]:
    """Extract structured traceback from pytest failure for documentation lookup."""
    tb = state.get("traceback") or ""
    if not tb:
        test_res = state.get("test_result")
        if test_res:
            tb = getattr(test_res, "traceback", "")
            if not tb:
                out = getattr(test_res, "output", "") or getattr(test_res, "stdout", "")
                if "=== FAILURES ===" in out:
                    tb = "=== FAILURES ===" + out.split("=== FAILURES ===", 1)[1]
                else:
                    tb = out
    if not tb:
        tb = "Test run failed with non-zero exit code but no traceback captured."

    logger.info(
        "Extracted traceback for healing",
        file=state.get("file_path"),
        traceback_preview=tb.splitlines()[-1] if tb.splitlines() else "",
    )
    return {"traceback": tb}


async def query_docs(state: FileSubgraphState) -> dict[str, Any]:
    """Query mcp-server-docs with the error traceback to retrieve relevant docs."""
    tb = state.get("traceback", "")
    target_library = state.get("target_library", "pydantic")
    docs_server = state.get("docs_server")
    if docs_server is None:
        docs_server = create_docs_server()

    search_query = _extract_query_from_traceback(tb)
    logger.info(
        "Querying docs for self-healing traceback",
        query=search_query,
        target_library=target_library,
    )

    doc_context = ""
    try:
        res = await _call_mcp_tool(
            docs_server,
            "search_corpus",
            {"query": search_query, "target_library": target_library, "top_k": 3},
        )
        chunks = res.get("results", [])
        if chunks:
            parts = [
                f"### {c.get('title', 'Doc Section')}\n{c.get('content', '')}"
                for c in chunks
            ]
            doc_context = "\n\n".join(parts)
    except Exception as e:  # noqa: BLE001
        logger.warning("Docs search failed during self-healing", error=str(e))

    if not doc_context:
        try:
            web_res = await _call_mcp_tool(
                docs_server,
                "web_search",
                {
                    "query": search_query,
                    "target_library": target_library,
                    "max_tokens": 1000,
                },
            )
            chunks = web_res.get("chunks", [])
            if chunks:
                parts = [
                    f"### {c.get('title', 'Web Doc')}\n{c.get('content', '')}"
                    for c in chunks
                ]
                doc_context = "\n\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Web search fallback failed during self-healing", error=str(exc)
            )

    return {"doc_context": doc_context}


async def patch_code(state: FileSubgraphState) -> dict[str, Any]:
    """Use LiteLLM with Gemini Flash to generate a fix from traceback + doc context."""
    attempts = state.get("healing_attempts", 0) + 1
    file_path = state["file_path"]
    workspace_path = state["workspace_path"]
    abs_path = _resolve_file_path(workspace_path, file_path)
    current_content = state.get("current_content") or abs_path.read_text(
        encoding="utf-8"
    )
    traceback_val = state.get("traceback", "")
    doc_context = state.get("doc_context", "")
    target_library = state.get("target_library", "target library")

    # Healing reasoning uses the default capable reasoning model
    config = load_config()
    model = config.model_default

    logger.info(
        "Self-Healing attempt",
        attempt=attempts,
        max_attempts=state.get("max_healing_attempts", 3),
        file=file_path,
        model=model,
    )

    prompt = (
        f"You are an expert autonomous software engineer fixing a regression caused by migrating to {target_library}.\n"
        f"File: {file_path}\n\n"
        f"Test Failure Traceback:\n```\n{traceback_val}\n```\n\n"
        f"Relevant Documentation:\n{doc_context}\n\n"
        f"Current File Source Code:\n```python\n{current_content}\n```\n\n"
        f"Fix the error so tests pass. Provide the complete updated Python file content. "
        f"Preserve all existing functionality, structure, comments, and imports. "
        f"Return ONLY Python code in ```python ... ``` fences."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an autonomous self-healing migration agent. "
                "Return ONLY python code in ```python ... ``` fences."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    llm_client = state.get("llm_client")
    patched_raw = await _call_llm(model=model, messages=messages, llm_client=llm_client)
    patched_code = _clean_code_fence(patched_raw)

    if patched_code:
        abs_path.write_text(patched_code, encoding="utf-8")
        current_content = patched_code

    return {
        "healing_attempts": attempts,
        "current_content": current_content,
    }


async def finalize(state: FileSubgraphState) -> dict[str, Any]:
    """Finalize file processing and construct the FileResult."""
    passed = state.get("passed", False)
    file_path = state["file_path"]
    attempts = state.get("healing_attempts", 0)

    if passed:
        status = FileStatus.SUCCESS
        diff_text = _compute_unified_diff(
            original=state.get("original_content", ""),
            current=state.get("current_content", ""),
            file_path=file_path,
        )
        traceback_text = ""
    else:
        status = FileStatus.FAILED
        diff_text = ""
        traceback_text = state.get("traceback") or ""

    file_result = FileResult(
        file_path=file_path,
        status=status,
        diff=diff_text,
        traceback=traceback_text,
        attempt_count=attempts,
    )

    logger.info(
        "File Sub-graph finalized",
        file=file_path,
        status=status.value,
        attempts=attempts,
        diff_length=len(diff_text),
    )

    return {
        "status": status.value,
        "diff": diff_text,
        "traceback": traceback_text,
        "attempt_count": attempts,
        "file_result": file_result.model_dump(),
    }


# ── Graph Assembly ─────────────────────────────────────────────────────


def build_file_subgraph() -> StateGraph:
    """Construct the uncompiled File Sub-graph StateGraph."""
    builder = StateGraph(FileSubgraphState)

    builder.add_node("apply_rules", apply_rules)
    builder.add_node("llm_fallback", llm_fallback)
    builder.add_node("run_tests", run_tests)
    builder.add_node("extract_traceback", extract_traceback)
    builder.add_node("query_docs", query_docs)
    builder.add_node("patch_code", patch_code)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "apply_rules")

    builder.add_conditional_edges(
        "apply_rules",
        route_after_apply,
        {
            "llm_fallback": "llm_fallback",
            "run_tests": "run_tests",
        },
    )
    builder.add_edge("llm_fallback", "run_tests")

    builder.add_conditional_edges(
        "run_tests",
        route_after_test,
        {
            "finalize": "finalize",
            "extract_traceback": "extract_traceback",
        },
    )

    builder.add_edge("extract_traceback", "query_docs")
    builder.add_edge("query_docs", "patch_code")
    builder.add_edge("patch_code", "run_tests")

    builder.add_edge("finalize", END)

    return builder


def compile_file_subgraph(checkpointer: Any | None = None) -> Any:
    """Compile the File Sub-graph with optional checkpointer."""
    builder = build_file_subgraph()
    return builder.compile(checkpointer=checkpointer)


async def run_file_subgraph(
    file_path: str,
    workspace_path: str,
    target_library: str,
    matched_rules: list[Any] | None = None,
    risk: RiskLevel | str = RiskLevel.LOW,
    sandbox_manager: Any = None,
    sandbox_container: Any = None,
    ast_server: Any = None,
    docs_server: Any = None,
    llm_client: Any = None,
    max_healing_attempts: int = 3,
    checkpointer: Any = None,
) -> FileResult:
    """Execute the File Sub-graph on a single file and return its FileResult.

    Maintains episodic context isolation: all internal state, attempts,
    tracebacks, and doc queries are contained to this file execution.
    """
    initial_state: FileSubgraphState = {
        "file_path": file_path,
        "workspace_path": str(workspace_path),
        "target_library": target_library,
        "matched_rules": _normalize_matched_rules(matched_rules),
        "risk": risk,
        "healing_attempts": 0,
        "max_healing_attempts": max_healing_attempts,
    }
    if sandbox_manager is not None:
        initial_state["sandbox_manager"] = sandbox_manager
    if sandbox_container is not None:
        initial_state["sandbox_container"] = sandbox_container
    if ast_server is not None:
        initial_state["ast_server"] = ast_server
    if docs_server is not None:
        initial_state["docs_server"] = docs_server
    if llm_client is not None:
        initial_state["llm_client"] = llm_client

    compiled = compile_file_subgraph(checkpointer=checkpointer)
    final_state = await compiled.ainvoke(initial_state)

    res_data = final_state.get("file_result")
    if isinstance(res_data, dict):
        return FileResult(**res_data)
    if isinstance(res_data, FileResult):
        return res_data

    return FileResult(
        file_path=file_path,
        status=FileStatus(final_state.get("status", "FAILED")),
        diff=final_state.get("diff", ""),
        traceback=final_state.get("traceback", ""),
        attempt_count=final_state.get("attempt_count", 0),
    )
