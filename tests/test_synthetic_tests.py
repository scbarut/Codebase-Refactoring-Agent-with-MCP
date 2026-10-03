"""Tests for synthetic smoke test generation on uncovered files (ADR-0007).

Verifies:
1. CoverageStatus enum and assignment in FilePlanEntry.
2. generate_synthetic_test node passes through when an existing test file exists.
3. generate_synthetic_test synthesizes a smoke test when no test file exists.
4. Traceback disambiguation distinguishes test fixture errors vs module logic errors.
5. heal_synthetic_test allows 1 attempt to adjust test fixture when test frame fails.
6. finalize cleans up ephemeral .tmp_smoke_test_ by default.
7. finalize persists to tests/test_<stem>_synthetic.py when persist_synthetic_test=True.
8. End-to-end File Sub-graph runs synthetic test and drives self-healing loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.file_subgraph import (
    SYNTHETIC_TEST_PREFIX,
    FileSubgraphState,
    _disambiguate_failure,
    finalize,
    generate_synthetic_test,
    heal_synthetic_test,
    route_after_test,
    run_file_subgraph,
)
from src.core.models import CoverageStatus, FilePlanEntry, FileStatus, RiskLevel
from src.core.sandbox import TestResult

# ── 1. Model Seam Tests ────────────────────────────────────────────────


def test_coverage_status_enum():
    assert CoverageStatus.VERIFIED == "VERIFIED"
    assert CoverageStatus.SYNTHETIC == "SYNTHETIC"
    assert CoverageStatus.UNCOVERED == "UNCOVERED"


def test_synthetic_test_prefix_constant():
    assert SYNTHETIC_TEST_PREFIX == "_tmp_smoke_test_"


def test_file_plan_entry_includes_coverage_status():
    entry = FilePlanEntry(
        file_path="service.py",
        matched_rules=[],
        affected_nodes=[],
        risk=RiskLevel.LOW,
        coverage_status=CoverageStatus.VERIFIED,
    )
    assert entry.coverage_status == CoverageStatus.VERIFIED


# ── 2. Synthetic Test Generation Node Tests ─────────────────────────────


@pytest.mark.asyncio
async def test_generate_synthetic_test_passes_through_existing_test(tmp_path: Path):
    """When a test file already exists, do not generate synthetic test."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "user.py").write_text("class User:\n    pass\n")
    tests_dir = ws / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_user.py").write_text("def test_user(): pass\n")

    state: FileSubgraphState = {
        "file_path": "user.py",
        "workspace_path": str(ws),
        "target_library": "pydantic",
    }

    result = await generate_synthetic_test(state)
    assert result.get("is_synthetic_test") is False
    assert result.get("test_file_path") == "tests/test_user.py"
    # Ensure no temporary file created
    assert not (ws / f"{SYNTHETIC_TEST_PREFIX}user.py").exists()


@pytest.mark.asyncio
async def test_generate_synthetic_test_creates_smoke_test_when_no_test_exists(tmp_path: Path):
    """When no test file exists, extract AST signatures and synthesize smoke test."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "untested.py").write_text("class Untested:\n    def run(self): return 42\n")

    mock_ast_server = AsyncMock()
    mock_ast_server.call_tool.return_value = MagicMock(
        content=[
            MagicMock(
                text=json.dumps({
                    "classes": [{"name": "Untested", "methods": [{"name": "run"}]}],
                    "functions": [],
                })
            )
        ]
    )

    mock_llm = AsyncMock()
    mock_llm.return_value = (
        "```python\n"
        "from untested import Untested\n\n"
        "def test_smoke_untested():\n"
        "    obj = Untested()\n"
        "    assert obj.run() == 42\n"
        "```"
    )

    state: FileSubgraphState = {
        "file_path": "untested.py",
        "workspace_path": str(ws),
        "target_library": "pydantic",
        "ast_server": mock_ast_server,
        "llm_client": mock_llm,
    }

    result = await generate_synthetic_test(state)
    assert result.get("is_synthetic_test") is True
    assert result.get("test_file_path") == "_tmp_smoke_test_untested.py"

    smoke_file = ws / "_tmp_smoke_test_untested.py"
    assert smoke_file.is_file()
    assert "test_smoke_untested" in smoke_file.read_text(encoding="utf-8")


# ── 3. Disambiguation Tests ─────────────────────────────────────────────


def test_disambiguate_failure_detects_test_caller_frame():
    """Traceback originating inside the test script frame routes to test healing."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "_tmp_smoke_test_model.py", line 8, in test_smoke\n'
        "    model = UserModel(wrong_arg=123)\n"
        "TypeError: UserModel.__init__() got an unexpected keyword argument 'wrong_arg'\n"
    )
    result = _disambiguate_failure(
        traceback=traceback,
        test_file_path="_tmp_smoke_test_model.py",
        source_file_path="model.py",
    )
    assert result == "test_frame"


