"""Tests for the Orchestration Graph: scan, plan, HITL gateway.

Tests operate at the Orchestration Graph boundary seam.  MCP server tools
are exercised against real temp-dir codebases (no network), so the tests
prove the full ingest → scan → build_plan → hitl_gateway pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.core.graph import (
    _build_file_plan_entry,
    _bump_risk,
    _has_test_coverage,
    _max_risk,
    _rule_matches_file,
    build_orchestration_graph,
    compile_orchestration_graph,
    get_async_postgres_saver,
    get_postgres_saver,
    resolve_rule_set,
)
from src.core.models import RiskLevel

# ── Helpers ────────────────────────────────────────────────────────────


def _make_sample_codebase(root: Path) -> Path:
    """Create a small fake codebase that imports pydantic.

    Layout::

        root/
        ├── models.py        (imports pydantic)
        ├── utils.py          (no pydantic import)
        └── tests/
            └── test_models.py  (exists → coverage heuristic says covered)
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "models.py").write_text(
        "from pydantic import BaseModel, validator\n\n"
        "class UserModel(BaseModel):\n"
        "    name: str\n\n"
        "    @validator('name')\n"
        "    def validate_name(cls, v):\n"
        "        return v.strip()\n",
        encoding="utf-8",
    )
    (root / "utils.py").write_text(
        "import os\n\ndef helper():\n    return 42\n", encoding="utf-8"
    )
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_models.py").write_text(
        "def test_placeholder():\n    pass\n", encoding="utf-8"
    )
    return root


def _make_uncovered_codebase(root: Path) -> Path:
    """Codebase with a pydantic file using validator (MEDIUM risk) but NO matching test file."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "api.py").write_text(
        "from pydantic import BaseModel, validator\n\n"
        "class Item(BaseModel):\n"
        "    price: float\n\n"
        "    @validator('price')\n"
        "    def validate_price(cls, v):\n"
        "        return v\n",
        encoding="utf-8",
    )
    return root


def _make_low_risk_codebase(root: Path) -> Path:
    """Codebase with a pydantic file using only BaseSettings (LOW risk) and covered by test."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.py").write_text(
        "from pydantic import BaseSettings\n\n"
        "class AppSettings(BaseSettings):\n"
        "    database_url: str = 'localhost'\n",
        encoding="utf-8",
    )
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_settings.py").write_text(
        "def test_settings():\n    pass\n", encoding="utf-8"
    )
    return root


# ── Unit tests for risk helpers ────────────────────────────────────────


class TestRiskHelpers:
    def test_max_risk_empty(self):
        assert _max_risk([]) == RiskLevel.LOW

    def test_max_risk_single(self):
        assert _max_risk([RiskLevel.HIGH]) == RiskLevel.HIGH

    def test_max_risk_mixed(self):
        assert _max_risk([RiskLevel.LOW, RiskLevel.MEDIUM]) == RiskLevel.MEDIUM

    def test_bump_risk_low(self):
        assert _bump_risk(RiskLevel.LOW) == RiskLevel.MEDIUM

    def test_bump_risk_medium(self):
        assert _bump_risk(RiskLevel.MEDIUM) == RiskLevel.HIGH

    def test_bump_risk_high_stays(self):
        assert _bump_risk(RiskLevel.HIGH) == RiskLevel.HIGH


# ── Unit tests for rule set resolution ─────────────────────────────────


class TestResolveRuleSet:
    def test_pydantic_resolves(self):
        path = resolve_rule_set("pydantic")
        assert path.endswith(".yaml")
        assert "pydantic" in path

    def test_case_insensitive(self):
        assert resolve_rule_set("Pydantic") == resolve_rule_set("pydantic")

    def test_unknown_library_raises(self):
        with pytest.raises(ValueError, match="No rule set"):
            resolve_rule_set("nonexistent-library")


# ── Unit test for test coverage heuristic ──────────────────────────────


class TestCoverageHeuristic:
    def test_covered_file(self, tmp_path: Path):
        codebase = _make_sample_codebase(tmp_path / "repo")
        assert _has_test_coverage("models.py", str(codebase)) is True

    def test_uncovered_file(self, tmp_path: Path):
        codebase = _make_uncovered_codebase(tmp_path / "repo")
        assert _has_test_coverage("api.py", str(codebase)) is False


