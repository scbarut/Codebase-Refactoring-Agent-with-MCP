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

import asyncio
import contextvars
import difflib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

_current_sandbox_container: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_sandbox_container", default=None
)
_current_sandbox_manager: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_sandbox_manager", default=None
)

from src.core.config import Settings, load_config
from src.core.logging import get_logger
from src.core.mcp_client import call_mcp_tool
from src.core.models import FileResult, FileStatus, MatchedRule, RiskLevel
from src.core.sandbox import SandboxManager

logger = get_logger(__name__)

SYNTHETIC_TEST_PREFIX: str = "_tmp_smoke_test_"


def _get_ast_server():
    from src.mcp_servers.ast_server import create_ast_server

    return create_ast_server()


def _get_docs_server():
    from src.mcp_servers.docs_server import create_docs_server

    return create_docs_server()


def _prune_unused_imports(file_path: Path) -> None:
    """Safely remove unused imports from the modified Python file using ruff.

    Supports all migration target libraries (pydantic, celery, sqlalchemy, requests, etc.).
    Preserves __init__.py files where imports are commonly re-exported.
    """
    if file_path.name == "__init__.py" or not file_path.is_file():
        return
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--select",
                "F401",
                "--fix",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Automatic unused import pruning skipped or failed",
            path=str(file_path),
            error=str(exc),
        )


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

    # Synthetic Test state (ADR-0007)
    test_file_path: str | None
    is_synthetic_test: bool
    persist_synthetic_test: bool
    test_healing_attempts: int
    test_content: str | None

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
    """Strip markdown code block fences (```python ... ```) from LLM output, including any conversational preamble."""
    text = text.strip()
    match = re.search(r"```(?:python)?\s*\n([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
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
    if ws.is_dir():
        for found in ws.rglob(test_name):
            rel_parts = found.relative_to(ws).parts
            if not any(
                part in {".venv", "venv", "__pycache__", ".git"} for part in rel_parts
            ):
                return str(found.relative_to(ws)).replace("\\", "/")

    # 3. If file_path itself is a test file
    is_test_file = (
        stem.startswith("test_")
        or stem.endswith("_test")
        or "tests" in Path(file_path).parts
    )
    if is_test_file and (ws / file_path).exists():
        return str(file_path).replace("\\", "/")

    # 4. Search test files that import this module
    if ws.is_dir():
        ignored = {".venv", "venv", "__pycache__", ".git"}
        candidate_test_files: list[Path] = []
        tests_dir = ws / "tests"
        if tests_dir.is_dir():
            for candidate in sorted(tests_dir.rglob("*.py")):
                try:
                    rel = candidate.relative_to(ws)
                    if not any(p in ignored for p in rel.parts):
                        candidate_test_files.append(candidate)
                except ValueError:
                    continue
        for candidate in sorted(ws.rglob("*.py")):
            if (
                candidate.name.startswith("test_")
                or candidate.name.endswith("_test.py")
            ):
                try:
                    rel = candidate.relative_to(ws)
                    if not any(p in ignored for p in rel.parts) and candidate not in candidate_test_files:
                        candidate_test_files.append(candidate)
                except ValueError:
                    continue

        pattern = re.compile(
            rf"\b(from\s+[\w.]*{re.escape(stem)}\s+import|import\s+[\w.]*{re.escape(stem)}\b)"
        )
        for test_file in candidate_test_files:
            try:
                content = test_file.read_text(encoding="utf-8", errors="replace")
                if pattern.search(content):
                    return str(test_file.relative_to(ws)).replace("\\", "/")
            except OSError:
                continue

    return None


def _disambiguate_failure(traceback: str, test_file_path: str, source_file_path: str) -> str:
    """Disambiguate whether test failure was in the test fixture/caller frame or source module.

    Returns:
        "test_frame" if the failure frame is in the synthetic test file without entering the source module.
        "source_frame" if the exception occurred inside the migrated source module.
    """
    if not traceback:
        return "source_frame"

    source_stem = Path(source_file_path).name
    test_stem = Path(test_file_path).name

    # Check if the traceback enters the source module
    enters_source = any(
        f'File "{source_stem}"' in line
        or f"/{source_stem}" in line
        or f"\\{source_stem}" in line
        or f'"{source_file_path}"' in line
        for line in traceback.splitlines()
    )
    if enters_source:
        return "source_frame"

    # Check if failure occurs in test file frame
    has_test_frame = any(
        f'File "{test_stem}"' in line
        or f"/{test_stem}" in line
        or f"\\{test_stem}" in line
        or f'"{test_file_path}"' in line
        for line in traceback.splitlines()
    )
    if has_test_frame:
        return "test_frame"

    return "source_frame"



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


# Re-export shared helper for backward compatibility and internal graph calls
_call_mcp_tool = call_mcp_tool


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
        "num_retries": 3,
    }

    # Pass explicit Gemini API key if using Gemini provider; otherwise LiteLLM auto-detects from environment
    if model.startswith("gemini") and (
        config.gemini_api_key or os.environ.get("GEMINI_API_KEY")
    ):
        kwargs["api_key"] = config.gemini_api_key or os.environ.get("GEMINI_API_KEY")

    # Configure fallback models solely from config.yaml
    fallbacks: list[str] = []
    if config.model_default and model != config.model_default:
        fallbacks.append(config.model_default)
    elif config.model_lite and config.model_lite != model:
        fallbacks.append(config.model_lite)

    if fallbacks:
        kwargs["fallbacks"] = fallbacks

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = await litellm.acompletion(**kwargs)
            return response.choices[0].message.content or ""
        except (
            litellm.ServiceUnavailableError,
            litellm.RateLimitError,
            litellm.APIConnectionError,
            litellm.Timeout,
        ) as err:
            if attempt == max_attempts:
                logger.error(
                    "LLM call failed after retries",
                    error=str(err),
                    model=model,
                )
                raise
            backoff = 2 * attempt
            logger.warning(
                "Transient LLM error, retrying with backoff",
                model=model,
                attempt=attempt,
                backoff_seconds=backoff,
                error=str(err),
            )
            await asyncio.sleep(backoff)

    return ""


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
        ast_server = _get_ast_server()

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

    _prune_unused_imports(abs_path)
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
        docs_server = _get_docs_server()

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
        rule_id = r.get("rule_id", "")
        if rule_id == "unmatched-general" or not (r.get("old_qualified_name") or r.get("description")):
            query = f"{target_library} migration guide"
        else:
            query = (
                r.get("old_qualified_name")
                or r.get("description")
                or rule_id
                or target_library
            )
        doc_ref = r.get("doc_ref")
        if doc_ref:
            try:
                res = await _call_mcp_tool(
                    docs_server, "lookup_doc_ref", {"doc_ref": doc_ref}
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

        # If file has no matched rules or local corpus yielded no sections, fetch from external source
        if not doc_sections or not state.get("matched_rules"):
            try:
                search_term = f"{target_library} migration {query}".strip()
                web_res = await _call_mcp_tool(
                    docs_server,
                    "web_search",
                    {
                        "query": search_term,
                        "target_library": target_library,
                        "max_tokens": 1000,
                    },
                )
                web_chunks = web_res.get("chunks", [])
                answer = web_res.get("answer")
                if answer and not any("Tavily Migration Synthesis" in c.get("title", "") for c in web_chunks):
                    doc_sections.append(f"### Tavily AI Synthesis\n{answer}")
                for c in web_chunks:
                    doc_sections.append(
                        f"### {c.get('title', 'Web Doc')}\n{c.get('content', '')}"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Web search fallback failed during LLM fallback", error=str(exc)
                )

    doc_context = "\n\n".join(doc_sections)

    prompt = (
        f"You are migrating the Python file '{file_path}' to '{target_library}'.\n"
        f"The following patterns require migration and do not have an automated rule:\n"
        f"{json.dumps(unmatched, indent=2)}\n\n"
        f"Relevant migration documentation:\n{doc_context}\n\n"
        f"Current file source code:\n```python\n{current_content}\n```\n\n"
        f"Please rewrite the file to complete the migration to {target_library}. "
        f"Preserve all other logic, docstrings, and formatting. "
        f"Do not introduce unused imports; only import symbols that are actively used. "
        f"Return ONLY the complete updated Python code."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Python codebase migration engineer. "
                "Return ONLY python code in ```python ... ``` fences. "
                "Do not include unused imports."
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
        _prune_unused_imports(abs_path)
        try:
            current_content = abs_path.read_text(encoding="utf-8")
        except OSError:
            current_content = rewritten_code

    return {"current_content": current_content}


async def generate_synthetic_test(state: FileSubgraphState) -> dict[str, Any]:
    """Ensure a test target exists: use existing test file or synthesize an AST-grounded smoke test."""
    file_path = state["file_path"]
    workspace_path = state["workspace_path"]
    target_library = state.get("target_library", "target library")

    existing_test = _find_test_file(file_path, workspace_path)
    if existing_test:
        return {
            "test_file_path": existing_test,
            "is_synthetic_test": False,
        }

    # No existing test file: synthesize an AST-grounded smoke test (ADR-0007)
    stem = Path(file_path).stem
    test_file_name = f"{SYNTHETIC_TEST_PREFIX}{stem}.py"
    abs_test_path = _resolve_file_path(workspace_path, test_file_name)
    abs_source_path = _resolve_file_path(workspace_path, file_path)

    ast_server = state.get("ast_server")
    if ast_server is None:
        ast_server = _get_ast_server()

    try:
        sig_data = await call_mcp_tool(
            ast_server,
            "extract_signatures",
            {"file_path": str(abs_source_path).replace("\\", "/")},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Signature extraction failed for synthetic test", error=str(exc))
        sig_data = {"classes": [], "functions": []}

    current_content = state.get("current_content")
    if not current_content and abs_source_path.is_file():
        current_content = abs_source_path.read_text(encoding="utf-8")
    current_content = current_content or ""

    config = load_config()
    model = route_model(state.get("risk"), config=config)

    prompt = (
        f"You are generating a minimal, robust pytest smoke and contract test for the Python file '{file_path}' "
        f"which has been migrated to '{target_library}'.\n\n"
        f"AST Signatures in this file:\n{json.dumps(sig_data, indent=2)}\n\n"
        f"Migrated File Content:\n```python\n{current_content}\n```\n\n"
        f"Requirements:\n"
        f"1. Import the module or symbols from '{stem}'.\n"
        f"2. Write 1-3 simple test functions (e.g. test_smoke_{stem}) that instantiate classes with mock/default values and execute basic methods.\n"
        f"3. Mock or patch any external network, database, cloud, or LLM clients (e.g., pymongo/MongoClient, redis/Redis, openai, httpx, requests) at module or test scope using unittest.mock (MagicMock, patch) so import-time and instantiation-time connections do not fail or hang in an offline sandbox.\n"
        f"4. The goal is to verify that the module compiles, imports cleanly, and runs basic logic without syntax or initialization errors.\n"
        f"5. Return ONLY executable python test code in ```python ... ``` fences."
    )

    messages = [
        {
            "role": "system",
            "content": "You are an expert test engineer writing minimal, safe pytest smoke tests. Return ONLY python code in ```python ... ``` fences.",
        },
        {"role": "user", "content": prompt},
    ]

    llm_client = state.get("llm_client")
    test_code_raw = await _call_llm(model=model, messages=messages, llm_client=llm_client)
    test_code = _clean_code_fence(test_code_raw)

    if test_code:
        abs_test_path.write_text(test_code, encoding="utf-8")

    logger.info(
        "Synthesized smoke test for uncovered file",
        file=file_path,
        synthetic_test=test_file_name,
    )

    return {
        "test_file_path": test_file_name,
        "is_synthetic_test": True,
        "test_content": test_code,
        "test_healing_attempts": 0,
    }


async def run_tests(state: FileSubgraphState) -> dict[str, Any]:
    """Run scoped pytest targeting the modified module in the Docker Sandbox."""
    file_path = state["file_path"]
    workspace_path = state["workspace_path"]
    sandbox_manager = state.get("sandbox_manager") or _current_sandbox_manager.get()
    if sandbox_manager is None:
        sandbox_manager = SandboxManager()

    sandbox_container = (
        state.get("sandbox_container") or _current_sandbox_container.get()
    )
    test_target = state.get("test_file_path") or _find_test_file(file_path, workspace_path)

    if sandbox_container is None:
        logger.info(
            "No sandbox container provided; skipping test execution",
            file=file_path,
        )
        return {
            "test_result": None,
            "passed": True,
            "exit_code": 0,
            "output": "No sandbox container provided; tests skipped.",
            "traceback": "",
        }

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

    # Pytest exit code 5 means NO_TESTS_COLLECTED. If no scoped tests were collected,
    # and no syntax/runtime traceback occurred, this is not a test failure.
    if exit_code == 5 and not traceback_val:
        passed = True
        exit_code = 0
        if not output or "no tests ran" in output:
            output = "No scoped tests collected for module; verified."

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

    # If synthetic test failed, check if failure is in test frame before source was entered
    if state.get("is_synthetic_test"):
        test_attempts = state.get("test_healing_attempts", 0)
        traceback_val = state.get("traceback", "")
        test_file = state.get("test_file_path", "")
        source_file = state.get("file_path", "")
        failure_frame = _disambiguate_failure(traceback_val, test_file, source_file)
        if failure_frame == "test_frame" and test_attempts < 1:
            return "heal_synthetic_test"

    max_attempts = state.get("max_healing_attempts", 3)
    attempts = state.get("healing_attempts", 0)

    if attempts < max_attempts:
        return "extract_traceback"
    return "finalize"


async def heal_synthetic_test(state: FileSubgraphState) -> dict[str, Any]:
    """Heal an invalid synthetic test fixture when the error is in the caller frame."""
    test_healing_attempts = state.get("test_healing_attempts", 0) + 1
    file_path = state["file_path"]
    workspace_path = state["workspace_path"]
    test_file_path = state.get("test_file_path")
    if not test_file_path:
        test_file_path = f"{SYNTHETIC_TEST_PREFIX}{Path(file_path).stem}.py"

    abs_test_path = _resolve_file_path(workspace_path, test_file_path)
    abs_source_path = _resolve_file_path(workspace_path, file_path)

    test_content = state.get("test_content")
    if not test_content and abs_test_path.is_file():
        test_content = abs_test_path.read_text(encoding="utf-8")
    test_content = test_content or ""

    source_content = state.get("current_content")
    if not source_content and abs_source_path.is_file():
        source_content = abs_source_path.read_text(encoding="utf-8")
    source_content = source_content or ""

    traceback_val = state.get("traceback", "")
    target_library = state.get("target_library", "target library")

    config = load_config()
    model = route_model(state.get("risk"), config=config)

    logger.info(
        "Healing synthetic smoke test fixture",
        file=file_path,
        test_file=test_file_path,
        attempt=test_healing_attempts,
    )

    prompt = (
        f"You are fixing an invalid synthetic pytest smoke test for the Python file '{file_path}' "
        f"which has been migrated to '{target_library}'.\n\n"
        f"The test failed because the test fixture made an invalid call, wrong argument, or failed import.\n\n"
        f"Test Failure Traceback:\n```\n{traceback_val}\n```\n\n"
        f"Current Test Code:\n```python\n{test_content}\n```\n\n"
        f"Source File Code:\n```python\n{source_content}\n```\n\n"
        f"Requirements:\n"
        f"1. Fix the test fixture so that it correctly imports and instantiates the classes or functions from '{Path(file_path).stem}'.\n"
        f"2. Keep the test minimal and safe. Do not test complex business logic.\n"
        f"3. Mock or patch any external database, network, or third-party service connections (e.g. MongoClient, Redis, API clients) with unittest.mock if the test failed due to connection or missing service errors.\n"
        f"4. Return ONLY valid, executable Python pytest code in ```python ... ``` fences."
    )

    messages = [
        {
            "role": "system",
            "content": "You are an expert test engineer fixing an invalid pytest smoke test fixture. Return ONLY python code in ```python ... ``` fences.",
        },
        {"role": "user", "content": prompt},
    ]

    llm_client = state.get("llm_client")
    healed_raw = await _call_llm(model=model, messages=messages, llm_client=llm_client)
    healed_code = _clean_code_fence(healed_raw)

    if healed_code:
        abs_test_path.write_text(healed_code, encoding="utf-8")
        test_content = healed_code

    return {
        "test_healing_attempts": test_healing_attempts,
        "test_content": test_content,
    }


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
        docs_server = _get_docs_server()

    search_query = _extract_query_from_traceback(tb)
    logger.info(
        "Querying docs for self-healing traceback",
        query=search_query,
        target_library=target_library,
    )

    doc_context = ""
    chunks: list[dict[str, Any]] = []
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

    has_matched_rules = bool(state.get("matched_rules"))
    # When a file has no matched rules, or local corpus returns no results,
    # or the highest match score is low (weak stopword match), fetch from the external source via web_search
    top_score = max((c.get("score", 0.0) for c in chunks), default=0.0)
    needs_external = not doc_context or not has_matched_rules or top_score < 3.5

    if needs_external:
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
            web_chunks = web_res.get("chunks", [])
            answer = web_res.get("answer")
            web_parts = []
            if answer and not any("Tavily Migration Synthesis" in c.get("title", "") for c in web_chunks):
                web_parts.append(f"### Tavily AI Synthesis\n{answer}")
            for c in web_chunks:
                web_parts.append(f"### {c.get('title', 'Web Doc')}\n{c.get('content', '')}")
            if web_parts:
                web_context = "\n\n".join(web_parts)
                if not doc_context or not has_matched_rules:
                    # When no matched rules, external source documentation is the primary reference
                    doc_context = f"{web_context}\n\n{doc_context}".strip()
                else:
                    doc_context = f"{doc_context}\n\n{web_context}".strip()
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

    try:
        from src.core.graph import _dispatch_progress_callback

        cb = _dispatch_progress_callback.get()
        if cb is not None:
            cb({
                "type": "healing_attempt",
                "file_path": file_path,
                "attempt": attempts,
                "max_attempts": state.get("max_healing_attempts", 3),
                "message": f"Self-healing attempt {attempts}/3 for {file_path}",
            })
    except Exception:  # noqa: BLE001, S110
        pass

    prompt = (
        f"You are an expert autonomous software engineer fixing a regression caused by migrating to {target_library}.\n"
        f"File: {file_path}\n\n"
        f"Test Failure Traceback:\n```\n{traceback_val}\n```\n\n"
        f"Relevant Documentation:\n{doc_context}\n\n"
        f"Current File Source Code:\n```python\n{current_content}\n```\n\n"
        f"Fix the error so tests pass. Provide the complete updated Python file content. "
        f"Preserve all existing functionality, structure, comments, and imports. "
        f"Do not introduce unused imports; only import symbols that are actively used. "
        f"Return ONLY Python code in ```python ... ``` fences."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an autonomous self-healing migration agent. "
                "Return ONLY python code in ```python ... ``` fences. "
                "Do not include unused imports."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    llm_client = state.get("llm_client")
    patched_raw = await _call_llm(model=model, messages=messages, llm_client=llm_client)
    patched_code = _clean_code_fence(patched_raw)

    if patched_code:
        abs_path.write_text(patched_code, encoding="utf-8")
        _prune_unused_imports(abs_path)
        try:
            current_content = abs_path.read_text(encoding="utf-8")
        except OSError:
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
    workspace_path = state.get("workspace_path")

    # Clean up or persist ephemeral synthetic tests (ADR-0007)
    if state.get("is_synthetic_test") and state.get("test_file_path") and workspace_path:
        test_file_path = state["test_file_path"]
        abs_test_path = _resolve_file_path(workspace_path, test_file_path)
        persist = state.get("persist_synthetic_test", False)
        if persist and abs_test_path.is_file():
            tests_dir = Path(workspace_path) / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(file_path).stem
            target_path = tests_dir / f"test_{stem}_synthetic.py"
            abs_test_path.rename(target_path)
            logger.info("Persisted synthetic test", source=str(abs_test_path), target=str(target_path))
        elif abs_test_path.is_file():
            try:
                abs_test_path.unlink()
                logger.info("Removed ephemeral synthetic test", file=str(abs_test_path))
            except OSError as exc:
                logger.warning("Failed to clean up ephemeral synthetic test", file=str(abs_test_path), error=str(exc))

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
    builder.add_node("generate_synthetic_test", generate_synthetic_test)
    builder.add_node("run_tests", run_tests)
    builder.add_node("heal_synthetic_test", heal_synthetic_test)
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
            "run_tests": "generate_synthetic_test",
        },
    )
    builder.add_edge("llm_fallback", "generate_synthetic_test")
    builder.add_edge("generate_synthetic_test", "run_tests")

    builder.add_conditional_edges(
        "run_tests",
        route_after_test,
        {
            "finalize": "finalize",
            "extract_traceback": "extract_traceback",
            "heal_synthetic_test": "heal_synthetic_test",
        },
    )
    builder.add_edge("heal_synthetic_test", "run_tests")

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
    original_content: str | None = None,
    persist_synthetic_test: bool = False,
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
        "persist_synthetic_test": persist_synthetic_test,
    }
    if original_content is not None:
        initial_state["original_content"] = original_content
    if ast_server is not None:
        initial_state["ast_server"] = ast_server
    if docs_server is not None:
        initial_state["docs_server"] = docs_server
    if llm_client is not None:
        initial_state["llm_client"] = llm_client

    token_container = _current_sandbox_container.set(sandbox_container)
    token_manager = _current_sandbox_manager.set(sandbox_manager)
    try:
        compiled = compile_file_subgraph(checkpointer=checkpointer)
        final_state = await compiled.ainvoke(initial_state)
    finally:
        _current_sandbox_container.reset(token_container)
        _current_sandbox_manager.reset(token_manager)

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
