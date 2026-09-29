# 08: File Sub-graph — rewrite, test, Self-Healing Loop

**What to build:** The nested LangGraph File Sub-graph that processes a single file. Given a file path, its matched Migration Rules, and access to MCP servers + Sandbox, it: applies declarative rules via `mcp-server-ast` (or falls back to LLM via LiteLLM for unmatched patterns, using `mcp-server-docs` for context), runs scoped tests in the Sandbox, and if tests fail, enters the Self-Healing Loop (extract traceback → query `mcp-server-docs` → patch → re-test, capped at 3 attempts). Model routing: `gemini-flash-lite` for LOW-risk Rewrites, `gemini-flash` for MEDIUM/HIGH or no-rule-match. Returns a per-file result: `SUCCESS` with diff, or `FAILED` with last traceback. Tested at the graph boundary with stubbed MCP servers and LLM client.

**Blocked by:** 03 (mcp-server-ast), 04 (mcp-server-docs), 06 (sandbox manager)

**Status:** ready-for-agent

- [ ] LangGraph `StateGraph` with nodes: `apply_rules`, `llm_fallback`, `run_tests`, `extract_traceback`, `query_docs`, `patch_code`, `finalize`
- [ ] `apply_rules` node: for each matched Migration Rule, calls `mcp-server-ast` `apply_transform` with the rule's transformer. Unmatched patterns flagged for LLM fallback
- [ ] `llm_fallback` node: for unmatched patterns, sends the AST node context + Doc Corpus chunk to LiteLLM. Routes to `gemini-flash-lite` (LOW risk) or `gemini-flash` (MEDIUM/HIGH/unmatched) based on the rule's risk category
- [ ] `run_tests` node: calls `SandboxManager.run_tests()` targeting the modified module
- [ ] Self-Healing Loop: on test failure, `extract_traceback` → `query_docs` (via `mcp-server-docs`, matched against the traceback) → `patch_code` (LLM generates a fix) → `run_tests` again. Loop counter capped at 3
- [ ] `finalize` node: returns `FileResult` with status (`SUCCESS`/`FAILED`), diff (if success), traceback (if failed), attempt count
- [ ] Episodic context isolation: the sub-graph's state is self-contained, no leakage to/from other files
- [ ] Tests verify: successful rewrite with passing tests, self-healing that fixes a test failure, FAILED status after 3 failed attempts
