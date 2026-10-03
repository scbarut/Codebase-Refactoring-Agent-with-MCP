# Autonomous Codebase Refactoring & Migration Agent with MCP

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/LangGraph-0.2%2B-orange?logo=langchain&logoColor=white" alt="LangGraph"/>
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/Next.js-14-black?logo=next.js&logoColor=white" alt="Next.js 14"/>
  <img src="https://img.shields.io/badge/Model_Context_Protocol-MCP-blueviolet" alt="Model Context Protocol"/>
  <img src="https://img.shields.io/badge/Docker-Sandboxed-2496ED?logo=docker&logoColor=white" alt="Docker Sandboxed"/>
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"/>
</p>

An enterprise-grade autonomous engineering agent that ingests legacy Python codebases, identifies deprecated patterns, plans migrations with **Human-in-the-Loop (HITL)** approval, transforms code with deterministic **LibCST** syntax transformers and **tiered LLMs**, and autonomously **self-heals** regressions inside isolated **Docker sandboxes**.

---

## Table of Contents

- [Overview & Technology Stack](#overview--technology-stack)
- [Agent Architecture & Workflow](#agent-architecture--workflow)
- [Supported Migration Targets](#supported-migration-targets)
- [Quick Start (Docker Compose)](#quick-start-docker-compose)
- [Testing with Bundled `temprepo`](#testing-with-bundled-temprepo)
- [Local Development Setup](#local-development-setup)
- [CLI & API Reference](#cli--api-reference)
- [Configuration & Model Routing](#configuration--model-routing)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Running Tests & License](#running-tests--license)

---

## Overview & Technology Stack

Legacy migrations (e.g., **Pydantic v1 → v2**, **SQLAlchemy 1.4 → 2.0**) are risky when done by simple LLM prompting due to hallucinations, token limits, and lack of real runtime feedback. This agent solves this through a deterministic-first, sandboxed architecture:

| Layer | Stack | Key Responsibilities |
| :--- | :--- | :--- |
| **Agent & State Machine** | **LangGraph**, **PostgreSQL** | Macro Orchestration Graph with persistent `interrupt()` HITL checkpoints and episodic per-file sub-graphs. |
| **Model Gateway (LLM)** | **LiteLLM** (Gemini, Claude, GPT, Groq, Ollama) | Tiered routing: `model_lite` for low-risk transforms, `model_default` for semantic fallback and patch reasoning. |
| **AST Transformation** | **LibCST**, Python `ast` | Deterministic, lossless syntax transformation preserving comments, formatting, and whitespace. |
| **Tool Protocols (MCP)** | **Model Context Protocol** | Decoupled tool servers (`mcp-server-ast`, `mcp-server-docs`, `mcp-server-git`) over stdio JSON-RPC. |
| **Backend & Real-Time** | **FastAPI**, **Uvicorn**, **WebSockets** | REST job management and real-time streaming progress logs. |
| **Frontend UI** | **Next.js 14**, **React 18**, **Monaco Editor** | Interactive dashboard with plan approval, live logs, and split-screen code diffs. |
| **Sandboxing & Runtime** | **Docker Engine**, **Docker Compose** | Ephemeral Python containers (3.9–3.12) running isolated `pytest` verification suites. |
| **CLI & Tooling** | **uv**, **Click**, **Pytest**, **Ruff** | Unified CLI (`migration-agent`), test automation, and code formatting. |

---

## Agent Architecture & Workflow

The system is organized into two primary LangGraph state machines: the top-level **Orchestration Graph** (managing the macro lifecycle) and the episodic **File Sub-graph** (executing per-file refactoring, sandboxing, and self-healing).

### 1. Macro Orchestration Graph (Top-Level Lifecycle)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                   1. ORCHESTRATION GRAPH (MACRO LIFECYCLE)                   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                               [ User Request ]
                         (Local Path or Git Repo URL)
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. INGEST CODEBASE                                                           │
│    • Clone repository or copy local directory into an Agent Workspace        │
│    • Preserves original user codebase untouched (ADR-0005)                   │
│    • Tool: mcp-server-git                                                    │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. SCAN AST & DEPENDENCIES                                                   │
│    • Statically inspect imports and parse syntax trees                       │
│    • Detect all files importing the Target Library (e.g. Pydantic, SQLAlchemy)│
│    • Tool: mcp-server-ast                                                    │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. BUILD MIGRATION PLAN                                                      │
│    • Match AST nodes against declarative YAML Migration Rules                │
│    • Calculate risk ratings per file: LOW (safe) | MEDIUM | HIGH (breaking)   │
│    • Generate structured plan with symbol-level targets                      │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
════════════════════════════════════════════════════════════════════════════════
║                      4. HITL GATEWAY (PAUSE & REVIEW)                        ║
║  • LangGraph interrupt() halts execution and persists thread in PostgreSQL   ║
║  • Developer inspects affected files, diffs, and risk scores in Web UI       ║
║  • Developer deselects sensitive files and explicitly approves migration     ║
════════════════════════════════════════════════════════════════════════════════
                  │                                         │
       [ Approved File List ]                      [ Cancel / Reject ]
                  │                                         │
                  ▼                                         ▼
┌──────────────────────────────────────────┐    ┌──────────────────────────────┐
│ 5. RESUME FROM HITL                      │    │            ABORT             │
│    • Resume execution with approved files│    │ • Job marked as cancelled    │
│    • Create dedicated git branch         │    │ • State saved in database    │
└─────────────────────┬────────────────────┘    └──────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 6. DISPATCH FILE SUB-GRAPHS                                                  │
│    • Spawn episodic, isolated File Sub-graphs for each approved file         │
│    • Context Isolation: Test failures in file A never pollute prompt of B    │
└─────────────────────┬────────────────────────────────────────────────────────┘
                      │
                      ▼  (Dispatches per-file tasks)
┌──────────────────────────────────────────────────────────────────────────────┐
│                [ NESTED FILE SUB-GRAPH & SELF-HEALING LOOP ]                 │
│         • LibCST AST Transforms  • LLM Fallback  • Docker Sandboxes          │
└─────────────────────┬────────────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7. AGGREGATE RESULTS                                                         │
│    • Collect verified contents, unified diffs, test telemetry, and logs      │
│    • Compile unified MigrationResult object                                  │
└─────────────────────┬────────────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 8. COMMIT & OUTPUT                                                           │
│    • Commit refactored code to the migration branch via mcp-server-git       │
│    • Generate copy-pasteable `git apply` commands and diff report            │
└─────────────────────┬────────────────────────────────────────────────────────┘
                      │
                      ▼
           [ Migration Completed Successfully ✓ ]
```

---

### 2. File Sub-graph & Autonomous Self-Healing Loop

```
┌──────────────────────────────────────────────────────────────────────────────┐
│               2. FILE SUB-GRAPH & AUTONOMOUS SELF-HEALING LOOP               │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                     [ File Dispatched from Orchestrator ]
                                       │
                                       ▼
                           /───────────────────────\
                          <  Rule Match Available?  >
                           \───────────────────────/
                                  │         │
                        (YES)     │         │     (NO)
                                  ▼         ▼
             ┌─────────────────────────┐   ┌─────────────────────────┐
             │ apply_rules (LibCST)    │   │ llm_fallback (LiteLLM)  │
             │ Deterministic AST       │   │ Tiered Model Fallback   │
             │ Transform (Zero LLM)    │   │ (model_lite / default)  │
             └────────────┬────────────┘   └────────────┬────────────┘
                          │                             │
                          └──────────────┬──────────────┘
                                         │
                                         ▼
                             /───────────────────────\
                            <  Existing Tests Found?  >
                             \───────────────────────/
                                    │         │
                           (NO)     │         │     (YES)
                                    ▼         │
               ┌───────────────────────────┐  │
               │ generate_synthetic_test   │  │
               │ Synthesize smoke/contract │  │
               │ tests for uncovered file  │  │
               └─────────────┬─────────────┘  │
                             │                │
                             └────────┬───────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ run_tests: EXECUTE IN ISOLATED DOCKER SANDBOX                                │
│    • Mounts workspace into ephemeral multi-version Python container          │
│    • Runs targeted pytest suite (zero host execution risk)                   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
                            /─────────────────────\
                           <    All Tests Pass?    >
                            \─────────────────────/
                                  │         │
                         (YES)    │         │     (NO)
                                  ▼         ▼
                                  │   /───────────────────────\
                                  │  <  Attempts < Max (3)?    >
                                  │   \───────────────────────/
                                  │             │         │
                                  │     (YES)   │         │  (NO - Exhausted)
                                  │             ▼         ▼
                                  │      ┌─────────────┐ ┌─────────────────────┐
                                  │      │extract_trace│ │ Mark File Migration │
                                  │      │back (stderr)│ │     as FAILED       │
                                  │      └──────┬──────┘ └──────────┬──────────┘
                                  │             │                   │
                                  │             ▼                   │
                                  │      ┌─────────────┐            │
                                  │      │ query_docs  │            │
                                  │      │mcp-docs+web │            │
                                  │      └──────┬──────┘            │
                                  │             │                   │
                                  │             ▼                   │
                                  │      ┌─────────────┐            │
                                  │      │ patch_code  │            │
                                  │      │LLM targeted │            │
                                  │      │code patch   │            │
                                  │      └──────┬──────┘            │
                                  │             │                   │
                                  │             └─────────┐         │
                                  │ (Self-Healing Loop)   │         │
                                  │                       ▼         │
                                  │               [ Re-Run Tests ]  │
                                  │                                 │
                                  ▼                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ finalize                                                                     │
│    • Save verified code, compute unified diff, record execution status       │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
                     [ Return FileResult to Orchestrator ]
```

---

### 3. End-to-End System & Platform Topology

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         3. END-TO-END SYSTEM TOPOLOGY                        │
└──────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────┐
 │                         CLIENT & PRESENTATION LAYER                        │
 │  • Next.js 14 Dashboard (Port 3000)   • CLI: migration-agent (Python/Click)│
 │  • Monaco Split-Diff Editor           • Real-time WebSocket Status Stream  │
 └──────────────────────┬──────────────────────────────────────┬──────────────┘
                        │ HTTP REST Requests                   │ WebSocket
                        ▼                                      ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │                             FASTAPI API SERVICE                            │
 │  • Asynchronous Job Manager           • Background Tasks & Event Dispatcher│
 │  • LangGraph State Machine Engine     • Structured JSON Logging (structlog)│
 └──────┬───────────────────────┬──────────────────────┬────────────────┬─────┘
        │                       │                      │                │
        │ stdio JSON-RPC        │                      │                │
        ▼                       ▼                      ▼                ▼
 ┌───────────────┐      ┌───────────────┐      ┌───────────────┐ ┌────────────┐
 │  MCP SERVERS  │      │  POSTGRESQL   │      │ DOCKER ENGINE │ │  LiteLLM   │
 │ • git_server  │      │ (Checkpointer)│      │  (Sandboxes)  │ │ (Routing)  │
 │ • ast_server  │      │ • Thread state│      │ • Pytest runs │ │ • Gemini   │
 │ • docs_server │      │ • HITL pause  │      │ • Ephemeral   │ │ • Claude   │
 └───────────────┘      └───────────────┘      └───────────────┘ └────────────┘
```

---

## Supported Migration Targets

| Target Library | Transition | Highlights of Automated Transforms |
| :--- | :--- | :--- |
| **`pydantic`** | `v1 → v2` | `@validator` → `@field_validator`, `@root_validator` → `@model_validator`, `class Config` → `ConfigDict`, `.dict()` → `.model_dump()`, `.parse_obj()` → `.model_validate()`, `BaseSettings` → `pydantic-settings`. |
| **`sqlalchemy`** | `1.4 → 2.0` | `declarative_base()` → `orm.declarative_base()`, `session.query(M)` → `session.scalars(select(M))`, `Query.get()` → `session.get()`. |
| **`celery`** | `4 → 5` | Deprecated `@app.task` → `@shared_task`, uppercase settings modernization. |
| **`requests`** | Modernization | Enforce explicit timeouts (`timeout=...`), `data=json.dumps(...)` → `json=...`. |

*Custom rules can be added by placing YAML definitions in `src/rules/` and reference guides in `src/docs_corpus/`.*

---

## Quick Start (Docker Compose)

The fastest zero-dependency way to run the entire platform:

### 1. Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (daemon running)
- **LLM API Key**: Google Gemini API key by default (`GEMINI_API_KEY`), or OpenAI / Anthropic / Groq / Ollama.
- **Tavily API Key (Optional)**: `TAVILY_API_KEY` for live web search documentation retrieval when an error or pattern is not found in the local corpus.

### 2. Setup Environment
```bash
git clone https://github.com/your-username/Autonomous-Codebase-Refactoring-Migration-Agent-with-MCP.git
cd Autonomous-Codebase-Refactoring-Migration-Agent-with-MCP

cp .env.example .env
cp config.example.yaml config.yaml
```
Edit `.env` to provide your secret API keys:
```env
# Primary LLM API Key (Default is Google Gemini)
GEMINI_API_KEY=your_gemini_api_key_here

# Documentation Web Search Fallback (Optional, but recommended)
TAVILY_API_KEY=your_tavily_api_key_here
```

### 3. Launch Services
```bash
docker compose up --build -d
```
- **Web Dashboard**: [http://localhost:3000](http://localhost:3000)
- **API Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

*(To stop: `docker compose down` | View logs: `docker compose logs -f`)*

---

## Testing with Bundled `temprepo`

This repository includes a ready-to-migrate sample codebase at [`./temprepo`](temprepo):

- **Via Web UI**: Open [http://localhost:3000](http://localhost:3000) $\to$ **New Migration Job** $\to$ Target Path: `./temprepo`, Target: `pydantic` $\to$ Review plan $\to$ Approve files $\to$ Inspect live Monaco diff!
- **Via CLI**:
  ```bash
  uv run migration-agent run ./temprepo --target pydantic --wait
  ```

---

## Local Development Setup

To run outside Docker containers for local development:

```bash
# 1. Start PostgreSQL checkpointer
docker compose up postgres -d

# 2. Python environment & dependencies
uv venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"              # or: pip install -e ".[dev]"

# 3. Start FastAPI Backend (Port 8000)
uvicorn src.api.main:app --reload --port 8000

# 4. Start Next.js Frontend (Port 3000)
cd web && npm install && npm run dev
```

---

## CLI & API Reference

### CLI Commands (`migration-agent`)
| Command | Description |
| :--- | :--- |
| `migration-agent start` | Boots the full Docker Compose stack with health polling |
| `migration-agent stop` | Stops all running stack containers cleanly |
| `migration-agent run <path> -t <lib> [--wait]` | Submits a migration job and streams progress |
| `migration-agent status [<job_id>]` | Checks overall stack health or specific job details |
| `migration-agent mcp-ast / mcp-git / mcp-docs` | Launches standalone MCP servers over stdio |

### Key REST & WebSocket Endpoints
| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/api/jobs` | `POST` | Create migration job (`{ "path_or_url": "...", "target_library": "..." }`) |
| `/api/jobs/{id}` | `GET` | Get job status (`scanning`, `awaiting_approval`, `migrating`, `complete`) |
| `/api/jobs/{id}/plan` | `GET` | Retrieve file-level AST risk plan for review |
| `/api/jobs/{id}/approve` | `POST` | Approve files to resume migration (`{ "approved_files": [...] }`) |
| `/api/jobs/{id}/results` | `GET` | Get final unified diffs and generated git apply commands |
| `/api/jobs/{id}/stream` | `WebSocket` | Real-time event stream for logs, test runs, and self-healing |

---

## Configuration & Model Routing

Configure models in [`config.yaml`](config.example.yaml) using [LiteLLM](https://docs.litellm.ai/):

```yaml
model_lite: "gemini/gemini-2.5-flash"      # Fast model for simple transforms
model_default: "gemini/gemini-2.5-flash"   # Frontier model for complex fallback and self-heal patches
max_healing_attempts: 3                   # Max sandbox retry cycles per file
```

**Supported Providers**: Google Gemini (`gemini/gemini-2.5-flash`), OpenAI (`openai/gpt-4o`), Anthropic (`anthropic/claude-3-5-sonnet-20241022`), Groq (`groq/llama-3.3-70b-versatile`), or local Ollama (`ollama/qwen2.5-coder:14b`).

**Web Search & Doc Fallback**: When an error or deprecated pattern isn't covered in the local `src/docs_corpus/`, `mcp-server-docs` automatically uses live web search via [Tavily](https://tavily.com/) if `TAVILY_API_KEY` is set in `.env` (with DuckDuckGo fallback).

> [!NOTE]
> **Security & Workspace Isolation**: The API container mounts `/var/run/docker.sock` to launch ephemeral sandbox runners. User source repositories are never edited in-place; all transformations occur inside `~/.migration-agent/workspaces/`.

---

## Troubleshooting & FAQ

- **Docker daemon not running**: Start Docker Desktop or run `sudo systemctl start docker`.
- **Permission denied on `/var/run/docker.sock` (Linux)**: Run `sudo usermod -aG docker $USER && newgrp docker`.
- **Port Conflict (5432, 8000, 3000)**: Adjust port mappings in `docker-compose.yml` and match `DATABASE_URL` / `api_port`.
- **API Key Missing**: Ensure `.env` has your valid key, then restart: `docker compose down && docker compose up -d`.

---

## Running Tests & License

```bash
uv run pytest -m "not docker"   # Fast unit tests (no Docker needed)
uv run pytest                   # Full suite including sandbox integration tests
uv run ruff check .             # Linting and style check
```

Licensed under the [MIT License](LICENSE).
