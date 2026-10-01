import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_servers.docs_server import (
    _clean_html_artifacts,
    _normalize_search_query,
    create_docs_server,
)
import os


@pytest.mark.asyncio
async def test_tool_discovery():
    server = create_docs_server()
    tools = await server.list_tools()
    tool_names = {t.name for t in tools}
    expected_tools = {
        "lookup_doc_ref",
        "search_corpus",
        "web_search",
    }
    assert expected_tools.issubset(tool_names)
    for tool in tools:
        assert tool.description


@pytest.mark.asyncio
async def test_lookup_doc_ref_exact_section():
    server = create_docs_server()
    result = await server.call_tool(
        "lookup_doc_ref",
        {
            "doc_ref": "pydantic-v2-migration.md#validator-to-field-validator",
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["doc_ref"] == "pydantic-v2-migration.md#validator-to-field-validator"
    assert "validator-to-field-validator" in data["section"]
    assert "@field_validator" in data["content"]
    assert "Changes to `@validator`" in data["content"]
    # Ensure it doesn't bleed into the next section
    assert "## root-validator-to-model-validator" not in data["content"]


@pytest.mark.asyncio
async def test_lookup_doc_ref_with_library_prefix():
    server = create_docs_server()
    result = await server.call_tool(
        "lookup_doc_ref",
        {
            "doc_ref": "pydantic/pydantic-v2-migration.md#root-validator-to-model-validator",
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert "@model_validator" in data["content"]
    assert "## config-class-to-model-config" not in data["content"]


@pytest.mark.asyncio
async def test_lookup_doc_ref_missing_section():
    server = create_docs_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "lookup_doc_ref",
            {
                "doc_ref": "pydantic-v2-migration.md#non-existent-section",
            },
        )


@pytest.mark.asyncio
async def test_lookup_doc_ref_missing_file():
    server = create_docs_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "lookup_doc_ref",
            {
                "doc_ref": "non-existent-file.md#any-section",
            },
        )


@pytest.mark.asyncio
async def test_search_corpus_bm25_ranking():
    server = create_docs_server()
    result = await server.call_tool(
        "search_corpus",
        {
            "query": "field_validator classmethod mode before ValidationInfo",
            "target_library": "pydantic",
            "top_k": 3,
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_results"] > 0
    top_result = data["results"][0]
    # The top result should be the validator-to-field-validator section
    assert "validator-to-field-validator" in top_result["doc_ref"]
    assert "@field_validator" in top_result["content"]
    # Higher score than subsequent results
    if len(data["results"]) > 1:
        assert top_result["score"] >= data["results"][1]["score"]


@pytest.mark.asyncio
async def test_search_corpus_config_query():
    server = create_docs_server()
    result = await server.call_tool(
        "search_corpus",
        {
            "query": "model_config ConfigDict orm_mode from_attributes extra forbid",
            "top_k": 2,
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_results"] > 0
    top_result = data["results"][0]
    assert "config-class-to-model-config" in top_result["doc_ref"]
    assert "ConfigDict" in top_result["content"]
    assert len(data["results"]) <= 2


@pytest.mark.asyncio
async def test_search_corpus_no_matches():
    server = create_docs_server()
    result = await server.call_tool(
        "search_corpus",
        {
            "query": "xyz123completelyirrelevantquery987",
            "top_k": 5,
        },
    )

    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_results"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_corpus_empty_query():
    server = create_docs_server()
    with pytest.raises(ToolError):
        await server.call_tool(
            "search_corpus",
            {
                "query": "",
            },
        )


@pytest.mark.asyncio
async def test_clean_html_artifacts():
    dirty_html = """
    <div>
        <h3>Result Title &amp; Subtitle</h3>
        <p>This is a <b>bold</b> text with an <a href="http://example.com">inline link</a>.</p>
        <script>alert("ignore me")</script>
        <p>Special symbols: &lt; &gt; &quot; &#39; &#x2F;</p>
    </div>
    """
    clean = _clean_html_artifacts(dirty_html)
    assert "<div>" not in clean
    assert "<p>" not in clean
    assert "<b>" not in clean
    assert "<script>" not in clean
    assert "</" not in clean
    assert "alert" not in clean
    assert "Result Title & Subtitle" in clean
    assert "Special symbols: < > \" '" in clean


@pytest.mark.asyncio
async def test_web_search_tavily_mocked():
    server = create_docs_server()

    mock_tavily_response = {
        "results": [
            {
                "title": "Migrating to Pydantic V2",
                "url": "https://docs.pydantic.dev/latest/migration/",
                "content": "<p>In V2, <code>validator</code> is deprecated. Use <code>field_validator</code> instead.</p>",
            },
            {
                "title": "Pydantic V2 ConfigDict Changes",
                "url": "https://docs.pydantic.dev/latest/config/",
                "content": "Use <b>ConfigDict</b> with <code>from_attributes=True</code> instead of <i>orm_mode=True</i>.",
            },
            {
                "title": "Third result",
                "url": "https://example.com/3",
                "content": "Extra details on BaseModel.model_dump().",
            },
            {
                "title": "Fourth result (should be dropped because top-3 max)",
                "url": "https://example.com/4",
                "content": "This fourth chunk should not be present in output.",
            },
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_tavily_response
    mock_resp.raise_for_status = MagicMock()

    with patch("src.mcp_servers.docs_server.load_config") as mock_conf, \
         patch("httpx.Client.post", return_value=mock_resp):
        cfg = MagicMock()
        cfg.tavily_api_key = "fake_tavily_key"
        cfg.docs_corpus_dir = "src/docs_corpus"
        mock_conf.return_value = cfg

        result = await server.call_tool(
            "web_search",
            {
                "query": "Pydantic validator deprecated traceback",
                "target_library": "pydantic",
            },
        )

        assert not result.is_error
        data = json.loads(result.content[0].text)
        assert data["source"] == "tavily"
        assert len(data["chunks"]) <= 3
        assert data["total_tokens"] <= 1500
        # Check no HTML artifacts
        for chunk in data["chunks"]:
            assert "<p>" not in chunk["content"]
            assert "<code>" not in chunk["content"]
            assert "<b>" not in chunk["content"]


@pytest.mark.asyncio
async def test_web_search_duckduckgo_fallback_mocked():
    server = create_docs_server()

    mock_ddg_html = """
    <html>
    <body>
        <div class="result__body">
            <h2 class="result__title">
                <a class="result__url" href="/l/?uddg=https%3A%2F%2Fstackoverflow.com%2Fq%2F12345">Pydantic V2 Migration Error</a>
            </h2>
            <a class="result__snippet">In Pydantic v2, @validator is replaced with @field_validator &amp; @classmethod.</a>
        </div>
        <div class="result__body">
            <h2 class="result__title">
                <a class="result__url" href="https://example.com/guide">Migration Guide</a>
            </h2>
            <a class="result__snippet">Config class is replaced with model_config = ConfigDict(from_attributes=True).</a>
        </div>
    </body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_ddg_html
    mock_resp.raise_for_status = MagicMock()

    with patch("src.mcp_servers.docs_server.load_config") as mock_conf, \
         patch("httpx.Client.post", return_value=mock_resp):
        cfg = MagicMock()
        cfg.tavily_api_key = None
        cfg.docs_corpus_dir = "src/docs_corpus"
        mock_conf.return_value = cfg

        result = await server.call_tool(
            "web_search",
            {
                "query": "ValidationError: 'validator' is deprecated in Pydantic v2",
                "target_library": "pydantic",
            },
        )

        assert not result.is_error
        data = json.loads(result.content[0].text)
        assert data["source"] == "duckduckgo"
        assert len(data["chunks"]) >= 1
        assert len(data["chunks"]) <= 3
        assert data["total_tokens"] <= 1500
        first_chunk = data["chunks"][0]
        assert first_chunk["url"] == "https://stackoverflow.com/q/12345"
        assert "@field_validator & @classmethod" in first_chunk["content"]


@pytest.mark.asyncio
async def test_web_search_budget_enforcement():
    server = create_docs_server()

    # Generate a massive response (e.g. 5000 words ~ 6000+ tokens)
    huge_content = "Word " * 5000
    mock_tavily_response = {
        "results": [
            {
                "title": "Huge Page 1",
                "url": "https://example.com/huge1",
                "content": huge_content,
            },
            {
                "title": "Huge Page 2",
                "url": "https://example.com/huge2",
                "content": huge_content,
            },
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_tavily_response
    mock_resp.raise_for_status = MagicMock()

    with patch("src.mcp_servers.docs_server.load_config") as mock_conf, \
         patch("httpx.Client.post", return_value=mock_resp):
        cfg = MagicMock()
        cfg.tavily_api_key = "fake_tavily_key"
        cfg.docs_corpus_dir = "src/docs_corpus"
        mock_conf.return_value = cfg

        result = await server.call_tool(
            "web_search",
            {
                "query": "some error",
                "max_tokens": 1500,
            },
        )

        assert not result.is_error
        data = json.loads(result.content[0].text)
        assert data["total_tokens"] <= 1500
        total_counted = sum(chunk["tokens"] for chunk in data["chunks"])
        assert total_counted <= 1500


def test_normalize_search_query_multi_library_and_versions():
    """Verify dynamic normalization across various libraries, versions, and raw tracebacks."""
    # 1. Traceback with Python 3.11 paths and version tag celery_v4_to_v5
    raw_tb = """
    Traceback (most recent call last):
      File "/usr/local/lib/python3.11/site-packages/vine/five.py", line 361, in <module>
        from inspect import formatargspec, getfullargspec
    ImportError: cannot import name 'formatargspec' from 'inspect' (/usr/local/lib/python3.11/inspect.py)
    """
    q, lib = _normalize_search_query(raw_tb, target_library="celery_v4_to_v5")
    assert lib == "celery"
    assert "celery" in q.lower()
    assert "v4 to v5 migration" in q
    assert "cannot import name 'formatargspec'" in q
    assert "/usr/local/lib" not in q

    # 2. Pydantic v1 to v2 tag
    q2, lib2 = _normalize_search_query(
        "pydantic.errors.PydanticUserError: The 'regex' argument to Field(...) has been removed in Pydantic V2",
        target_library="pydantic-v1-to-v2",
    )
    assert lib2 == "pydantic"
    assert "v1 to v2 migration" in q2
    assert "regex" in q2

    # 3. SQLAlchemy version constraint
    q3, lib3 = _normalize_search_query(
        "RemovedIn20Warning: Deprecated API features detected!",
        target_library="sqlalchemy>=2.0,<3.0",
    )
    assert lib3 == "sqlalchemy"
    assert "v2.0 migration" in q3


@pytest.mark.asyncio
async def test_web_search_tavily_with_synthesis_answer():
    server = create_docs_server()

    mock_tavily_response = {
        "answer": "In Python 3.11 formatargspec is removed. Celery 5.3+ upgrades vine to 5.0+ which fixes this.",
        "results": [
            {
                "title": "Celery 5.3 Release Notes",
                "url": "https://docs.celeryq.dev/en/stable/whatsnew-5.3.html",
                "content": "Python 3.11 compatibility fixes included.",
            }
        ],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_tavily_response
    mock_resp.raise_for_status = MagicMock()

    with patch("src.mcp_servers.docs_server.load_config") as mock_conf, \
         patch("httpx.Client.post", return_value=mock_resp):
        cfg = MagicMock()
        cfg.tavily_api_key = "fake_key"
        mock_conf.return_value = cfg

        result = await server.call_tool(
            "web_search",
            {
                "query": "ImportError: cannot import name formatargspec from inspect",
                "target_library": "celery",
            },
        )

        assert not result.is_error
        data = json.loads(result.content[0].text)
        assert data["source"] == "tavily"
        assert data["answer"] == mock_tavily_response["answer"]
        assert len(data["chunks"]) == 2
        assert "Tavily Migration Synthesis: Celery" in data["chunks"][0]["title"]
        assert "Celery 5.3+" in data["chunks"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("TAVILY_API_KEY"),
    reason="TAVILY_API_KEY environment variable not set for live testing",
)
async def test_web_search_live_tavily():
    """Live integration test against the real Tavily search API."""
    server = create_docs_server()
    result = await server.call_tool(
        "web_search",
        {
            "query": "celery vine inspect formatargspec python 3.11",
            "target_library": "celery",
            "max_tokens": 1200,
        },
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["source"] == "tavily"
    assert len(data["chunks"]) >= 1
    assert data["total_tokens"] <= 1200
    assert any("inspect" in c["content"].lower() or "celery" in c["content"].lower() for c in data["chunks"])

