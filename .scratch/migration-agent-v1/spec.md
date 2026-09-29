# Autonomous Codebase Refactoring & Migration Agent — v1 Spec

Status: ready-for-agent

## Problem Statement

Developers migrating legacy Python codebases across breaking library versions (e.g., Pydantic v1 → v2, SQLAlchemy 1.4 → 2.0) face three compounding problems: multi-file repositories exhaust LLM context windows, foundation models lack awareness of recent API breaking changes, and autonomous code modification without sandboxed verification risks introducing silent regressions. Manual migration is repetitive, error-prone, and blocks upgrades for months.

## Solution

An agent that takes a Python codebase (local directory or GitHub URL), statically scans it to identify deprecated library usages, presents a structured Migration Plan with per-file risk levels for human review, rewrites approved files using a hybrid rule engine (declarative rules for known patterns, LLM fallback for novel ones), self-heals test failures in a per-job Docker Sandbox (capped at 3 attempts), and presents the results as a Monaco split-diff view with copy-pasteable git commands to apply the changes.

The system is composed of:
- A **LangGraph Orchestration Graph** driving the full lifecycle with a nested **File Sub-graph** per file for episodic context isolation.
- Three **MCP Servers** (`mcp-server-ast`, `mcp-server-docs`, `mcp-server-git`) providing tool capabilities via stdio transport.
- A **FastAPI** backend exposing REST + WebSocket endpoints.
- A **Next.js** frontend with four pages (Dashboard, Plan Review, Live Progress, Results).
- A **PostgreSQL** checkpointer for HITL Gateway state persistence.
- A **Docker Sandbox** per job for safe test execution.

## User Stories

1. As a developer, I want to submit a local Python project directory for migration, so that I don't have to push it to GitHub first.

2. As a developer, I want to submit a GitHub repository URL for migration, so that the agent clones and works on it in an isolated Agent Workspace without touching my local copy.

3. As a developer, I want to select a Target Library transition (e.g., "Pydantic v1 → v2") when submitting a Migration Job, so that the agent knows what to scan for.

4. As a developer, I want the agent to statically scan my codebase and produce a Migration Plan listing every affected file, the specific AST nodes to change, and a risk level per file, so that I can understand the scope before anything is modified.

5. As a developer, I want to see risk levels (`LOW`, `MEDIUM`, `HIGH`) per file in the Migration Plan, determined by the Migration Rule's base risk and bumped up if the file has no test coverage, so that I can prioritize my review.

6. As a developer, I want to deselect specific files from the Migration Plan before approving, so that I can exclude sensitive or out-of-scope files from automated modification.

7. As a developer, I want the agent to pause at a HITL Gateway and wait for my explicit approval before modifying any code, so that I maintain control over what gets changed.

8. As a developer, I want the agent to apply declarative Migration Rules (via libcst transformers) for well-known patterns without making an LLM call, so that common rewrites are fast, deterministic, and cheap.

9. As a developer, I want the agent to fall back to an LLM (via LiteLLM with Gemini) for deprecated patterns that don't match any declarative rule, using context from the Doc Corpus, so that novel patterns are still handled.

10. As a developer, I want the agent to use `gemini-flash-lite` for LOW-risk rewrites and `gemini-flash` for MEDIUM/HIGH-risk or unmatched patterns, so that simple transforms are fast and cheap while complex reasoning gets a more capable model.

11. As a developer, I want each file's rewrite to run in its own File Sub-graph with isolated context, so that test failures and healing attempts from one file don't pollute another file's LLM prompt.

12. As a developer, I want the agent to run scoped unit tests in a per-job Docker Sandbox targeting only the modified module, so that test execution is safe and isolated from my host machine.

13. As a developer, I want the agent to self-heal test failures by extracting the traceback, querying `mcp-server-docs` for relevant documentation, patching the code, and re-running tests — up to 3 attempts, so that many test failures are resolved without my intervention.

14. As a developer, I want files that fail all 3 self-healing attempts to be marked as `FAILED` with the last traceback attached, while the agent continues migrating remaining files, so that one stubborn file doesn't block the entire migration.

15. As a developer, I want to see a real-time streaming log in the web UI during migration, showing which file is being processed, test results, and healing attempts, so that I can monitor progress without polling.

16. As a developer, I want the final results presented as a Monaco split-diff view per file with color-coded changes, so that I can review exactly what was modified before applying.

