"""MCP Client Helper — unified tool execution for MCP server instances.

Dispatches tool calls across FastMCP server instances, direct object callables,
and parses CallToolResult content (JSON / text) cleanly into structured data.
"""

from __future__ import annotations

import inspect
import json
from typing import Any


async def call_mcp_tool(
    server: Any,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """Call an MCP server tool and parse the result cleanly.

    Supports FastMCP server instances (via ``call_tool``), direct tool callable
    attributes, and handles coroutines/awaitables and CallToolResult content /
    JSON parsing.

    Args:
        server: An MCP server instance (e.g. FastMCP) or an object implementing
            ``call_tool`` or the named tool as a method/callable.
        tool_name: The name of the tool to invoke.
        arguments: Optional dictionary of keyword arguments passed to the tool.

    Returns:
        A parsed dict or list if JSON text content is present, or raw text/dict,
        or an empty dict if the result is empty or not directly structured.

    Raises:
        ValueError: If the server object does not implement ``call_tool`` or the
            requested tool method.
    """
    args = arguments if arguments is not None else {}

    if hasattr(server, "call_tool"):
        res = server.call_tool(tool_name, args)
        if inspect.isawaitable(res):
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
        res = fn(**args)
        if inspect.isawaitable(res):
            res = await res
        return res if isinstance(res, dict) else {}

    raise ValueError(f"Server does not support tool '{tool_name}'")
