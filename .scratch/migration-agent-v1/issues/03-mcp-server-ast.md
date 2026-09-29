# 03: mcp-server-ast — parse and rewrite with libcst

**What to build:** An MCP server (stdio transport) that exposes AST operations for Python source code. It can scan a directory to find all files importing a Target Library (fast pre-filter using Python's built-in `ast` module), extract function/class signatures from a file via libcst, and apply a `CSTTransformer` (referenced by dotted path) to rewrite AST nodes — preserving the original file's formatting, whitespace, and comments. Tested at the MCP tool interface seam.

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

- [x] MCP server registered with stdio transport, discoverable by the agent
- [x] `scan_imports` tool: given a directory path and a target library name (e.g., "pydantic"), returns a list of files that import it (using Python `ast` for speed)
- [x] `extract_signatures` tool: given a file path, returns a structured list of function and class signatures (names, decorators, base classes, arguments) via libcst
- [x] `apply_transform` tool: given a file path and a `CSTTransformer` class (referenced by dotted Python path), applies the transformation and returns the rewritten source code with formatting preserved
- [x] `get_node_context` tool: given a file path and a symbol name, returns the source code of that specific AST node (function, class) for isolated LLM processing
- [x] Tests verify that formatting, comments, and whitespace are preserved through a round-trip parse → transform → write
- [x] Tests verify the fast `ast` pre-filter correctly identifies files importing the target library