17. As a developer, I want the agent to output copy-pasteable git commands I can run locally to apply the migration changes from the isolated Agent Workspace to my repository, so that my original code is never modified without my explicit action.

18. As a developer, I want an optional one-click PR trigger for GitHub-sourced repositories, so that I can create a pull request directly from the results view.

19. As a developer, I want to configure my Gemini API key via a `.env` file and system preferences (workspace path, model selection, max healing attempts) via a `config.yaml`, so that secrets and preferences are managed separately.

20. As a developer, I want to start the entire stack with a single CLI command (`migration-agent start`), which boots the docker-compose stack and opens the browser to the web dashboard, so that setup is one step.

21. As a developer, I want to submit migration jobs directly from the CLI (`migration-agent run ./path --target pydantic:v2`), so that I can script and automate migrations.

22. As a developer, I want to see a dashboard listing all my Migration Jobs with status badges (scanning, awaiting approval, migrating, complete, failed), so that I can track multiple jobs.

23. As a developer, I want `mcp-server-docs` to serve pre-indexed markdown migration guides as the primary documentation source, with a lightweight web fallback (Tavily API or DuckDuckGo + Trafilatura) returning top-3 chunks (≤1500 tokens) for edge cases, so that documentation retrieval is fast and context-lean.

24. As a developer, I want `mcp-server-ast` to use libcst for parsing, extracting function signatures, and replacing AST nodes deterministically, so that rewrites preserve my code's formatting and comments.

25. As a developer, I want `mcp-server-git` to handle repository cloning, branch management (`migrate/<target-library>-<timestamp>`), structured diff generation, and commit bundling in the Agent Workspace, so that git operations are automated and isolated.

26. As a developer, I want the Sandbox base images (Python 3.9–3.12 with pytest and coverage pre-installed) to be available from `ghcr.io` for instant use, or buildable locally via included Dockerfiles for offline/custom scenarios, so that first-run is fast and customization is possible.

27. As a developer, I want structured JSON logging (via structlog) for the application layer and optional LangSmith integration (opt-in via env var) for LLM/graph tracing, so that I can debug issues at both the system and AI reasoning layers.

28. As a developer, I want the system to work with Docker socket mount for spawning Sandbox containers, with the README documenting the security implications and recommending Podman as a rootless alternative, so that I can make an informed security choice.

## Implementation Decisions

- **Python-only for v1.** Multi-language support deferred — it requires separate AST engines, doc corpora, and sandbox runtimes per language.

- **libcst for AST parsing and rewriting.** Python's built-in `ast` module is used only as a fast pre-filter to identify files that import the Target Library. All source-to-source transformations use libcst to preserve formatting and comments.

- **Hybrid rule engine (ADR-0001).** Declarative Migration Rules in YAML handle known patterns deterministically. Complex rules reference Python `CSTTransformer` classes by dotted path. LLM fallback (via LiteLLM) handles unmatched patterns using Doc Corpus context.

- **Tiered model routing (ADR-0001).** `gemini-flash-lite` for LOW-risk rewrites (simple renames, argument reordering). `gemini-flash` for MEDIUM/HIGH-risk or no-rule-match rewrites. Routing decision is per-Rewrite based on the Migration Rule's risk category.

- **Nested LangGraph sub-graphs (ADR-0002).** The Orchestration Graph drives the job lifecycle. Each approved file gets its own File Sub-graph (rewrite → test → Self-Healing Loop) for episodic context isolation. Results are aggregated back into the Orchestration Graph state.

- **PostgreSQL checkpointer (ADR-0004).** State persistence via `PostgresSaver` in a docker-compose-managed PostgreSQL instance. Enables crash recovery at the HITL Gateway and future multi-user concurrency.

- **Isolated Agent Workspace (ADR-0005).** The agent clones/copies the codebase to `~/.migration-agent/workspaces/<repo-name>-<timestamp>/`. User's original repository is never modified. Output is copy-pasteable git commands + optional PR trigger.

- **Pre-indexed Doc Corpus with constrained web fallback (ADR-0003).** Each Migration Rule carries a `doc_ref` pointing to a specific markdown section (flat file lookup). BM25 keyword search for the LLM-fallback case. Web fallback via Tavily/DDG+Trafilatura, top-3 chunks, ≤1500 tokens.

- **Per-job Docker Sandbox.** Fresh container per Migration Job. Pre-built base images (Python 3.9–3.12, pytest, coverage) from `ghcr.io`. Project dependencies installed via `pip install` at runtime from detected `requirements.txt` / `pyproject.toml` / `setup.py`.

