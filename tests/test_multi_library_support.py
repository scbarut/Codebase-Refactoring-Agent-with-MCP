"""Tests verifying multi-library migration support (pydantic, sqlalchemy, requests, celery)."""

from pathlib import Path

import pytest

from src.core.graph import _build_file_plan_entry, _has_test_coverage, resolve_rule_set
from src.core.mcp_client import call_mcp_tool
from src.mcp_servers.ast_server import create_ast_server
from src.rules.loader import load_rules


def test_rule_sets_registry():
    """Verify that all 4 target libraries resolve their rule set files."""
    for lib in ["pydantic", "sqlalchemy", "requests", "celery"]:
        rule_path = resolve_rule_set(lib)
        assert Path(rule_path).exists(), f"Rule file for {lib} does not exist: {rule_path}"
        rules = load_rules(rule_path)
        assert len(rules) > 0, f"No rules loaded for {lib}"


@pytest.mark.asyncio
async def test_scan_and_plan_temprepo_libraries():
    """Verify scanning and plan building against temprepo for all 4 libraries."""
    temprepo_dir = Path("temprepo").resolve()
    assert temprepo_dir.is_dir()

    ast_server = create_ast_server()

    # 1. Pydantic
    pydantic_scan = await call_mcp_tool(
        ast_server, "scan_imports", {"directory_path": str(temprepo_dir), "target_library": "pydantic"}
    )
    assert "models.py" in pydantic_scan["relative_files"]
    assert "schemas.py" in pydantic_scan["relative_files"]
    assert _has_test_coverage("models.py", str(temprepo_dir)) is True
    assert _has_test_coverage("schemas.py", str(temprepo_dir)) is True

    # 2. SQLAlchemy
    sa_scan = await call_mcp_tool(
        ast_server, "scan_imports", {"directory_path": str(temprepo_dir), "target_library": "sqlalchemy"}
    )
    assert "database.py" in sa_scan["relative_files"]
    assert _has_test_coverage("database.py", str(temprepo_dir)) is True
    sa_rules = load_rules(resolve_rule_set("sqlalchemy"))
    sa_plan = await _build_file_plan_entry("database.py", str(temprepo_dir), sa_rules, ast_server)
    assert len(sa_plan.matched_rules) > 0

    # 3. Requests
    req_scan = await call_mcp_tool(
        ast_server, "scan_imports", {"directory_path": str(temprepo_dir), "target_library": "requests"}
    )
    assert "api_client.py" in req_scan["relative_files"]
    assert _has_test_coverage("api_client.py", str(temprepo_dir)) is True
    req_rules = load_rules(resolve_rule_set("requests"))
    req_plan = await _build_file_plan_entry("api_client.py", str(temprepo_dir), req_rules, ast_server)
    assert len(req_plan.matched_rules) > 0

    # 4. Celery
    celery_scan = await call_mcp_tool(
        ast_server, "scan_imports", {"directory_path": str(temprepo_dir), "target_library": "celery"}
    )
    assert "tasks.py" in celery_scan["relative_files"]
    assert _has_test_coverage("tasks.py", str(temprepo_dir)) is True
    celery_rules = load_rules(resolve_rule_set("celery"))
    celery_plan = await _build_file_plan_entry("tasks.py", str(temprepo_dir), celery_rules, ast_server)
    assert len(celery_plan.matched_rules) > 0
