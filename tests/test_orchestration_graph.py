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
    aggregate_results,
    build_orchestration_graph,
    commit_and_output,
    compile_orchestration_graph,
    dispatch_file_subgraphs,
    get_async_postgres_saver,
    get_postgres_saver,
    is_github_repo,
    resolve_rule_set,
    resume_from_hitl,
)
from src.core.models import (
    FileResult,
    FileStatus,
    GitCommands,
    MigrationResult,
    RiskLevel,
)

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

    # Verify second-half nodes executed to completion
    file_results = state.values.get("file_results", [])
    assert len(file_results) == 1
    assert file_results[0]["file_path"] == "models.py"

    migration_res = state.values.get("migration_result", {})
    assert migration_res["total_files"] == 1
    assert migration_res["success_count"] == 1
    assert migration_res["failure_count"] == 0

    git_cmds = state.values.get("git_commands", {})
    assert "commands" in git_cmds
    assert "one_liner" in git_cmds


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


# ── Helpers for Second-Half Orchestration Tests ─────────────────────────


def _make_two_file_codebase(root: Path) -> Path:
    """Create a sample codebase with two files importing pydantic."""
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
    (root / "schemas.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class ItemSchema(BaseModel):\n"
        "    id: int\n"
        "    title: str\n",
        encoding="utf-8",
    )
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_models.py").write_text(
        "def test_dummy(): pass\n", encoding="utf-8"
    )
    (tests_dir / "test_schemas.py").write_text(
        "def test_dummy(): pass\n", encoding="utf-8"
    )
    return root


# ── End-to-End Orchestration Graph Tests (Ticket 09) ───────────────────


@pytest.mark.asyncio
async def test_end_to_end_job_lifecycle_with_stubs(tmp_path: Path):
    """Simulate a full job lifecycle: submit -> scan -> plan -> approve -> rewrite -> result.

    Verifies that the Orchestration Graph runs end-to-end with stubbed File Sub-graph
    runner and produces a complete MigrationResult with diffs and git commands.
    """
    source = _make_two_file_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-e2e-001"
    config = {"configurable": {"thread_id": thread_id}}

    # Define custom stubbed file runner returning distinct diffs per file
    async def stub_runner(file_path: str, **kwargs: Any) -> FileResult:
        return FileResult(
            file_path=file_path,
            status=FileStatus.SUCCESS,
            diff=f"--- a/{file_path}\n+++ b/{file_path}\n@@ -1 +1 @@\n-old\n+new",
            traceback="",
            attempt_count=0,
        )

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

    with patch("src.core.graph.run_file_subgraph", side_effect=stub_runner):
        # 1. Submit -> Ingest -> Scan -> Build Plan -> pause at HITL Gateway
        async for _ in graph.astream(initial_state, config):
            pass

        state_before_approval = await graph.aget_state(config)
        plan = state_before_approval.values.get("migration_plan", [])
        assert len(plan) == 2
        planned_paths = {p["file_path"] for p in plan}
        assert planned_paths == {"models.py", "schemas.py"}

        # 2. Approve all planned files -> resume graph
        async for _ in graph.astream(
            Command(resume=["models.py", "schemas.py"]), config
        ):
            pass

    # 3. Verify final state and MigrationResult
    final_state = await graph.aget_state(config)
    values = final_state.values

    assert values["approved_files"] == ["models.py", "schemas.py"]

    # Verify per-file results
    file_results = values.get("file_results", [])
    assert len(file_results) == 2
    assert {r["file_path"] for r in file_results} == {"models.py", "schemas.py"}
    assert all(r["status"] == "SUCCESS" for r in file_results)

    # Verify MigrationResult
    migration_res = values.get("migration_result", {})
    assert migration_res["total_files"] == 2
    assert migration_res["success_count"] == 2
    assert migration_res["failure_count"] == 0
    assert migration_res["total_healing_attempts"] == 0
    assert len(migration_res["successful_files"]) == 2
    assert len(migration_res["failed_files"]) == 0
    assert "models.py" in migration_res["full_diff"]
    assert "schemas.py" in migration_res["full_diff"]

    # Verify git commands
    git_cmds = values.get("git_commands", {})
    assert "commands" in git_cmds
    assert len(git_cmds["commands"]) >= 3
    assert "one_liner" in git_cmds
    assert "patch_command" in git_cmds
    assert git_cmds["is_github"] is False
    assert git_cmds["pr_command"] is None