- **MCP servers as stdio child processes.** `mcp-server-ast`, `mcp-server-docs`, and `mcp-server-git` run as child processes of the FastAPI api service. Streamable HTTP transport available for production deployment via config switch.

- **Monorepo layout:**
  ```
  src/
  ├── core/          # LangGraph graphs, rule engine, state schemas
  ├── mcp_servers/   # ast, docs, git servers
  ├── api/           # FastAPI endpoints + WebSocket
  ├── rules/         # YAML rule sets + Python transformers
  ├── docs_corpus/   # Pre-indexed migration guides
  web/               # Next.js frontend
  tests/
  docker/            # Dockerfiles for sandbox
  ```

- **uv for dependency management.** `pyproject.toml` + `uv.lock` for deterministic builds.

- **REST + WebSocket API.** REST for CRUD operations (6 endpoints). WebSocket per job for real-time streaming.

- **4-page Next.js frontend.** Dashboard, Plan Review (with file deselection + approve), Live Progress (WebSocket log stream), Results (Monaco split-diff + git commands).

- **CLI launcher + scriptable run.** `migration-agent start` boots docker-compose and opens browser. `migration-agent run` submits jobs directly.

- **`.env` for secrets, `config.yaml` for preferences.** Ship `.env.example` and `config.example.yaml`. Config values overridable via CLI flags.

- **structlog (JSON) + opt-in LangSmith.** Application logging via structlog. LLM/graph tracing via LangSmith when `LANGSMITH_API_KEY` is set.

- **Pydantic v1 → v2 as the first Migration Rule Set.** Proof-of-concept with high mechanical-rewrite ratio and excellent official migration guide.

## Testing Decisions

- **Good tests test external behavior at seam boundaries**, not implementation internals. A test should break only when the system's observable behavior changes, never when you refactor internals.

- **Primary seam — Orchestration Graph boundary.** Input: `MigrationJobConfig` (codebase path, Target Library). Output: `MigrationResult` (list of per-file results with diffs, statuses, tracebacks for failures). MCP servers and the LLM client are injected dependencies, stubbed in tests. This seam validates the full migration logic: scan → plan → rewrite → test → heal → aggregate.

- **Supporting seam — MCP server tool interface.** Each MCP server tool is tested independently: call with inputs, verify outputs. `mcp-server-ast`: give it source code, verify parsed signatures and rewritten output. `mcp-server-docs`: give it a query, verify returned chunks. `mcp-server-git`: give it a repo path, verify branch creation, diff output.

- **Supporting seam — FastAPI HTTP boundary.** Integration tests call REST endpoints and verify response contracts (status codes, JSON schemas). The LangGraph graph is injected and stubbed for fast execution.

- **No frontend E2E tests for v1.** The Next.js frontend is tested manually. E2E framework deferred to v2.

- **Sandbox tests require Docker.** Tests that exercise the actual Docker Sandbox are marked and run only in CI or when Docker is available. Unit tests of the sandbox management code stub the Docker client.

## Out of Scope

- **Multi-language support.** Only Python codebases for v1.
- **Authentication and multi-tenancy.** Single-user, localhost-only for v1.
- **Custom user-authored Migration Rule Sets.** v1 ships only the Pydantic v1 → v2 rule set. User-extensible rule sets deferred.
- **Parallel file processing.** File Sub-graphs run sequentially in v1. Parallelization is an architectural option (ADR-0002) but not implemented.
- **Full-suite test execution.** The Sandbox runs only scoped unit tests targeting the modified module, not the full test suite.
- **Frontend E2E testing.** Manual testing only for v1.
- **CI/CD pipeline.** Not included in v1 scope.
- **SQLAlchemy and other Migration Rule Sets.** Only Pydantic v1 → v2 ships with v1.

## Further Notes

- The architecture is designed for extensibility: adding a new Target Library migration means adding a YAML rule file, optional Python transformers, and a Doc Corpus directory. No core agent changes needed.
- The Docker socket security risk is accepted for v1 (localhost-only tool). Podman is documented as the rootless alternative.
- LangSmith tracing is opt-in and imposes no dependency when the API key is unset.
- The Pydantic v1 → v2 migration was chosen as the first rule set because it has the highest ratio of mechanically-rewritable patterns (decorators, config classes, import paths) and the best official migration guide for populating the Doc Corpus.
