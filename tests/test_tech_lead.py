"""Tests for the Tech Lead agent."""

import pytest

from src.agents.base import AgentMessage, AgentRole
from src.agents.tech_lead import TechLeadAgent
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.orchestrator.task_graph import TaskGraph, TaskStatus


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
async def kb():
    """KB pre-loaded with spec and architecture artifacts."""
    kb = KnowledgeBase()
    await kb.put(
        "spec",
        {
            "title": "Task Manager",
            "features": ["user auth", "task CRUD"],
            "constraints": ["must use PostgreSQL"],
            "tech_stack": ["Python", "FastAPI"],
            "acceptance_criteria": ["users can create tasks"],
            "non_functional_requirements": ["<200ms response time"],
        },
        author="spec_analyst",
        tags=("spec",),
    )
    await kb.put(
        "api_contract",
        {
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/tasks",
                    "description": "Create a task",
                    "request_schema": {},
                    "response_schema": {},
                }
            ]
        },
        author="architect",
        tags=("architecture",),
    )
    return kb


@pytest.fixture
def task_graph():
    return TaskGraph()


def _make_decomposition_response() -> LLMResponse:
    """Canned LLM response with task decomposition."""
    tasks = [
        {
            "title": "Implement user model",
            "description": "Create User SQLAlchemy model",
            "assigned_to": "developer_backend",
            "dependencies": [],
        },
        {
            "title": "Implement auth endpoints",
            "description": "Create login/register endpoints",
            "assigned_to": "developer_backend",
            "dependencies": ["Implement user model"],
        },
        {
            "title": "Create frontend auth pages",
            "description": "Login and register UI",
            "assigned_to": "developer_frontend",
            "dependencies": [],
        },
    ]
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={"tasks": tasks},
                id="tc_1",
            ),
        ),
    )


async def test_tech_lead_decomposes_spec_into_tasks(bus, kb, task_graph):
    """decompose_spec calls LLM and populates the task graph."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)

    spec = (await kb.get("spec")).value
    tasks = await agent.decompose_spec(spec)

    assert len(tasks) == 3
    assert task_graph.task_count == 3
    assert mock.call_count == 1


async def test_tech_lead_assigns_correct_roles(bus, kb, task_graph):
    """Tasks are assigned to the correct AgentRole based on LLM output."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)

    spec = (await kb.get("spec")).value
    tasks = await agent.decompose_spec(spec)

    backend_tasks = [t for t in tasks if t.assigned_to == AgentRole.DEVELOPER_BACKEND]
    frontend_tasks = [t for t in tasks if t.assigned_to == AgentRole.DEVELOPER_FRONTEND]
    assert len(backend_tasks) == 2
    assert len(frontend_tasks) == 1


async def test_tech_lead_resolves_dependencies(bus, kb, task_graph):
    """Tasks with dependency titles are resolved to task IDs in the graph."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)

    spec = (await kb.get("spec")).value
    tasks = await agent.decompose_spec(spec)

    # "Implement auth endpoints" depends on "Implement user model"
    auth_task = next(t for t in tasks if t.title == "Implement auth endpoints")
    user_task = next(t for t in tasks if t.title == "Implement user model")
    assert user_task.id in auth_task.dependencies


async def test_tech_lead_dispatches_ready_tasks(bus, kb, task_graph):
    """dispatch_ready_tasks sends messages via the bus for READY tasks."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)
    await agent.start()

    spec = (await kb.get("spec")).value
    await agent.decompose_spec(spec)

    dispatched: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        dispatched.append(msg)

    # Subscribe as a developer agent to capture dispatched tasks
    await bus.subscribe("dev_backend", capture)
    await bus.subscribe("dev_frontend", capture)

    await agent.dispatch_ready_tasks()

    # "Implement user model" and "Create frontend auth pages" have no deps -> READY
    assert len(dispatched) >= 2
    assert all(m.message_type == "development_task" for m in dispatched)

    await agent.stop()


async def test_tech_lead_handles_task_completion(bus, kb, task_graph):
    """Processing a task_result message updates the task graph."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)
    await agent.start()

    spec = (await kb.get("spec")).value
    tasks = await agent.decompose_spec(spec)
    user_task = next(t for t in tasks if t.title == "Implement user model")

    # Simulate a developer completing the task
    result_msg = AgentMessage(
        sender="dev_backend",
        recipient=agent.agent_id,
        content={"task_id": user_task.id, "status": "completed", "result": {"files": ["user.py"]}},
        message_type="task_result",
    )
    await bus.publish(result_msg)

    updated = await task_graph.get_task(user_task.id)
    assert updated.status == TaskStatus.COMPLETED

    await agent.stop()


async def test_tech_lead_handles_task_failure(bus, kb, task_graph):
    """A failed task_result blocks dependent tasks."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)
    await agent.start()

    spec = (await kb.get("spec")).value
    tasks = await agent.decompose_spec(spec)
    user_task = next(t for t in tasks if t.title == "Implement user model")
    auth_task = next(t for t in tasks if t.title == "Implement auth endpoints")

    # Simulate failure
    result_msg = AgentMessage(
        sender="dev_backend",
        recipient=agent.agent_id,
        content={"task_id": user_task.id, "status": "failed", "error": "compile error"},
        message_type="task_result",
    )
    await bus.publish(result_msg)

    failed = await task_graph.get_task(user_task.id)
    blocked = await task_graph.get_task(auth_task.id)
    assert failed.status == TaskStatus.FAILED
    assert blocked.status == TaskStatus.BLOCKED

    await agent.stop()