# ── Integration: full graph through HITL Gateway ───────────────────────


@pytest.mark.asyncio
async def test_graph_produces_plan_and_pauses(tmp_path: Path):
    """The graph should ingest, scan, build a plan, then pause at HITL."""
    source = _make_sample_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-001"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "source": str(source),
        "target_library": "pydantic",
        "workspace_base_dir": str(tmp_path / "workspaces"),
        "workspace_path": "",
        "job_id": "",
        "repo_name": "",
        "base_branch": "",
        "scanned_files": [],
        "migration_plan": [],
        "approved_files": [],
        "rule_set_path": "",
    }

    # Run the graph — it should pause at hitl_gateway
    events: list[dict[str, Any]] = []
    async for event in graph.astream(initial_state, config):
        events.append(event)

    # The last event should be the interrupt
    last_event = events[-1]
    assert "__interrupt__" in last_event, (
        f"Expected graph to pause at HITL gateway. Last event keys: {list(last_event.keys())}"
    )

    # Inspect interrupt payload
    interrupt_info = last_event["__interrupt__"]
    assert len(interrupt_info) == 1
    interrupt_value = interrupt_info[0].value
    assert interrupt_value["message"] == "Migration Plan ready for review"
    plan = interrupt_value["plan"]

    # The plan should have exactly 1 file (models.py imports pydantic)
    assert len(plan) == 1
    entry = plan[0]
    assert entry["file_path"] == "models.py"

    # Only the matched rule for this file should be present (validator, not config or root_validator)
    assert len(entry["matched_rules"]) == 1
    assert entry["matched_rules"][0]["rule_id"] == "validator-to-field-validator"

    # Affected nodes should include UserModel class and validate_name method
    node_names = {n["symbol_name"] for n in entry["affected_nodes"]}
    assert "UserModel" in node_names

    # Risk should not be bumped because test_models.py exists; base risk is MEDIUM
    assert entry["risk"] == "MEDIUM"


@pytest.mark.asyncio
async def test_graph_risk_bumped_without_test_coverage(tmp_path: Path):
    """Files without test coverage should have risk bumped by +1."""
    source = _make_uncovered_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-002"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "source": str(source),
        "target_library": "pydantic",
        "workspace_base_dir": str(tmp_path / "workspaces"),
        "workspace_path": "",
        "job_id": "",
        "repo_name": "",
        "base_branch": "",
        "scanned_files": [],
        "migration_plan": [],
        "approved_files": [],
        "rule_set_path": "",
    }

    events = []
    async for event in graph.astream(initial_state, config):
        events.append(event)

    last_event = events[-1]
    assert "__interrupt__" in last_event

    plan = last_event["__interrupt__"][0].value["plan"]
    assert len(plan) == 1
    entry = plan[0]
    assert entry["file_path"] == "api.py"

    # Matched rule is validator (MEDIUM). Without test coverage, MEDIUM bumps to HIGH.
    assert len(entry["matched_rules"]) == 1
    assert entry["matched_rules"][0]["rule_id"] == "validator-to-field-validator"
    assert entry["risk"] == "HIGH"


@pytest.mark.asyncio
async def test_graph_resume_with_approved_files(tmp_path: Path):
    """After HITL pause, resuming with approved files completes the graph."""
    source = _make_sample_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-003"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "source": str(source),
        "target_library": "pydantic",
        "workspace_base_dir": str(tmp_path / "workspaces"),
        "workspace_path": "",
        "job_id": "",
        "repo_name": "",
        "base_branch": "",
        "scanned_files": [],
        "migration_plan": [],
        "approved_files": [],
        "rule_set_path": "",
    }

    # First run — pauses at HITL
    async for _ in graph.astream(initial_state, config):
        pass

    # Resume with approved files
    resume_events: list[dict[str, Any]] = []
    async for event in graph.astream(Command(resume=["models.py"]), config):
        resume_events.append(event)

    # After resume, the graph should complete (no more interrupts)
    # The hitl_gateway node should have set approved_files
    state = await graph.aget_state(config)
    approved = state.values.get("approved_files", [])
    assert approved == ["models.py"]