@pytest.mark.asyncio
async def test_end_to_end_selective_approval(tmp_path: Path):
    """When a human selectively approves only a subset of files, only approved files are processed."""
    source = _make_two_file_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-selective"
    config = {"configurable": {"thread_id": thread_id}}

    processed_files: list[str] = []

    async def stub_runner(file_path: str, **kwargs: Any) -> FileResult:
        processed_files.append(file_path)
        return FileResult(
            file_path=file_path,
            status=FileStatus.SUCCESS,
            diff=f"--- a/{file_path}\n+++ b/{file_path}\n@@ -1 +1 @@\n-old\n+new",
            traceback="",
            attempt_count=0,
        )

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

    with patch("src.core.graph.run_file_subgraph", side_effect=stub_runner):
        async for _ in graph.astream(initial_state, config):
            pass

        # Human only approves models.py (prunes schemas.py)
        async for _ in graph.astream(Command(resume=["models.py"]), config):
            pass

    final_state = await graph.aget_state(config)
    values = final_state.values

    assert values["approved_files"] == ["models.py"]
    assert processed_files == ["models.py"]

    migration_res = values.get("migration_result", {})
    assert migration_res["total_files"] == 1
    assert migration_res["success_count"] == 1
    assert migration_res["failure_count"] == 0
    assert len(migration_res["successful_files"]) == 1
    assert migration_res["successful_files"][0]["file_path"] == "models.py"


@pytest.mark.asyncio
async def test_end_to_end_with_file_failure(tmp_path: Path):
    """When a file fails migration, MigrationResult partitions successes and failures accurately."""
    source = _make_two_file_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-failure"
    config = {"configurable": {"thread_id": thread_id}}

    async def stub_runner(file_path: str, **kwargs: Any) -> FileResult:
        if file_path == "models.py":
            return FileResult(
                file_path=file_path,
                status=FileStatus.SUCCESS,
                diff=f"--- a/{file_path}\n+++ b/{file_path}\n@@ -1 +1 @@\n-old\n+new",
                traceback="",
                attempt_count=1,
            )
        else:
            return FileResult(
                file_path=file_path,
                status=FileStatus.FAILED,
                diff="",
                traceback="E   AssertionError: validation failed after 3 attempts",
                attempt_count=3,
            )

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

    with patch("src.core.graph.run_file_subgraph", side_effect=stub_runner):
        async for _ in graph.astream(initial_state, config):
            pass

        async for _ in graph.astream(
            Command(resume=["models.py", "schemas.py"]), config
        ):
            pass

    final_state = await graph.aget_state(config)
    migration_res = final_state.values.get("migration_result", {})

    assert migration_res["total_files"] == 2
    assert migration_res["success_count"] == 1
    assert migration_res["failure_count"] == 1
    assert migration_res["total_healing_attempts"] == 4  # 1 (models) + 3 (schemas)

    assert len(migration_res["successful_files"]) == 1
    assert migration_res["successful_files"][0]["file_path"] == "models.py"

    assert len(migration_res["failed_files"]) == 1
    assert migration_res["failed_files"][0]["file_path"] == "schemas.py"
    assert "AssertionError" in migration_res["failed_files"][0]["traceback"]


