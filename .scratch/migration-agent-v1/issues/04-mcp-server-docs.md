# 04: mcp-server-docs — corpus lookup and web fallback

**What to build:** An MCP server (stdio transport) that retrieves documentation relevant to a migration error or pattern. Primary source: flat-file lookup via `doc_ref` pointing to a specific section in the pre-indexed Doc Corpus. Secondary: BM25 keyword search over the entire corpus when no `doc_ref` is available. Fallback: constrained web search via Tavily API or DuckDuckGo + Trafilatura, returning top-3 relevant chunks with a total budget of ≤1500 tokens, matched against the specific test traceback. No headless browsers. Tested at the MCP tool interface seam.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] MCP server registered with stdio transport, discoverable by the agent
- [x] `lookup_doc_ref` tool: given a `doc_ref` string (e.g., `pydantic-v2-migration.md#validator-to-field-validator`), returns the referenced section content from the Doc Corpus
- [x] `search_corpus` tool: given a query string, performs BM25 keyword search over all Doc Corpus markdown files and returns the top-k relevant chunks
- [x] `web_search` tool: given an error traceback or query, performs a constrained web search (Tavily or DuckDuckGo + Trafilatura), returns top-3 chunks within ≤1500 tokens total, as clean markdown without HTML artifacts
- [x] Doc Corpus directory structure: `src/docs_corpus/<target-library>/` with markdown files
- [x] Tests verify flat-file lookup returns exact sections
- [x] Tests verify BM25 search ranks relevant chunks higher than irrelevant ones
- [x] Tests verify web fallback respects the 1500-token budget and returns clean markdown

## Implementation Notes

- Implemented `mcp-server-docs` server in `src/mcp_servers/docs_server.py` using `mcp.server.mcpserver.MCPServer` with stdio transport.
- Pre-indexed Pydantic V1 -> V2 migration documentation in `src/docs_corpus/pydantic/pydantic-v2-migration.md`.
- Implemented `lookup_doc_ref` supporting exact markdown heading section extraction without cross-section bleeding.
- Implemented `search_corpus` using in-memory Okapi BM25 keyword ranking over section-level markdown chunks.
- Implemented `web_search` with Tavily API integration and DuckDuckGo fallback, HTML tag and entity stripping, and strict `<= 1500` token budget truncation.
- Registered CLI script entrypoint `mcp-server-docs` in `pyproject.toml` and CLI command `migration-agent mcp-docs` in `src/cli.py`.
- Tested at the MCP tool interface seam in `tests/test_mcp_docs.py` (13 unit tests passed).