@pytest.mark.asyncio
async def test_graph_no_matching_files(tmp_path: Path):
    """When no files import the target library, plan should be empty."""
    source = tmp_path / "empty_source"
    source.mkdir()
    (source / "main.py").write_text("import os\nprint('hello')\n", encoding="utf-8")

    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-004"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "source": str(source),
        "target_library": "pydantic",
        "workspace_base_dir": str(tmp_path / "workspaces"),
        "workspace_path": "",
        "job_id": "",
        "repo_name": "",
        "base_branch": "",
        "scanned_files": [],
        "migration_plan": [],
        "approved_files": [],
        "rule_set_path": "",
    }

    events = []
    async for event in graph.astream(initial_state, config):
        events.append(event)

    last_event = events[-1]
    assert "__interrupt__" in last_event
    plan = last_event["__interrupt__"][0].value["plan"]
    assert plan == []


@pytest.mark.asyncio
async def test_graph_state_persisted_across_pause(tmp_path: Path):
    """Verify that state is persisted by the checkpointer at the HITL pause."""
    source = _make_sample_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-005"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "source": str(source),
        "target_library": "pydantic",
        "workspace_base_dir": str(tmp_path / "workspaces"),
        "workspace_path": "",
        "job_id": "",
        "repo_name": "",
        "base_branch": "",
        "scanned_files": [],
        "migration_plan": [],
        "approved_files": [],
        "rule_set_path": "",
    }

    async for _ in graph.astream(initial_state, config):
        pass

    # Retrieve persisted state
    state = await graph.aget_state(config)
    values = state.values

    # Core state fields should be populated
    assert values["job_id"] != ""
    assert values["workspace_path"] != ""
    assert values["target_library"] == "pydantic"
    assert len(values["scanned_files"]) == 1
    assert len(values["migration_plan"]) == 1
    assert values["approved_files"] == []  # Not yet approved


@pytest.mark.asyncio
async def test_build_orchestration_graph_structure():
    """Verify the graph has the expected node names and edges."""
    builder = build_orchestration_graph()
    # Check nodes exist
    node_names = set(builder.nodes.keys())
    assert {"ingest", "scan", "build_plan", "hitl_gateway"}.issubset(node_names)


class TestPostgresSaverConfig:
    def test_get_postgres_saver_default_url(self):
        with patch("src.core.graph.PostgresSaver.from_conn_string") as mock_from_conn:
            mock_saver = MagicMock()
            mock_from_conn.return_value.__enter__.return_value = mock_saver
            with get_postgres_saver() as saver:
                assert saver is mock_saver
                mock_saver.setup.assert_called_once()
            mock_from_conn.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_async_postgres_saver_custom_url(self):
        with patch(
            "src.core.graph.AsyncPostgresSaver.from_conn_string"
        ) as mock_from_conn:
            mock_saver = MagicMock()
            mock_saver.setup = AsyncMock()
            mock_from_conn.return_value.__aenter__.return_value = mock_saver
            custom_url = "postgresql://user:pass@localhost:5432/custom_db"
            async with get_async_postgres_saver(custom_url) as saver:
                assert saver is mock_saver
                mock_saver.setup.assert_awaited_once()
            mock_from_conn.assert_called_once_with(custom_url)


# ── Tests for specific rule filtering and low-risk isolation ───────────


@pytest.mark.asyncio
async def test_graph_file_with_low_risk_patterns_only(tmp_path: Path):
    """A file with only low-risk patterns must only match low-risk rules and keep LOW risk."""
    source = _make_low_risk_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-low-risk"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "source": str(source),
        "target_library": "pydantic",
        "workspace_base_dir": str(tmp_path / "workspaces"),
        "workspace_path": "",
        "job_id": "",
        "repo_name": "",
        "base_branch": "",
        "scanned_files": [],
        "migration_plan": [],
        "approved_files": [],
        "rule_set_path": "",
    }

    events = []
    async for event in graph.astream(initial_state, config):
        events.append(event)

    last_event = events[-1]
    assert "__interrupt__" in last_event
    plan = last_event["__interrupt__"][0].value["plan"]
    assert len(plan) == 1
    entry = plan[0]
    assert entry["file_path"] == "settings.py"

    # Must ONLY contain the low-risk basesettings rule
    rule_ids = {r["rule_id"] for r in entry["matched_rules"]}
    assert rule_ids == {"basesettings-to-pydantic-settings"}

    # Must NOT have any high or medium risk rules assigned
    assert all(r["risk"] == "LOW" for r in entry["matched_rules"])
    assert "config-class-to-model-config" not in rule_ids
    assert "root-validator-to-model-validator" not in rule_ids
    assert "validator-to-field-validator" not in rule_ids

    # With test coverage, final risk stays LOW
    assert entry["risk"] == "LOW"