@pytest.mark.asyncio
async def test_end_to_end_github_repo_source(tmp_path: Path):
    """GitHub-sourced repositories include optional PR creation command in git_commands."""
    from src.core.graph import _call_git_tool

    source_dir = _make_sample_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-github-pr"
    config = {"configurable": {"thread_id": thread_id}}

    async def fake_call_git_tool(server: Any, tool_name: str, args: dict[str, Any]):
        if tool_name == "clone_repo":
            # Delegate to real clone_repo with local source_dir
            return await _call_git_tool(
                server,
                "clone_repo",
                {
                    "source": str(source_dir),
                    "workspace_base_dir": str(tmp_path / "workspaces"),
                },
            )
        return await _call_git_tool(server, tool_name, args)

    initial_state: dict[str, Any] = {
        "source": "https://github.com/my-org/my-service.git",
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

    with patch("src.core.graph._call_git_tool", side_effect=fake_call_git_tool):
        async for _ in graph.astream(initial_state, config):
            pass

        async for _ in graph.astream(Command(resume=["models.py"]), config):
            pass

    final_state = await graph.aget_state(config)
    git_cmds = final_state.values.get("git_commands", {})

    assert git_cmds["is_github"] is True
    assert git_cmds["pr_command"] is not None
    assert "gh pr create" in git_cmds["pr_command"]
    assert "Migrate to pydantic" in git_cmds["pr_command"]


@pytest.mark.asyncio
async def test_end_to_end_empty_approval_prunes_all(tmp_path: Path):
    """When a human rejects all files (empty list), the job completes cleanly with 0 files."""
    source = _make_sample_codebase(tmp_path / "source")
    checkpointer = InMemorySaver()
    graph = compile_orchestration_graph(checkpointer=checkpointer)

    thread_id = "test-thread-empty-approval"
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

    # Reject all files
    async for _ in graph.astream(Command(resume=[]), config):
        pass

    final_state = await graph.aget_state(config)
    values = final_state.values

    assert values["approved_files"] == []
    assert values["file_results"] == []
    migration_res = values.get("migration_result", {})
    assert migration_res["total_files"] == 0
    assert migration_res["success_count"] == 0
    assert migration_res["failure_count"] == 0


# ── Unit Tests for Second-Half Nodes ────────────────────────────────────


class TestResumeFromHitlUnit:
    @pytest.mark.asyncio
    async def test_normalizes_list_of_strings(self):
        state = {
            "approved_files": ["a.py", "b.py"],
            "workspace_path": "",
            "target_library": "pydantic",
        }
        res = await resume_from_hitl(state)
        assert res["approved_files"] == ["a.py", "b.py"]
        assert res["file_results"] == []

    @pytest.mark.asyncio
    async def test_normalizes_list_of_dicts(self):
        state = {
            "approved_files": [{"file_path": "a.py"}, {"file_path": "b.py"}],
            "workspace_path": "",
            "target_library": "pydantic",
        }
        res = await resume_from_hitl(state)
        assert res["approved_files"] == ["a.py", "b.py"]

    @pytest.mark.asyncio
    async def test_none_defaults_to_migration_plan(self):
        state = {
            "approved_files": None,
            "migration_plan": [{"file_path": "m.py"}],
            "workspace_path": "",
            "target_library": "pydantic",
        }
        res = await resume_from_hitl(state)
        assert res["approved_files"] == ["m.py"]


class TestDispatchFileSubgraphsUnit:
    @pytest.mark.asyncio
    async def test_sequential_execution_with_stub(self):
        calls = []

        async def dummy_runner(file_path: str, **kwargs: Any) -> FileResult:
            calls.append(file_path)
            return FileResult(file_path=file_path, status=FileStatus.SUCCESS)

        state = {
            "approved_files": ["f1.py", "f2.py"],
            "workspace_path": "/dummy",
            "target_library": "pydantic",
            "migration_plan": [],
        }
        with patch("src.core.graph.run_file_subgraph", side_effect=dummy_runner):
            res = await dispatch_file_subgraphs(state)
        assert calls == ["f1.py", "f2.py"]
        assert len(res["file_results"]) == 2
        assert res["file_results"][0]["file_path"] == "f1.py"

    @pytest.mark.asyncio
    async def test_runner_exception_handled_cleanly(self):
        async def failing_runner(file_path: str, **kwargs: Any) -> FileResult:
            raise RuntimeError("Unexpected runner crash")

        state = {
            "approved_files": ["broken.py"],
            "workspace_path": "/dummy",
            "target_library": "pydantic",
            "migration_plan": [],
        }
        with patch("src.core.graph.run_file_subgraph", side_effect=failing_runner):
            res = await dispatch_file_subgraphs(state)
        assert len(res["file_results"]) == 1
        entry = res["file_results"][0]
        assert entry["status"] == "FAILED"
        assert "Unexpected runner crash" in entry["traceback"]


class TestAggregateResultsUnit:
    @pytest.mark.asyncio
    async def test_summary_and_partitioning(self):
        file_results = [
            {
                "file_path": "a.py",
                "status": "SUCCESS",
                "diff": "diff_a",
                "attempt_count": 0,
            },
            {
                "file_path": "b.py",
                "status": "SUCCESS",
                "diff": "diff_b",
                "attempt_count": 1,
            },
            {
                "file_path": "c.py",
                "status": "FAILED",
                "traceback": "err_c",
                "attempt_count": 3,
            },
        ]
        state = {
            "job_id": "job-123",
            "target_library": "pydantic",
            "file_results": file_results,
        }
        res = await aggregate_results(state)
        mr = res["migration_result"]

        assert mr["job_id"] == "job-123"
        assert mr["total_files"] == 3
        assert mr["success_count"] == 2
        assert mr["failure_count"] == 1
        assert mr["total_healing_attempts"] == 4
        assert len(mr["successful_files"]) == 2
        assert len(mr["failed_files"]) == 1
        assert mr["failed_files"][0]["file_path"] == "c.py"


class TestCommitAndOutputUnit:
    @pytest.mark.asyncio
    async def test_commit_and_output_calls_git_server(self):
        mock_git = MagicMock()

        async def fake_call(tool_name: str, args: dict[str, Any]):
            if tool_name == "commit_file":
                return {"commit_hash": "abc"}
            if tool_name == "generate_diff":
                return {"diff": "unified-diff-text"}
            if tool_name == "get_apply_commands":
                return {
                    "commands": ["git fetch", "git merge"],
                    "one_liner": "git fetch && git merge",
                    "patch_command": "git apply --check",
                }
            return {}

        mock_git.call_tool = AsyncMock(side_effect=fake_call)

        state = {
            "file_results": [
                {"file_path": "a.py", "status": "SUCCESS", "diff": "+a"},
                {"file_path": "b.py", "status": "FAILED", "traceback": "fail"},
            ],
            "workspace_path": "/dummy/ws",
            "target_library": "pydantic",
            "base_branch": "main",
            "branch_name": "migrate/pydantic",
            "source": "/local/repo",
            "migration_result": {"total_files": 2},
        }

        with patch("src.core.graph.create_git_server", return_value=mock_git):
            res = await commit_and_output(state)

        # Only a.py (successful) was committed
        mock_git.call_tool.assert_any_call(
            "commit_file",
            {
                "workspace_path": "/dummy/ws",
                "file_path": "a.py",
                "message": "Migrate a.py to pydantic",
            },
        )
        mock_git.call_tool.assert_any_call(
            "generate_diff",
            {
                "workspace_path": "/dummy/ws",
                "base_branch": "main",
            },
        )
        mock_git.call_tool.assert_any_call(
            "get_apply_commands",
            {
                "workspace_path": "/dummy/ws",
                "branch_name": "migrate/pydantic",
                "original_path": "/local/repo",
            },
        )

        assert res["migration_result"]["full_diff"] == "unified-diff-text"
        assert res["git_commands"]["is_github"] is False
        assert res["git_commands"]["pr_command"] is None


class TestIsGithubRepo:
    def test_github_urls(self):
        assert is_github_repo("https://github.com/user/repo") is True
        assert is_github_repo("http://github.com/user/repo.git") is True
        assert is_github_repo("git@github.com:user/repo.git") is True

    def test_non_github_urls(self):
        assert is_github_repo("/local/path/to/repo") is False
        assert is_github_repo("C:\\path\\to\\repo") is False
        assert is_github_repo("https://gitlab.com/user/repo") is False
        assert is_github_repo("") is False
        assert is_github_repo(None) is False


class TestOrchestrationModels:
    def test_git_commands_model(self):
        cmds = GitCommands(
            commands=["git fetch", "git merge"],
            one_liner="git fetch && git merge",
            patch_command="git apply",
            workspace_path="/ws",
            branch_name="b1",
            pr_command="gh pr create",
            is_github=True,
        )
        assert cmds.commands == ["git fetch", "git merge"]
        assert cmds.is_github is True
        assert cmds.pr_command == "gh pr create"

    def test_migration_result_model(self):
        result = MigrationResult(
            job_id="job-1",
            target_library="pydantic",
            total_files=1,
            successful_files=[
                FileResult(file_path="a.py", status=FileStatus.SUCCESS, diff="+code")
            ],
            failed_files=[],
            success_count=1,
            failure_count=0,
            total_healing_attempts=1,
            full_diff="+code",
            git_commands={"commands": ["git merge"]},
        )
        assert result.job_id == "job-1"
        assert result.success_count == 1
        assert len(result.successful_files) == 1
        assert result.successful_files[0].file_path == "a.py"
