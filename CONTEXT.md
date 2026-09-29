# Autonomous Codebase Refactoring & Migration Agent

An agent that ingests a Python codebase, identifies deprecated library usages, proposes a migration plan for human approval, rewrites the code, and self-heals test failures in a sandboxed environment.

## Language

**Migration Job**:
The top-level unit of work: one user-submitted codebase being migrated from one library version to another.
_Avoid_: Run, session, task

**Migration Plan**:
A structured report produced by static analysis before any code is modified. Lists every affected file, the AST nodes to change, and a risk level per file. The human approves or prunes this before the agent rewrites anything.
_Avoid_: Proposal, report

**Target Library**:
The specific library (and version transition) being migrated, e.g. "Pydantic v1 → v2".
_Avoid_: Dependency, package

**Rewrite**:
A single code transformation applied to one AST node or symbol within a file. The smallest unit of migration work.
_Avoid_: Patch, fix, edit

**Sandbox**:
A per-job Docker container where test execution happens. Destroyed after the job completes. No host-side code execution.
_Avoid_: Environment, runner

**Self-Healing Loop**:
The iterative cycle (capped at 3 attempts) where the agent runs tests, reads failure tracebacks, queries documentation, patches the code, and re-runs. Operates on one file at a time.
_Avoid_: Retry loop, auto-fix

**HITL Gateway**:
A LangGraph interrupt point where execution pauses and the human reviews the Migration Plan. The agent resumes only after explicit approval.
_Avoid_: Approval step, checkpoint

**MCP Server**:
An external tool process exposing capabilities via the Model Context Protocol. Three in this system: `mcp-server-ast`, `mcp-server-docs`, `mcp-server-git`.
_Avoid_: Tool server, plugin

**Migration Rule**:
A declarative pattern mapping that describes one specific code transformation: an old AST pattern, the new replacement, and a base risk category (`LOW`, `MEDIUM`, `HIGH`). Applied via `libcst` transformers without an LLM call.
_Avoid_: Transform, pattern

**Migration Rule Set**:
A collection of Migration Rules for one Target Library transition (e.g., all rules for Pydantic v1 → v2). Ships as a structured file alongside a pre-indexed Doc Corpus for that library.
_Avoid_: Rule pack, migration config

**Doc Corpus**:
Pre-indexed markdown files containing official migration guides and API documentation for a Target Library. The primary source for `mcp-server-docs`. Supplemented by lightweight web search fallback (Tavily / DuckDuckGo + Trafilatura) when a pattern isn't covered.
_Avoid_: Knowledge base, docs index

**Orchestration Graph**:
The top-level LangGraph state machine that drives the full Migration Job lifecycle: ingest → scan → plan → HITL Gateway → rewrite → aggregate results.
_Avoid_: Main graph, workflow

**File Sub-graph**:
A per-file LangGraph sub-graph spawned by the Orchestration Graph for the rewrite → test → Self-Healing Loop cycle. Maintains episodic context isolation: test failures from one file never leak into another file's prompt.
_Avoid_: Child graph, file processor

**Agent Workspace**:
An isolated directory where the agent clones or copies the user's codebase and performs all modifications. Located at `~/.migration-agent/workspaces/<repo-name>-<timestamp>/`. The user's original repository is never modified in-place. After migration, the agent outputs copy-pasteable git commands (and optional PR trigger) for the user to apply changes.
_Avoid_: Working directory, temp dir