class TestRuleMatching:
    def test_rule_matches_decorator(self):
        rule = {
            "id": "validator-to-field-validator",
            "old_qualified_name": "pydantic.validator",
            "new_qualified_name": "pydantic.field_validator",
            "risk": "MEDIUM",
        }
        code = "from pydantic import BaseModel, validator\nclass U(BaseModel):\n @validator('x')\n def f(cls, v): return v\n"
        assert _rule_matches_file(rule, code) is True

    def test_rule_matches_inner_class_config(self):
        rule = {
            "id": "config-class-to-model-config",
            "old_qualified_name": "Config",
            "new_qualified_name": "model_config",
            "risk": "HIGH",
        }
        code = "from pydantic import BaseModel\nclass U(BaseModel):\n class Config:\n  orm_mode = True\n"
        assert _rule_matches_file(rule, code) is True

    def test_rule_matches_method_call(self):
        rule = {
            "id": "dict-to-model-dump",
            "old_qualified_name": "BaseModel.dict",
            "new_qualified_name": "BaseModel.model_dump",
            "risk": "LOW",
        }
        code = "user = User()\ndata = user.dict(exclude_unset=True)\n"
        assert _rule_matches_file(rule, code) is True

    def test_rule_matches_field_regex(self):
        rule = {
            "id": "field-regex-to-pattern",
            "old_qualified_name": "pydantic.Field.regex",
            "new_qualified_name": "pydantic.Field.pattern",
            "risk": "LOW",
        }
        code = "from pydantic import BaseModel, Field\nclass M(BaseModel):\n x: str = Field(..., regex='^[a-z]+$')\n"
        assert _rule_matches_file(rule, code) is True

    def test_rule_does_not_match_unrelated_code(self):
        rule = {
            "id": "config-class-to-model-config",
            "old_qualified_name": "Config",
            "new_qualified_name": "model_config",
            "risk": "HIGH",
        }
        code = "from pydantic import BaseModel\nclass Simple(BaseModel):\n name: str\n"
        assert _rule_matches_file(rule, code) is False


@pytest.mark.asyncio
async def test_build_file_plan_entry_isolated(tmp_path: Path):
    """Verify _build_file_plan_entry builds an isolated plan entry with only matching rules."""
    from src.mcp_servers.ast_server import create_ast_server

    file_path = tmp_path / "models.py"
    file_path.write_text(
        "from pydantic import BaseSettings\n\nclass Config(BaseSettings):\n    host: str = 'localhost'\n",
        encoding="utf-8",
    )
    rules = [
        {
            "id": "basesettings-to-pydantic-settings",
            "old_qualified_name": "pydantic.BaseSettings",
            "new_qualified_name": "pydantic_settings.BaseSettings",
            "risk": "LOW",
        },
        {
            "id": "validator-to-field-validator",
            "old_qualified_name": "pydantic.validator",
            "new_qualified_name": "pydantic.field_validator",
            "risk": "MEDIUM",
        },
    ]
    ast_server = create_ast_server()
    entry = await _build_file_plan_entry(
        rel_file="models.py",
        workspace_path=str(tmp_path),
        rules=rules,
        ast_server=ast_server,
    )
    # Only basesettings matched, validator did not
    assert len(entry.matched_rules) == 1
    assert entry.matched_rules[0].rule_id == "basesettings-to-pydantic-settings"
    assert entry.matched_rules[0].risk == RiskLevel.LOW
