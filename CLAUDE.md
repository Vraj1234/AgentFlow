# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (use a venv — system pip is blocked on macOS)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tests
pytest tests/ -v                          # all tests
pytest tests/test_core.py::test_name -v   # single test
pytest --cov=src --cov-report=term-missing # with coverage

# Lint & format
ruff check src/ tests/                    # lint
ruff format src/ tests/                   # auto-format

# CLI
agentflow new "your spec here"            # run the pipeline
agentflow status                          # check pipeline status
agentflow dashboard                       # launch web dashboard

# Docker
docker compose up                         # requires ANTHROPIC_API_KEY env var
```

## Architecture

AgentFlow is an async-first agent orchestration system. Core primitives wire everything together:

### Core Infrastructure

1. **Agent** (`src/agents/base.py`) — Abstract base class. Each specialized agent subclasses this with its own `process()` and `execute()` methods. Agents have a role (`AgentRole` enum), a unique ID, and lifecycle methods (`start`/`stop`).

2. **MessageBus** (`src/orchestrator/message_bus.py`) — Async pub/sub. Agents subscribe by ID and receive `AgentMessage` instances. Supports point-to-point (`publish`) and broadcast. All messages are stored in an ordered history for audit.

3. **KnowledgeBase** (`src/orchestrator/knowledge_base.py`) — Append-only versioned store. Agents write tagged entries (e.g., spec, architecture) and read the latest or full history.

4. **TaskGraph** (`src/orchestrator/task_graph.py`) — Dependency-aware task scheduler. Tasks are organized into parallelizable waves via topological sort. Status transitions cascade (failure blocks dependents).

5. **LLMClient** (`src/llm/client.py`) — Async wrapper around Anthropic SDK with retry/backoff. `LLMClientProtocol` defines the interface; `MockLLMClient` provides deterministic testing.

### Specialized Agents

- **SpecAnalystAgent** (`src/agents/spec_analyst.py`) — Interviews human, generates clarifying questions, produces `StructuredSpec` via Pydantic model.
- **ArchitectAgent** (`src/agents/architect.py`) — Generates 4 architecture artifacts (db_schema, api_contract, component_architecture, infra_blueprint).
- **TechLeadAgent** (`src/agents/tech_lead.py`) — Decomposes spec into tasks, populates TaskGraph, dispatches to developers.
- **DeveloperAgent** (`src/agents/developer.py`) — Single class parameterized by specialty (backend/frontend/infra). Generates code on isolated git branches.
- **QAAgent** (`src/agents/qa.py`) — Generates tests from spec, runs pytest, supports retry loops.
- **IntegrationAgent** (`src/agents/integrator.py`) — Merges agent branches via `WorkspaceMerger`, runs final test suite.

### Infrastructure Layers

- **VCS** (`src/vcs/`) — Git-based workspace isolation with branch-per-agent, checkpoint/rollback, semantic diff, and workspace merging.
- **Sandbox** (`src/sandbox/`) — Async subprocess executor with timeout, plus `PytestRunner` for test execution.
- **Interface** (`src/interface/`) — Typer CLI (`cli.py`), Pipeline orchestrator (`pipeline.py`), and FastAPI web dashboard (`dashboard/`).

### Communication Flow

Agents never reference each other directly. All coordination goes through the message bus. Shared artifacts (specs, schemas, contracts) go into the knowledge base.

### Concurrency Model

All agent I/O is async (asyncio). The message bus, knowledge base, task graph, and pipeline use `asyncio.Lock` for safe concurrent access.

## Key Conventions

- Immutable data: `AgentMessage`, `KnowledgeEntry`, `Task`, all result dataclasses are frozen. Don't mutate — create new instances via `dataclasses.replace()`.
- `LLMClientProtocol` for type-safe LLM client injection. All agents accept this protocol, not the concrete `LLMClient`.
- Error replies: All agents catch exceptions in `process()` and send error messages back to the sender.
- Input sanitization: Length caps, null-byte stripping, path traversal protection on all LLM outputs before file writes.
- `asyncio_mode = "auto"` in pytest config — async tests don't need explicit markers.
- Line length: 100 (ruff).
- Python target: 3.11+.