async def test_tech_lead_execute_reads_from_kb(bus, kb, task_graph):
    """execute() reads spec and architecture from KB before decomposing."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)

    result = await agent.execute({})

    assert "tasks" in result
    assert len(result["tasks"]) == 3
    # Verify the LLM prompt included spec content
    call = mock.calls[0]
    assert "Task Manager" in call["messages"][0]["content"]


async def test_tech_lead_raises_without_spec(bus, task_graph):
    """Tech Lead raises when spec is missing from KB."""
    empty_kb = KnowledgeBase()
    mock = MockLLMClient()
    agent = TechLeadAgent(mock, bus, empty_kb, task_graph)

    with pytest.raises(ValueError, match="spec.*not found"):
        await agent.execute({})


async def test_tech_lead_process_error_reply(bus, task_graph):
    """process() sends error reply when execute fails."""
    empty_kb = KnowledgeBase()
    mock = MockLLMClient()
    agent = TechLeadAgent(mock, bus, empty_kb, task_graph)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("orchestrator", capture)

    msg = AgentMessage(
        sender="orchestrator",
        recipient=agent.agent_id,
        content={},
        message_type="decompose_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "decompose_error"

    await agent.stop()


async def test_tech_lead_ignores_unknown_message_type(bus, kb, task_graph):
    """Unknown message types don't crash the agent."""
    mock = MockLLMClient()
    agent = TechLeadAgent(mock, bus, kb, task_graph)
    await agent.start()

    msg = AgentMessage(
        sender="someone",
        recipient=agent.agent_id,
        content={},
        message_type="unknown_type",
    )
    await bus.publish(msg)
    assert mock.call_count == 0

    await agent.stop()


async def test_tech_lead_raises_on_non_dict_spec(bus, task_graph):
    """Tech Lead raises TypeError when spec in KB is not a dict."""
    bad_kb = KnowledgeBase()
    await bad_kb.put("spec", "not a dict", author="test", tags=("spec",))
    mock = MockLLMClient()
    agent = TechLeadAgent(mock, bus, bad_kb, task_graph)

    with pytest.raises(TypeError, match="Expected spec to be a dict"):
        await agent.execute({})


async def test_tech_lead_writes_task_manifest_to_kb(bus, kb, task_graph):
    """execute() writes a task_manifest entry to the KB."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)

    await agent.execute({})

    entry = await kb.get("task_manifest")
    assert entry is not None
    assert len(entry.value) == 3
    assert "orchestration" in entry.tags


async def test_tech_lead_task_result_missing_task_id(bus, kb, task_graph):
    """task_result with no task_id is logged and ignored."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)
    await agent.start()

    await agent.decompose_spec(
        (await kb.get("spec")).value,
    )

    msg = AgentMessage(
        sender="dev_backend",
        recipient=agent.agent_id,
        content={"status": "completed"},  # no task_id
        message_type="task_result",
    )
    # Should not raise
    await bus.publish(msg)

    await agent.stop()


async def test_tech_lead_task_result_unknown_status(bus, kb, task_graph):
    """task_result with unrecognized status is logged and ignored."""
    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, kb, task_graph)
    await agent.start()

    tasks = await agent.decompose_spec(
        (await kb.get("spec")).value,
    )
    first_task = tasks[0]

    msg = AgentMessage(
        sender="dev_backend",
        recipient=agent.agent_id,
        content={"task_id": first_task.id, "status": "banana"},
        message_type="task_result",
    )
    await bus.publish(msg)

    # Task should remain in its original status (READY), not changed
    t = await task_graph.get_task(first_task.id)
    assert t.status == TaskStatus.READY

    await agent.stop()


async def test_tech_lead_skips_non_dict_architecture_entries(bus, task_graph):
    """Architecture entries that aren't dicts are skipped with a warning."""
    bad_kb = KnowledgeBase()
    await bad_kb.put(
        "spec",
        {"title": "Test", "features": ["x"]},
        author="test",
        tags=("spec",),
    )
    await bad_kb.put(
        "api_contract",
        "not a dict",  # invalid
        author="test",
        tags=("architecture",),
    )

    mock = MockLLMClient([_make_decomposition_response()])
    agent = TechLeadAgent(mock, bus, bad_kb, task_graph)

    result = await agent.execute({})

    assert len(result["tasks"]) == 3
    # The LLM call should not include the bad api_contract
    call = mock.calls[0]
    assert "api_contract" not in call["messages"][0]["content"]
