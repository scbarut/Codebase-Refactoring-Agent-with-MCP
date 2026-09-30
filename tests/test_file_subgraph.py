"""Tests for the File Sub-graph: rewrite, test, Self-Healing Loop.

Verifies:
1. LangGraph StateGraph structure and node definitions.
2. Tiered model routing (LOW -> gemini-flash-lite, MEDIUM/HIGH/unmatched -> gemini-flash).
3. Declarative rule application via mcp-server-ast (with CST transformers).
4. LLM fallback for unmatched patterns using mcp-server-docs context.
5. Scoped test execution in SandboxManager.
6. Self-Healing Loop (extract traceback -> query docs -> patch code -> re-test).
7. Successful rewrite with passing tests (0 healing attempts, diff returned).
8. Self-healing that fixes a test failure (attempt count = 1, SUCCESS).
9. Failure after 3 failed attempts (capped at 3, FAILED status with traceback).
10. Episodic context isolation across sequential file executions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.graph import StateGraph

from src.core.config import Settings
from src.core.file_subgraph import (
    FileSubgraphState,
    _clean_code_fence,
    _compute_unified_diff,
    _extract_query_from_traceback,
    _find_test_file,
    _normalize_matched_rules,
    _resolve_file_path,
    apply_rules,
    build_file_subgraph,
    compile_file_subgraph,
    finalize,
    llm_fallback,
    patch_code,
    query_docs,
    route_after_apply,
    route_after_test,
    route_model,
    run_file_subgraph,
    run_tests,
)
from src.core.models import FileStatus, MatchedRule, RiskLevel
from src.core.sandbox import TestResult

# ── Fixtures & Helpers ─────────────────────────────────────────────────


@pytest.fixture
def mock_config() -> Settings:
    return Settings(
        model_lite="gemini/gemini-2.5-flash-lite",
        model_default="gemini/gemini-2.5-flash",
        max_healing_attempts=3,
    )


def _setup_workspace_file(
    tmp_path: Path, filename: str, content: str
) -> tuple[Path, str]:
    file_path = tmp_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return tmp_path, filename


# ── 1. Model Routing Tests (ADR-0001) ──────────────────────────────────


def test_route_model_low_risk(mock_config: Settings) -> None:
    assert route_model(RiskLevel.LOW, config=mock_config) == mock_config.model_lite
    assert route_model("LOW", config=mock_config) == mock_config.model_lite
    assert route_model("low", config=mock_config) == mock_config.model_lite


def test_route_model_medium_risk(mock_config: Settings) -> None:
    assert (
        route_model(RiskLevel.MEDIUM, config=mock_config) == mock_config.model_default
    )
    assert route_model("MEDIUM", config=mock_config) == mock_config.model_default


def test_route_model_high_risk(mock_config: Settings) -> None:
    assert route_model(RiskLevel.HIGH, config=mock_config) == mock_config.model_default
    assert route_model("HIGH", config=mock_config) == mock_config.model_default


def test_route_model_unmatched_or_none(mock_config: Settings) -> None:
    assert route_model(None, config=mock_config) == mock_config.model_default


# ── 2. Utility & Helper Unit Tests ─────────────────────────────────────


def test_clean_code_fence() -> None:
    fenced = "```python\nimport pydantic\n```"
    assert _clean_code_fence(fenced) == "import pydantic"

    fenced_no_lang = "```\nx = 1\n```"
    assert _clean_code_fence(fenced_no_lang) == "x = 1"

    plain = "def foo(): pass"
    assert _clean_code_fence(plain) == "def foo(): pass"


def test_compute_unified_diff() -> None:
    orig = "a = 1\nb = 2\n"
    curr = "a = 1\nb = 3\n"
    diff = _compute_unified_diff(orig, curr, "models.py")
    assert "-b = 2" in diff
    assert "+b = 3" in diff
    assert "--- a/models.py" in diff
    assert "+++ b/models.py" in diff

    # No diff when identical
    assert _compute_unified_diff(orig, orig, "models.py") == ""


def test_find_test_file(tmp_path: Path) -> None:
    models_py = tmp_path / "models.py"
    models_py.write_text("class UserModel: pass", encoding="utf-8")

    # When tests/test_models.py exists
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_models = tests_dir / "test_models.py"
    test_models.write_text("def test_user(): pass", encoding="utf-8")

    found = _find_test_file("models.py", str(tmp_path))
    assert found == "tests/test_models.py"

    # When no test file exists
    assert _find_test_file("nonexistent.py", str(tmp_path)) is None


def test_extract_query_from_traceback() -> None:
    tb = (
        "Traceback (most recent call last):\n"
        "  File 'test_models.py', line 10, in test_foo\n"
        "    user.dict()\n"
        "E   AttributeError: 'UserModel' object has no attribute 'dict'\n"
    )
    query = _extract_query_from_traceback(tb)
    assert "AttributeError: 'UserModel' object has no attribute 'dict'" in query


def test_resolve_file_path(tmp_path: Path) -> None:
    resolved = _resolve_file_path(str(tmp_path), "foo/bar.py")
    assert resolved == (tmp_path / "foo/bar.py").resolve()


def test_normalize_matched_rules() -> None:
    # 1. Empty / None
    assert _normalize_matched_rules(None) == []
    assert _normalize_matched_rules([]) == []

    # 2. Dict input
    dict_rule = {
        "rule_id": "r1",
        "old_qualified_name": "old.api",
        "new_qualified_name": "new.api",
        "risk": "LOW",
        "transformer_class": None,
    }
    normalized = _normalize_matched_rules([dict_rule])
    assert len(normalized) == 1
    assert isinstance(normalized[0], MatchedRule)
    assert normalized[0].rule_id == "r1"
    assert normalized[0].risk == RiskLevel.LOW

    # 3. Already MatchedRule instance
    model_rule = MatchedRule(
        rule_id="r2",
        old_qualified_name="old.v",
        new_qualified_name="new.v",
        risk=RiskLevel.HIGH,
    )
    assert _normalize_matched_rules([model_rule])[0] is model_rule


# ── 3. Sub-graph Graph Structure Verification ──────────────────────────


def test_file_subgraph_nodes_and_edges() -> None:
    builder = build_file_subgraph()
    assert isinstance(builder, StateGraph)

    expected_nodes = {
        "apply_rules",
        "llm_fallback",
        "run_tests",
        "extract_traceback",
        "query_docs",
        "patch_code",
        "finalize",
    }
    assert expected_nodes.issubset(set(builder.nodes.keys()))

    compiled = compile_file_subgraph()
    assert compiled is not None


# ── 4. Node Level Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_rules_with_declarative_transformer(tmp_path: Path) -> None:
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "models.py",
        "from pydantic import BaseModel, validator\n\nclass M(BaseModel):\n    @validator('x')\n    def v(cls, val): return val\n",
    )

    ast_server = AsyncMock()
    ast_server.call_tool.return_value = MagicMock(
        content=[
            MagicMock(
                text='{"modified": true, "transformed_code": "transformed", "written_to_disk": true}'
            )
        ]
    )

    state: FileSubgraphState = {
        "file_path": rel_file,
        "workspace_path": str(ws),
        "target_library": "pydantic",
        "matched_rules": [
            MatchedRule(
                rule_id="validator-to-field-validator",
                old_qualified_name="pydantic.validator",
                new_qualified_name="pydantic.field_validator",
                risk=RiskLevel.MEDIUM,
                transformer_class="src.rules.pydantic_transformers.ValidatorToFieldValidatorTransformer",
            )
        ],
        "ast_server": ast_server,
    }

    res = await apply_rules(state)
    assert res["unmatched_rules"] == []
    assert res["original_content"] != ""
    assert ast_server.call_tool.called
    assert route_after_apply({**state, **res}) == "run_tests"


@pytest.mark.asyncio
async def test_apply_rules_flags_unmatched_rules(tmp_path: Path) -> None:
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "models.py",
        "class M: pass\n",
    )

    state: FileSubgraphState = {
        "file_path": rel_file,
        "workspace_path": str(ws),
        "target_library": "pydantic",
        "matched_rules": [
            MatchedRule(
                rule_id="custom-novel-rule",
                old_qualified_name="pydantic.custom_pattern",
                new_qualified_name="pydantic.new_pattern",
                risk=RiskLevel.HIGH,
                transformer_class=None,  # No declarative transformer!
            )
        ],
    }

    res = await apply_rules(state)
    assert len(res["unmatched_rules"]) == 1
    assert res["unmatched_rules"][0]["rule_id"] == "custom-novel-rule"
    assert route_after_apply({**state, **res}) == "llm_fallback"


@pytest.mark.asyncio
async def test_llm_fallback_node(tmp_path: Path) -> None:
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "models.py",
        "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n",
    )

    docs_server = AsyncMock()
    docs_server.call_tool.return_value = MagicMock(
        content=[
            MagicMock(
                text='{"results": [{"title": "Pydantic Migration", "content": "Use model_validate"}]}'
            )
        ]
    )

    mock_llm = AsyncMock()
    mock_llm.return_value = "```python\nfrom pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n    # migrated\n```"

    state: FileSubgraphState = {
        "file_path": rel_file,
        "workspace_path": str(ws),
        "target_library": "pydantic",
        "risk": RiskLevel.LOW,
        "unmatched_rules": [{"rule_id": "test-unmatched", "risk": "LOW"}],
        "docs_server": docs_server,
        "llm_client": mock_llm,
    }

    res = await llm_fallback(state)
    assert "# migrated" in res["current_content"]
    assert "# migrated" in (ws / rel_file).read_text(encoding="utf-8")
    assert mock_llm.called
    # Check that model routed to flash-lite for LOW risk
    assert "flash-lite" in mock_llm.call_args.kwargs["model"]


@pytest.mark.asyncio
async def test_run_tests_node_passing(tmp_path: Path) -> None:
    sandbox_mgr = MagicMock()
    sandbox_mgr.run_tests.return_value = TestResult(
        passed=True,
        exit_code=0,
        stdout="1 passed in 0.05s",
        traceback="",
        output="1 passed in 0.05s",
    )

    state: FileSubgraphState = {
        "file_path": "models.py",
        "workspace_path": str(tmp_path),
        "sandbox_manager": sandbox_mgr,
        "sandbox_container": MagicMock(),
    }

    res = await run_tests(state)
    assert res["passed"] is True
    assert res["exit_code"] == 0
    assert route_after_test({**state, **res}) == "finalize"


@pytest.mark.asyncio
async def test_run_tests_node_failing(tmp_path: Path) -> None:
    sandbox_mgr = MagicMock()
    sandbox_mgr.run_tests.return_value = TestResult(
        passed=False,
        exit_code=1,
        stdout="1 failed",
        traceback="E   AttributeError: no attribute 'dict'",
        output="1 failed",
    )

    state: FileSubgraphState = {
        "file_path": "models.py",
        "workspace_path": str(tmp_path),
        "sandbox_manager": sandbox_mgr,
        "sandbox_container": MagicMock(),
        "healing_attempts": 0,
        "max_healing_attempts": 3,
    }

    res = await run_tests(state)
    assert res["passed"] is False
    assert route_after_test({**state, **res}) == "extract_traceback"


@pytest.mark.asyncio
async def test_query_docs_and_patch_code(tmp_path: Path) -> None:
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "models.py",
        "class User:\n    def get_dict(self):\n        return self.dict()\n",
    )

    docs_server = AsyncMock()
    docs_server.call_tool.return_value = MagicMock(
        content=[
            MagicMock(
                text='{"results": [{"title": "BaseModel Methods", "content": "dict() is replaced by model_dump()"}]}'
            )
        ]
    )

    mock_llm = AsyncMock()
    mock_llm.return_value = "```python\nclass User:\n    def get_dict(self):\n        return self.model_dump()\n```"

    state: FileSubgraphState = {
        "file_path": rel_file,
        "workspace_path": str(ws),
        "target_library": "pydantic",
        "traceback": "E   AttributeError: 'User' object has no attribute 'dict'",
        "docs_server": docs_server,
        "llm_client": mock_llm,
        "healing_attempts": 0,
        "max_healing_attempts": 3,
    }

    q_res = await query_docs(state)
    assert "model_dump" in q_res["doc_context"]

    p_res = await patch_code({**state, **q_res})
    assert p_res["healing_attempts"] == 1
    assert "model_dump" in p_res["current_content"]
    assert "model_dump" in (ws / rel_file).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_finalize_node_success(tmp_path: Path) -> None:
    orig = "def foo(): return 1\n"
    curr = "def foo(): return 2\n"

    state: FileSubgraphState = {
        "file_path": "utils.py",
        "passed": True,
        "original_content": orig,
        "current_content": curr,
        "healing_attempts": 1,
    }

    res = await finalize(state)
    assert res["status"] == "SUCCESS"
    assert "+def foo(): return 2" in res["diff"]
    assert res["traceback"] == ""
    assert res["attempt_count"] == 1
    assert res["file_result"]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_finalize_node_failure() -> None:
    state: FileSubgraphState = {
        "file_path": "broken.py",
        "passed": False,
        "traceback": "Traceback: SyntaxError",
        "healing_attempts": 3,
    }

    res = await finalize(state)
    assert res["status"] == "FAILED"
    assert res["diff"] == ""
    assert "SyntaxError" in res["traceback"]
    assert res["attempt_count"] == 3
    assert res["file_result"]["status"] == "FAILED"


# ── 5. End-to-End File Sub-graph Scenarios ──────────────────────────────


@pytest.mark.asyncio
async def test_scenario_successful_rewrite_with_passing_tests(tmp_path: Path) -> None:
    """Verifies a successful rewrite with passing tests on the first try (0 healing attempts)."""
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "models.py",
        "from pydantic import BaseModel, validator\n\nclass M(BaseModel):\n    @validator('x')\n    def v(cls, val): return val\n",
    )

    ast_server = AsyncMock()

    # Stub apply_transform to rewrite file
    async def mock_ast_call(tool_name: str, args: dict[str, Any]) -> Any:
        if tool_name == "apply_transform":
            rewritten = "from pydantic import BaseModel, field_validator\n\nclass M(BaseModel):\n    @field_validator('x')\n    @classmethod\n    def v(cls, val): return val\n"
            (ws / rel_file).write_text(rewritten, encoding="utf-8")
            return MagicMock(
                content=[
                    MagicMock(
                        text='{"modified": true, "written_to_disk": true, "transformed_code": "..."}'
                    )
                ]
            )
        return MagicMock(content=[MagicMock(text="{}")])

    ast_server.call_tool = mock_ast_call

    sandbox_mgr = MagicMock()
    sandbox_mgr.run_tests.return_value = TestResult(
        passed=True, exit_code=0, stdout="test passed", output="test passed"
    )

    result = await run_file_subgraph(
        file_path=rel_file,
        workspace_path=str(ws),
        target_library="pydantic",
        matched_rules=[
            MatchedRule(
                rule_id="validator-to-field-validator",
                old_qualified_name="pydantic.validator",
                new_qualified_name="pydantic.field_validator",
                risk=RiskLevel.MEDIUM,
                transformer_class="src.rules.pydantic_transformers.ValidatorToFieldValidatorTransformer",
            )
        ],
        ast_server=ast_server,
        sandbox_manager=sandbox_mgr,
        sandbox_container=MagicMock(),
    )

    assert result.status == FileStatus.SUCCESS
    assert result.attempt_count == 0
    assert "@field_validator" in result.diff
    assert result.traceback == ""


@pytest.mark.asyncio
async def test_scenario_self_healing_fixes_test_failure(tmp_path: Path) -> None:
    """Verifies that a test failure triggers self-healing, queries docs, patches code, and succeeds."""
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "models.py",
        "class User:\n    def get_data(self):\n        return self.dict()\n",
    )

    docs_server = AsyncMock()
    docs_server.call_tool.return_value = MagicMock(
        content=[
            MagicMock(
                text='{"results": [{"title": "V2 Guide", "content": "Replace .dict() with .model_dump()"}]}'
            )
        ]
    )

    mock_llm = AsyncMock()
    mock_llm.return_value = "```python\nclass User:\n    def get_data(self):\n        return self.model_dump()\n```"

    # Sandbox: fails on 1st run, passes on 2nd run after patch!
    test_runs = [
        TestResult(
            passed=False,
            exit_code=1,
            stdout="FAIL",
            traceback="E   AttributeError: 'User' has no attribute 'dict'",
            output="FAIL",
        ),
        TestResult(
            passed=True,
            exit_code=0,
            stdout="PASS",
            traceback="",
            output="PASS",
        ),
    ]

    sandbox_mgr = MagicMock()
    sandbox_mgr.run_tests.side_effect = test_runs

    result = await run_file_subgraph(
        file_path=rel_file,
        workspace_path=str(ws),
        target_library="pydantic",
        matched_rules=[],  # Triggers LLM fallback or direct test run
        docs_server=docs_server,
        llm_client=mock_llm,
        sandbox_manager=sandbox_mgr,
        sandbox_container=MagicMock(),
        max_healing_attempts=3,
    )

    assert result.status == FileStatus.SUCCESS
    assert result.attempt_count == 1
    assert "model_dump" in result.diff
    assert result.traceback == ""
    assert "model_dump" in (ws / rel_file).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_scenario_failed_status_after_3_failed_attempts(tmp_path: Path) -> None:
    """Verifies that if tests fail consecutively, loop terminates after 3 attempts with FAILED status."""
    ws, rel_file = _setup_workspace_file(
        tmp_path,
        "broken.py",
        "invalid python code\n",
    )

    docs_server = AsyncMock()
    docs_server.call_tool.return_value = MagicMock(
        content=[MagicMock(text='{"results": []}')]
    )

    mock_llm = AsyncMock()
    mock_llm.return_value = "```python\nstill invalid\n```"

    # Always failing test runs
    failing_result = TestResult(
        passed=False,
        exit_code=1,
        stdout="FAIL",
        traceback="E   SyntaxError: invalid syntax",
        output="FAIL",
    )

    sandbox_mgr = MagicMock()
    sandbox_mgr.run_tests.return_value = failing_result

    result = await run_file_subgraph(
        file_path=rel_file,
        workspace_path=str(ws),
        target_library="pydantic",
        matched_rules=[],
        docs_server=docs_server,
        llm_client=mock_llm,
        sandbox_manager=sandbox_mgr,
        sandbox_container=MagicMock(),
        max_healing_attempts=3,
    )

    assert result.status == FileStatus.FAILED
    assert result.attempt_count == 3
    assert result.diff == ""
    assert "SyntaxError: invalid syntax" in result.traceback
    # Initial run + 3 healing runs = 4 run_tests invocations total
    assert sandbox_mgr.run_tests.call_count == 4


@pytest.mark.asyncio
async def test_scenario_episodic_context_isolation(tmp_path: Path) -> None:
    """Verifies that state from file A does NOT leak into file B."""
    ws = tmp_path
    (ws / "file_a.py").write_text("content_a = 1", encoding="utf-8")
    (ws / "file_b.py").write_text("content_b = 2", encoding="utf-8")

    sandbox_mgr = MagicMock()
    # File A fails all 3 attempts
    # File B passes immediately
    sandbox_mgr.run_tests.side_effect = [
        # File A attempts
        TestResult(passed=False, exit_code=1, traceback="FileA error", output="FileA"),
        TestResult(passed=False, exit_code=1, traceback="FileA error", output="FileA"),
        TestResult(passed=False, exit_code=1, traceback="FileA error", output="FileA"),
        TestResult(passed=False, exit_code=1, traceback="FileA error", output="FileA"),
        # File B attempt
        TestResult(passed=True, exit_code=0, traceback="", output="FileB passed"),
    ]

    mock_llm = AsyncMock()
    mock_llm.return_value = "```python\npatched\n```"

    docs_server = AsyncMock()
    docs_server.call_tool.return_value = MagicMock(
        content=[MagicMock(text='{"results": []}')]
    )

    # Run File A
    res_a = await run_file_subgraph(
        file_path="file_a.py",
        workspace_path=str(ws),
        target_library="pydantic",
        docs_server=docs_server,
        llm_client=mock_llm,
        sandbox_manager=sandbox_mgr,
        sandbox_container=MagicMock(),
        max_healing_attempts=3,
    )

    # Run File B
    res_b = await run_file_subgraph(
        file_path="file_b.py",
        workspace_path=str(ws),
        target_library="pydantic",
        docs_server=docs_server,
        llm_client=mock_llm,
        sandbox_manager=sandbox_mgr,
        sandbox_container=MagicMock(),
        max_healing_attempts=3,
    )

    assert res_a.status == FileStatus.FAILED
    assert res_a.attempt_count == 3
    assert "FileA" in res_a.traceback

    # File B must be completely clean and isolated
    assert res_b.status == FileStatus.SUCCESS
    assert res_b.attempt_count == 0
    assert res_b.traceback == ""
