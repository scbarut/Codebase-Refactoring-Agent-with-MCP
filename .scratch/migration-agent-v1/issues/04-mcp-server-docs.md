# 04: mcp-server-docs — corpus lookup and web fallback

**What to build:** An MCP server (stdio transport) that retrieves documentation relevant to a migration error or pattern. Primary source: flat-file lookup via `doc_ref` pointing to a specific section in the pre-indexed Doc Corpus. Secondary: BM25 keyword search over the entire corpus when no `doc_ref` is available. Fallback: constrained web search via Tavily API or DuckDuckGo + Trafilatura, returning top-3 relevant chunks with a total budget of ≤1500 tokens, matched against the specific test traceback. No headless browsers. Tested at the MCP tool interface seam.

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

- [x] MCP server registered with stdio transport, discoverable by the agent
- [x] `lookup_doc_ref` tool: given a `doc_ref` string (e.g., `pydantic-v2-migration.md#validator-to-field-validator`), returns the referenced section content from the Doc Corpus
- [x] `search_corpus` tool: given a query string, performs BM25 keyword search over all Doc Corpus markdown files and returns the top-k relevant chunks
- [x] `web_search` tool: given an error traceback or query, performs a constrained web search (Tavily or DuckDuckGo + Trafilatura), returns top-3 chunks within ≤1500 tokens total, as clean markdown without HTML artifacts
- [x] Doc Corpus directory structure: `src/docs_corpus/<target-library>/` with markdown files
- [x] Tests verify flat-file lookup returns exact sections
- [x] Tests verify BM25 search ranks relevant chunks higher than irrelevant ones
- [x] Tests verify web fallback respects the 1500-token budget and returns clean markdown
