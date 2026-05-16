# AgentFlow — AI Software Development Factory

> **Give it a spec. Get back production-ready software.**

AgentFlow is an autonomous software development factory powered by a swarm of specialized AI agents. Unlike one-shot code generators, AgentFlow mirrors a real engineering org — it interviews you to refine requirements, architects the system, develops in parallel tracks, tests rigorously, and delivers deployable software. No GitHub dependency. No manual git workflows. Just spec in, software out.

## The Problem

AI code generation today is either:
- **One-shot** — paste a prompt, get a blob of code, pray it works
- **Human-bottlenecked** — AI writes code, but humans still manage branches, resolve conflicts, run tests, coordinate tasks

Neither approach scales. Real software development requires *iteration*, *coordination*, and *quality gates* — things that current tools punt back to humans.

## The Vision

AgentFlow replaces the entire software development lifecycle with an orchestrated agent swarm:

```
Human Spec → Interview & Refinement → Architecture → Parallel Development → Testing → Integration → Delivery
     ↑              ↓                                        ↓                   ↓
     └──── Feedback Loops ◄──────────────────────────────────┴───────────────────┘
```

The human stays in the loop at decision points, not in the weeds of implementation.

## Agent Architecture

### 1. Spec Analyst Agent
- Receives the initial human spec (plain English, bullet points, napkin sketch — whatever)
- Conducts a **structured interview** with the human:
  - Identifies ambiguities ("You said 'user auth' — do you need OAuth, magic links, or email/password?")
  - Proposes tradeoffs ("Real-time updates need WebSockets, which adds infra complexity — worth it?")
  - Surfaces missing requirements ("You didn't mention error handling for payments — what should happen on failure?")
- Outputs a **formal spec document**: features, constraints, acceptance criteria, tech stack recommendation

### 2. Tech Lead Agent
- The orchestrator. Reads the refined spec and creates a **work breakdown**:
  - Decomposes features into independent tasks
  - Identifies dependencies between tasks
  - Assigns tasks to developer agents based on domain (backend, frontend, infra)
  - Manages the execution schedule — parallelizes where possible, sequences where necessary
- **Monitors progress**: detects when agents are stuck, re-plans dynamically, resolves conflicts between agents' outputs
- Maintains a **project knowledge base** — shared context that all agents can read/write

### 3. Architect Agent
- Designs the system before any code is written:
  - Database schema with entity relationships
  - API contracts (endpoints, request/response shapes, auth requirements)
  - Component architecture (frontend modules, backend services, shared libraries)
  - Infrastructure blueprint (containerization, networking, environment config)
- Outputs architecture as **structured artifacts** (not just docs) that other agents consume programmatically

### 4. Developer Agents (multiple, parallelized)
- Specialized by domain:
  - **Backend Developer** — API implementation, business logic, database access
  - **Frontend Developer** — UI components, state management, API integration
  - **Infrastructure Developer** — Dockerfiles, CI config, deployment scripts, environment setup