def test_disambiguate_failure_detects_source_callee_frame():
    """Traceback reaching into the migrated source module routes to source healing."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "_tmp_smoke_test_model.py", line 5, in test_smoke\n'
        "    model = UserModel(name='test')\n"
        '  File "model.py", line 12, in __init__\n'
        "    raise ValueError('Regression in validation')\n"
        "ValueError: Regression in validation\n"
    )
    result = _disambiguate_failure(
        traceback=traceback,
        test_file_path="_tmp_smoke_test_model.py",
        source_file_path="model.py",
    )
    assert result == "source_frame"


def test_route_after_test_routes_to_heal_synthetic_test_when_test_frame_fails():
    """When a synthetic test fixture fails and test_healing_attempts < 1, heal test."""
    traceback = (
        '  File "_tmp_smoke_test_model.py", line 8, in test_smoke\n'
        "TypeError: invalid test call\n"
    )
    state: FileSubgraphState = {
        "passed": False,
        "is_synthetic_test": True,
        "test_file_path": "_tmp_smoke_test_model.py",
        "file_path": "model.py",
        "traceback": traceback,
        "test_healing_attempts": 0,
        "healing_attempts": 0,
    }
    assert route_after_test(state) == "heal_synthetic_test"


def test_route_after_test_routes_to_extract_traceback_after_one_test_heal():
    """After 1 test heal attempt, failures route to source code healing."""
    traceback = (
        '  File "_tmp_smoke_test_model.py", line 8, in test_smoke\n'
        "TypeError: invalid test call\n"
    )
    state: FileSubgraphState = {
        "passed": False,
        "is_synthetic_test": True,
        "test_file_path": "_tmp_smoke_test_model.py",
        "file_path": "model.py",
        "traceback": traceback,
        "test_healing_attempts": 1,
        "healing_attempts": 0,
    }
    assert route_after_test(state) == "extract_traceback"


# ── 4. Cleanup & Persistence Tests in finalize ─────────────────────────


@pytest.mark.asyncio
async def test_finalize_deletes_ephemeral_synthetic_test(tmp_path: Path):
    """By default, _tmp_smoke_test_ is cleaned up on finalize."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    smoke_file = ws / "_tmp_smoke_test_calc.py"
    smoke_file.write_text("def test_smoke(): pass\n")
    (ws / "calc.py").write_text("def add(a, b): return a + b\n")

    state: FileSubgraphState = {
        "file_path": "calc.py",
        "workspace_path": str(ws),
        "is_synthetic_test": True,
        "test_file_path": "_tmp_smoke_test_calc.py",
        "persist_synthetic_test": False,
        "passed": True,
        "original_content": "def add(a, b): return a + b\n",
        "current_content": "def add(a, b): return a + b\n",
    }

    await finalize(state)
    assert not smoke_file.exists()


@pytest.mark.asyncio
async def test_finalize_persists_synthetic_test_when_requested(tmp_path: Path):
    """When persist_synthetic_test=True, rename to tests/test_<stem>_synthetic.py."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    smoke_file = ws / "_tmp_smoke_test_calc.py"
    smoke_file.write_text("def test_smoke(): pass\n")
    (ws / "calc.py").write_text("def add(a, b): return a + b\n")

    state: FileSubgraphState = {
        "file_path": "calc.py",
        "workspace_path": str(ws),
        "is_synthetic_test": True,
        "test_file_path": "_tmp_smoke_test_calc.py",
        "persist_synthetic_test": True,
        "passed": True,
        "original_content": "def add(a, b): return a + b\n",
        "current_content": "def add(a, b): return a + b\n",
    }

    res = await finalize(state)
    assert not smoke_file.exists()
    persisted = ws / "tests" / "test_calc_synthetic.py"
    assert persisted.is_file()
    assert res["file_result"]["status"] == FileStatus.SUCCESS


# ── 5. Heal Synthetic Test Node Tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_heal_synthetic_test_node(tmp_path: Path):
    """heal_synthetic_test prompts the LLM with failure traceback to fix test fixture."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    smoke_file = ws / "_tmp_smoke_test_service.py"
    smoke_file.write_text("def test_service(): raise TypeError('wrong argument')\n")
    (ws / "service.py").write_text("class Service:\n    def __init__(self, key: str): self.key = key\n")

    mock_llm = AsyncMock()
    mock_llm.return_value = (
        "```python\n"
        "from service import Service\n\n"
        "def test_service():\n"
        "    s = Service(key='test')\n"
        "    assert s.key == 'test'\n"
        "```"
    )

    state: FileSubgraphState = {
        "file_path": "service.py",
        "workspace_path": str(ws),
        "target_library": "pydantic",
        "test_file_path": "_tmp_smoke_test_service.py",
        "traceback": "TypeError: wrong argument",
        "test_healing_attempts": 0,
        "llm_client": mock_llm,
    }

    result = await heal_synthetic_test(state)
    assert result["test_healing_attempts"] == 1
    assert "Service(key='test')" in result["test_content"]
    assert "Service(key='test')" in smoke_file.read_text(encoding="utf-8")


# ── 6. End-to-End File Sub-graph with Synthetic Tests ───────────────────


@pytest.mark.asyncio
async def test_end_to_end_file_subgraph_with_synthetic_test(tmp_path: Path):
    """File Sub-graph autonomously synthesizes smoke test for uncovered file and passes."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    source_file = ws / "helper.py"
    source_file.write_text("def compute(): return 100\n")

    mock_ast_server = AsyncMock()
    mock_ast_server.call_tool.return_value = MagicMock(
        content=[
            MagicMock(
                text=json.dumps({
                    "classes": [],
                    "functions": [{"name": "compute"}],
                })
            )
        ]
    )

    mock_llm = AsyncMock()
    mock_llm.return_value = (
        "```python\n"
        "from helper import compute\n\n"
        "def test_smoke_helper():\n"
        "    assert compute() == 100\n"
        "```"
    )

    mock_sandbox = AsyncMock()
    mock_sandbox.run_tests.return_value = TestResult(
        passed=True,
        exit_code=0,
        output="1 passed",
        traceback="",
    )

    res = await run_file_subgraph(
        file_path="helper.py",
        workspace_path=str(ws),
        target_library="pydantic",
        matched_rules=[],
        sandbox_manager=mock_sandbox,
        sandbox_container=MagicMock(),
        ast_server=mock_ast_server,
        llm_client=mock_llm,
        persist_synthetic_test=False,
    )

    assert res.status == FileStatus.SUCCESS
    # Ephemeral test must be cleaned up
    assert not (ws / "_tmp_smoke_test_helper.py").exists()

