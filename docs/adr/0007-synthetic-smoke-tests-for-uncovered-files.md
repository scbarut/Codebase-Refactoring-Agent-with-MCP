# Synthetic smoke tests for uncovered files in self-healing loop

Files without existing test coverage in a user repository present a critical blind spot during migration: without test failures, the Self-Healing Loop is bypassed entirely, leaving runtime syntax errors and broken imports undetected.

We synthesize an ephemeral Synthetic Test (a contract and smoke test exercising imports, model/class instantiations, and method signatures) inside the Sandbox when no existing test file is discovered.

We considered:
1. Skipping tests for uncovered files: Safe from flakiness, but completely disables the Self-Healing Loop and silently accepts broken migrations.
2. Full LLM behavioral unit test generation: Prone to hallucinating business rules, over-mocking, and "grading its own homework".
3. Synthetic smoke tests: Focused strictly on structural execution viability (importability, schema instantiation, method invocation).

By default, the Synthetic Test runs ephemerally in the Sandbox to drive the Self-Healing Loop and is deleted before git commit unless the developer opts in to persist it via the HITL Gateway. The Migration Plan displays an explicit `Coverage Status` (`VERIFIED`, `SYNTHETIC`, or `UNCOVERED`) to maintain transparency without silently lowering calculated risk tiers.

### Implementation Architecture
1. **Explicit Graph Node**: `generate_synthetic_test` is integrated as an explicit LangGraph node in the File Sub-graph between rewriting and `run_tests`, ensuring streaming visibility.
2. **Signature Grounding**: Prompt context is grounded with AST signatures from `mcp-server-ast` (`extract_signatures`), preventing hallucinated constructors or method arguments.
3. **Disambiguation Mechanics**: If a Synthetic Test fails, the traceback's failing frame is parsed. Errors in caller frames (test setup / harness parameters) allow up to 1 test fixture healing attempt; errors inside callee frames (migrated module logic) route directly to source self-healing.
4. **Placement & Cleanup**: Placed as `_tmp_smoke_test_<stem>.py` in the workspace root (valid Python module name avoiding pytest relative-import errors), executed via scoped pytest in the Sandbox, and cleaned up during `finalize` unless persistence was selected.
