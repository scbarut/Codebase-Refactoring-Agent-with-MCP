import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.core.file_subgraph import (
    FileSubgraphState,
    apply_rules,
    llm_fallback,
    query_docs,
    run_file_subgraph,
    run_tests,
)
from src.core.models import FileStatus, MatchedRule, RiskLevel
from src.core.sandbox import SandboxManager, TestResult
from src.mcp_servers.docs_server import create_docs_server


@pytest.mark.asyncio
async def test_repro_query_docs_fetches_external_source_when_no_matched_rules():
    """When a file has no matched rules, query_docs MUST fetch from external source (web_search)
    even if search_corpus returned generic/weak stopword matches.
    """
    docs_server = AsyncMock()
    # search_corpus returns a weak stopword match (e.g. field-regex-to-pattern)
    docs_server.call_tool.side_effect = [
        # Call 1: search_corpus
        MagicMock(
            content=[
                MagicMock(
                    text=json.dumps({
                        "results": [
                            {
                                "title": "field-regex-to-pattern",
                                "score": 2.1,
                                "content": "Changes in Field() Arguments",
                            }
                        ]
                    })
                )
            ]
        ),
        # Call 2: web_search
        MagicMock(
            content=[
                MagicMock(
                    text=json.dumps({
                        "source": "tavily",
                        "answer": "Use model_validate or updated Pydantic V2 syntax.",
                        "chunks": [
                            {
                                "title": "Pydantic V2 Migration Guide",
                                "content": "Official external migration docs.",
                            }
                        ],
                    })
                )
            ]
        ),
    ]

    state: FileSubgraphState = {
        "file_path": "agent/nodes/decision.py",
        "workspace_path": "/tmp/ws",
        "target_library": "pydantic",
        "traceback": "TypeError: decision_node() missing 1 required positional argument",
        "matched_rules": [],  # NO MATCHED RULES
        "docs_server": docs_server,
        "healing_attempts": 0,
    }

    res = await query_docs(state)

    # Must have called web_search to fetch from external source
    tool_calls = [call.args[0] for call in docs_server.call_tool.call_args_list]
    assert "web_search" in tool_calls, f"Expected web_search in tool calls, got: {tool_calls}"
    assert "Pydantic V2 Migration Guide" in res["doc_context"] or "Official external migration docs" in res["doc_context"] or "Use model_validate" in res["doc_context"]


@pytest.mark.asyncio
async def test_repro_llm_fallback_fetches_external_source_when_no_matched_rules(tmp_path: Path):
    """When a file has no matched rules, llm_fallback should use a meaningful search query
    and fetch from external source (web_search) when corpus has no matches, and use lookup_doc_ref.
    """
    file_p = tmp_path / "answer.py"
    file_p.write_text("from pydantic import BaseModel, Field\nclass A(BaseModel): pass\n")

    docs_server = AsyncMock()
    # search_corpus returns empty
    docs_server.call_tool.side_effect = [
        # Call 1: search_corpus
        MagicMock(content=[MagicMock(text=json.dumps({"results": []}))]),
        # Call 2: web_search
        MagicMock(
            content=[
                MagicMock(
                    text=json.dumps({
                        "source": "tavily",
                        "answer": "Pydantic V2 migration documentation from web.",
                        "chunks": [{"title": "Web Result", "content": "External doc chunk"}],
                    })
                )
            ]
        ),
    ]

    mock_llm = AsyncMock()
    mock_llm.return_value = "```python\nfrom pydantic import BaseModel\nclass A(BaseModel): pass\n```"

    state: FileSubgraphState = {
        "file_path": "answer.py",
        "workspace_path": str(tmp_path),
        "target_library": "pydantic",
        "risk": RiskLevel.MEDIUM,
        "matched_rules": [],
        "unmatched_rules": [{"rule_id": "unmatched-general", "risk": "MEDIUM"}],
        "docs_server": docs_server,
        "llm_client": mock_llm,
    }

    await llm_fallback(state)

    tool_calls = [call.args[0] for call in docs_server.call_tool.call_args_list]
    assert "web_search" in tool_calls, f"Expected web_search in tool calls, got {tool_calls}"


@pytest.mark.asyncio
async def test_repro_duckduckgo_parses_html_results():
    """DuckDuckGo fallback should correctly parse HTML search result pages."""
    server = create_docs_server()

    mock_html = """
    <div class="result results_links results_links_deep web-result ">
        <div class="links_main links_deep result__body">
            <h2 class="result__title">
                <a class="result__a" href="https://docs.pydantic.dev/latest/migration/">Pydantic V2 Migration Guide</a>
            </h2>
            <a class="result__snippet" href="https://docs.pydantic.dev/latest/migration/">
                Detailed migration guide for <b>Pydantic</b> V1 to <b>V2</b>.
            </a>
        </div>
    </div>
    """

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html
    mock_resp.raise_for_status = MagicMock()

    with patch("src.mcp_servers.docs_server.load_config") as mock_conf, \
         patch("httpx.Client.post", return_value=mock_resp):
        cfg = MagicMock()
        cfg.tavily_api_key = None
        cfg.docs_corpus_dir = "src/docs_corpus"
        mock_conf.return_value = cfg

        result = await server.call_tool(
            "web_search",
            {"query": "pydantic v2 migration", "target_library": "pydantic"},
        )

        data = json.loads(result.content[0].text)
        assert data["source"] == "duckduckgo"
        assert len(data["chunks"]) > 0, "DuckDuckGo returned 0 chunks from valid HTML!"
        assert "Pydantic V2 Migration Guide" in data["chunks"][0]["title"]


@pytest.mark.asyncio
async def test_repro_run_tests_with_no_scoped_test_file_does_not_fail_valid_code(tmp_path: Path):
    """When a file has no test file, run_tests must not run global pytest that fails valid code."""
    file_p = tmp_path / "answer.py"
    file_p.write_text("class Answer:\n    pass\n")

    sandbox_mgr = MagicMock()
    container = MagicMock()

    state: FileSubgraphState = {
        "file_path": "answer.py",
        "workspace_path": str(tmp_path),
        "sandbox_manager": sandbox_mgr,
        "sandbox_container": container,
    }

    # If pytest was run without scoped test file, and exit_code was 5 (no tests collected)
    sandbox_mgr.run_tests.return_value = TestResult(
        passed=False,
        exit_code=5,
        output="no tests ran",
        traceback="",
    )

    res = await run_tests(state)
    assert res["passed"] is True, "Exit code 5 (no tests collected) should not mark file as failed!"