- Each agent works in its own **isolated workspace** (not git branches — a custom content-addressable store)
- Agents can read each other's interfaces but can't modify each other's code
- When conflicts arise (e.g., frontend expects a field the backend doesn't provide), the Tech Lead Agent mediates

### 5. QA Agent
- Doesn't just write tests — it **validates behavior**:
  - Generates unit tests from the spec's acceptance criteria
  - Generates integration tests from the API contracts
  - Runs all tests against the developer agents' code
  - If tests fail, sends **specific failure reports** back to the responsible developer agent
  - Developer agent fixes → QA re-runs → loop until green
- Also performs:
  - **Security scan** — checks for common vulnerabilities (injection, auth bypass, exposed secrets)
  - **Performance baseline** — runs load tests and flags bottlenecks

### 6. Integration Agent
- Once all agents' code passes QA individually:
  - Merges all workspaces into a unified codebase using **semantic merging** (understands code intent, not just text diffs)
  - Resolves integration conflicts (import paths, shared state, config alignment)
  - Runs the full test suite against the integrated codebase
  - Packages the final output: runnable application + documentation + deployment config

## Git-Based Workspace Isolation

AgentFlow uses **git** under the hood, with a workspace isolation layer designed for AI agent workflows:

- **Branch-per-agent** — each agent works on its own branch or worktree, with clear interface boundaries
- **Semantic diff layer** — a presentation layer on top of git that summarizes logical changes ("added authentication middleware"), not just line-level diffs
- **Parallel-first** — agents work concurrently on isolated branches, merged through structured integration
- **Checkpoint & rollback** — git commits serve as checkpoints; any step can be rolled back without affecting others
- **Audit trail** — full provenance via git history: which agent wrote which code, based on which spec requirement, approved by which gate

## Human-in-the-Loop Gates

AgentFlow is autonomous but not unsupervised. Humans approve at key gates:

| Gate | What happens | Human action |
|------|-------------|--------------|
| **Spec Approval** | Interview complete, formal spec ready | Review, edit, approve |
| **Architecture Review** | System design complete | Approve or request changes |
| **Mid-Development Check** | Core features implemented, tests passing | Review progress, adjust priorities |
| **Final Delivery** | Integrated codebase, all tests green | Accept or request revisions |

Between gates, agents work autonomously. Feedback at any gate propagates back through the agent chain.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| **Agent Framework** | Python + Anthropic Claude API (tool use, structured outputs) |
| **Agent Orchestration** | Custom orchestrator (async task graph with dependency resolution) |
| **Version Control** | Git with branch-per-agent isolation and semantic diff layer |
| **Communication** | Agent message bus (in-process async queue) |
| **Human Interface** | CLI (primary) + Web dashboard (real-time progress visualization) |
| **Code Execution** | Docker sandboxes for running/testing generated code |
| **Output Packaging** | Docker Compose bundle with generated README and deployment docs |

## Example Workflow

```
$ agentflow new "I need a task management API with user auth, team workspaces,
  and real-time notifications when tasks are assigned"

🔍 Spec Analyst: I have a few questions to refine your requirements...

  Q1: For user authentication, do you prefer:
      (a) Email/password with JWT tokens
      (b) OAuth2 with Google/GitHub
      (c) Both
  > c

  Q2: Real-time notifications — should these be:
      (a) In-app only (WebSocket)
      (b) In-app + email
      (c) In-app + email + Slack webhook
  > a

  Q3: What scale are you designing for?
      (a) < 100 users (simple, fast to build)
      (b) 100-10K users (need caching, connection pooling)
      (c) 10K+ users (distributed architecture)
  > b

📋 Spec finalized. Review at: ./output/spec.md
   [Approve / Edit / Regenerate]: approve

🏗️  Architect designing system...
📋 Architecture ready. Review at: ./output/architecture.md
   [Approve / Edit / Regenerate]: approve

⚡ Development starting — 3 agents working in parallel...
   ├── Backend Developer:  ████████░░ 80% (12/15 endpoints)
   ├── Frontend Developer: ██████░░░░ 60% (4/7 pages)
   └── Infra Developer:    ██████████ 100% (Docker + CI ready)

🧪 QA Agent running tests...
   ├── Unit tests:        47/47 passing
   ├── Integration tests: 12/12 passing
   └── Security scan:     0 critical, 1 warning (logged)

🔗 Integration Agent merging workspaces...
   ├── Semantic merge:    clean
   ├── Full test suite:   59/59 passing
   └── Package:           ready

✅ Delivered: ./output/task-manager/
   ├── docker-compose.yml    (one command to run)
   ├── README.md             (setup + API docs)
   ├── src/backend/          (FastAPI + PostgreSQL)
   ├── src/frontend/         (React + WebSocket)
   └── tests/                (full coverage)
```

## What Makes This Different

| Feature | Typical AI Code Gen | CrewAI-style Teams | AgentFlow |
|---------|--------------------|--------------------|-----------|
| Spec refinement | None — GIGO | Basic prompt → output | Interactive interview with tradeoff analysis |
| Development model | One-shot generation | Linear pipeline (one agent at a time) | Parallel agents with shared context |
| Conflict resolution | Manual (git merge) | Not addressed | Git-based isolation with structured integration |
| Quality assurance | "Hope it works" | Tests generated but often not run | Closed-loop: test → fail → fix → retest |
| Human oversight | Review everything | Review final output | Gate-based: approve milestones, not lines |
| Iteration | Start over | Start over | Feedback propagates, agents revise |

## Project Structure

```
AgentFlow/
├── src/
│   ├── agents/
│   │   ├── spec_analyst.py       # Interview & spec refinement
│   │   ├── tech_lead.py          # Orchestration & task management
│   │   ├── architect.py          # System design
│   │   ├── developer.py          # Code generation (backend/frontend/infra)
│   │   ├── qa.py                 # Testing & validation
│   │   └── integrator.py         # Workspace merging & packaging
│   ├── orchestrator/
│   │   ├── task_graph.py         # Dependency-aware task scheduler
│   │   ├── message_bus.py        # Inter-agent communication
│   │   └── knowledge_base.py     # Shared project context
│   ├── vcs/
│   │   ├── workspace.py          # Git-based isolated agent workspaces
│   │   ├── branch_manager.py     # Branch-per-agent lifecycle
│   │   ├── semantic_diff.py      # Intent-level diff presentation layer
│   │   └── merger.py             # Structured integration & merge engine
│   ├── sandbox/
│   │   ├── executor.py           # Docker-based code execution
│   │   └── test_runner.py        # Sandboxed test execution
│   └── interface/
│       ├── cli.py                # Command-line interface
│       └── dashboard/            # Web-based progress dashboard
├── tests/
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## License

MIT
