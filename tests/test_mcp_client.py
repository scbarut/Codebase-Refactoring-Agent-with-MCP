"""Unit tests for src.core.mcp_client.call_mcp_tool."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.mcp_client import call_mcp_tool


@pytest.mark.asyncio
async def test_call_mcp_tool_fastmcp_json_content():
    """FastMCP CallToolResult containing valid JSON is parsed into a dict/list."""
    server = MagicMock()
    content_obj = MagicMock()
    content_obj.text = json.dumps({"status": "ok", "items": [1, 2, 3]})
    result_obj = MagicMock()
    result_obj.content = [content_obj]
    server.call_tool = AsyncMock(return_value=result_obj)

    res = await call_mcp_tool(server, "my_tool", {"arg": "val"})

    server.call_tool.assert_awaited_once_with("my_tool", {"arg": "val"})
    assert res == {"status": "ok", "items": [1, 2, 3]}


@pytest.mark.asyncio
async def test_call_mcp_tool_fastmcp_list_json():
    """FastMCP CallToolResult with JSON list is parsed cleanly."""
    server = MagicMock()
    content_obj = MagicMock()
    content_obj.text = json.dumps(["file1.py", "file2.py"])
    result_obj = MagicMock()
    result_obj.content = [content_obj]
    server.call_tool = AsyncMock(return_value=result_obj)

    res = await call_mcp_tool(server, "list_tool")

    assert res == ["file1.py", "file2.py"]


@pytest.mark.asyncio
async def test_call_mcp_tool_fastmcp_non_json_text():
    """FastMCP CallToolResult with non-JSON string is returned wrapped in dict."""
    server = MagicMock()
    content_obj = MagicMock()
    content_obj.text = "plain non-json error message"
    result_obj = MagicMock()
    result_obj.content = [content_obj]
    server.call_tool = AsyncMock(return_value=result_obj)

    res = await call_mcp_tool(server, "raw_tool")

    assert res == {"text": "plain non-json error message"}


@pytest.mark.asyncio
async def test_call_mcp_tool_fastmcp_direct_dict_result():
    """Server call_tool returning dict directly is returned as-is."""
    server = MagicMock()
    server.call_tool = AsyncMock(return_value={"direct": "dict"})

    res = await call_mcp_tool(server, "dict_tool")

    assert res == {"direct": "dict"}


@pytest.mark.asyncio
async def test_call_mcp_tool_fastmcp_empty_result():
    """Server call_tool returning empty content returns empty dict."""
    server = MagicMock()
    result_obj = MagicMock()
    result_obj.content = []
    server.call_tool = AsyncMock(return_value=result_obj)

    res = await call_mcp_tool(server, "empty_tool")

    assert res == {}


@pytest.mark.asyncio
async def test_call_mcp_tool_sync_call_tool():
    """Synchronous call_tool is handled without coroutine error."""
    server = MagicMock()
    content_obj = MagicMock()
    content_obj.text = json.dumps({"sync": True})
    result_obj = MagicMock()
    result_obj.content = [content_obj]
    server.call_tool = MagicMock(return_value=result_obj)

    res = await call_mcp_tool(server, "sync_tool")

    assert res == {"sync": True}


@pytest.mark.asyncio
async def test_call_mcp_tool_direct_method_attribute_async():
    """Server with matching async method attribute invokes it cleanly."""

    class CustomServer:
        async def custom_tool(self, **kwargs: Any) -> dict[str, Any]:
            return {"received": kwargs}

    server = CustomServer()
    res = await call_mcp_tool(server, "custom_tool", {"x": 42})

    assert res == {"received": {"x": 42}}


@pytest.mark.asyncio
async def test_call_mcp_tool_direct_method_attribute_sync():
    """Server with matching sync method attribute invokes it cleanly."""

    class CustomServer:
        def sync_tool(self, **kwargs: Any) -> dict[str, Any]:
            return {"synced": True}

    server = CustomServer()
    res = await call_mcp_tool(server, "sync_tool")

    assert res == {"synced": True}


@pytest.mark.asyncio
async def test_call_mcp_tool_direct_method_non_dict_fallback():
    """Server method returning non-dict falls back to empty dict."""

    class CustomServer:
        def non_dict_tool(self, **kwargs: Any) -> Any:
            return "unexpected string"

    server = CustomServer()
    res = await call_mcp_tool(server, "non_dict_tool")

    assert res == {}


@pytest.mark.asyncio
async def test_call_mcp_tool_unsupported_tool_raises_value_error():
    """Server missing call_tool and named method raises ValueError."""

    class EmptyServer:
        pass

    server = EmptyServer()
    with pytest.raises(ValueError, match="Server does not support tool 'missing_tool'"):
        await call_mcp_tool(server, "missing_tool")
