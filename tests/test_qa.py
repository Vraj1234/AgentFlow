"""Tests for the QA agent."""

import pytest

from src.agents.base import AgentMessage
from src.agents.qa import QAAgent, QAResult
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.sandbox.executor import SandboxExecutor
from src.sandbox.test_runner import PytestRunner
from src.vcs.workspace import Workspace


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
async def kb():
    """KB with spec and api_contract for QA to read."""
    kb = KnowledgeBase()
    await kb.put(
        "spec",
        {
            "title": "Task Manager",
            "features": ["task CRUD"],
            "acceptance_criteria": ["users can create tasks"],
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
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def test_runner():
    return PytestRunner(SandboxExecutor())


def _make_test_generation_response() -> LLMResponse:
    """Canned LLM response with generated test files."""
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "test_files": [
                        {
                            "path": "tests/test_tasks.py",
                            "content": (
                                "def test_create_task():\n"
                                "    assert True\n\n"
                                "def test_list_tasks():\n"
                                "    assert True\n"
                            ),
                        },
                    ]
                },
                id="tc_1",
            ),
        ),
    )


def _make_failing_test_response() -> LLMResponse:
    """Canned LLM response with a test that will fail."""
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "test_files": [
                        {
                            "path": "tests/test_fail.py",
                            "content": (
                                "def test_always_fails():\n"
                                "    assert False, 'intentional failure'\n"
                            ),
                        },
                    ]
                },
                id="tc_1",
            ),
        ),
    )


# --- Happy path tests ---


async def test_qa_generates_tests_from_spec(bus, kb, workspace, test_runner):
    """QA agent generates test files via LLM from spec and architecture."""
    mock = MockLLMClient([_make_test_generation_response()])
    agent = QAAgent(mock, bus, kb, workspace, test_runner)

    result = await agent.execute(
        {"task_id": "t1", "title": "Test task CRUD", "description": "Validate task endpoints"}
    )

    assert isinstance(result, QAResult)
    assert len(result.test_files_generated) >= 1
    assert "tests/test_tasks.py" in result.test_files_generated
    assert mock.call_count == 1


async def test_qa_runs_tests_and_reports_pass(bus, kb, workspace, test_runner):
    """QA reports passing results when all generated tests pass."""
    mock = MockLLMClient([_make_test_generation_response()])
    agent = QAAgent(mock, bus, kb, workspace, test_runner)

    result = await agent.execute({"task_id": "t1", "title": "Test", "description": "x"})

    assert result.passed
    assert result.total_tests >= 2
    assert len(result.final_failures) == 0


async def test_qa_runs_tests_and_reports_failure(bus, kb, workspace, test_runner):
    """QA reports failures when generated tests fail."""
    mock = MockLLMClient([_make_failing_test_response()])
    agent = QAAgent(mock, bus, kb, workspace, test_runner, max_retries=1)

    result = await agent.execute({"task_id": "t1", "title": "Test", "description": "x"})

    assert not result.passed
    assert result.total_tests >= 1
    assert result.iterations == 1


async def test_qa_sends_failure_to_developer(bus, kb, workspace, test_runner):
    """QA sends a failure report message to the developer agent."""
    mock = MockLLMClient([_make_failing_test_response()])
    agent = QAAgent(mock, bus, kb, workspace, test_runner, max_retries=1)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("tech_lead", capture)

    msg = AgentMessage(
        sender="tech_lead",
        recipient=agent.agent_id,
        content={
            "task_id": "t1",
            "title": "Test task CRUD",
            "description": "x",
            "developer_agent": "dev_backend",
        },
        message_type="qa_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "task_result"
    assert received[0].content["status"] == "failed"

    await agent.stop()


async def test_qa_message_flow_success(bus, kb, workspace, test_runner):
    """Successful QA sends completed task_result reply."""
    mock = MockLLMClient([_make_test_generation_response()])
    agent = QAAgent(mock, bus, kb, workspace, test_runner)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("tech_lead", capture)

    msg = AgentMessage(
        sender="tech_lead",
        recipient=agent.agent_id,
        content={"task_id": "t1", "title": "Test", "description": "x"},
        message_type="qa_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "task_result"
    assert received[0].content["status"] == "completed"

    await agent.stop()


# --- Error path tests ---


async def test_qa_process_error_reply(bus, workspace, test_runner):
    """process() sends error reply when execution fails."""
    empty_kb = KnowledgeBase()
    mock = MockLLMClient()  # empty queue -> will raise
    agent = QAAgent(mock, bus, empty_kb, workspace, test_runner)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("tech_lead", capture)

    msg = AgentMessage(
        sender="tech_lead",
        recipient=agent.agent_id,
        content={"task_id": "t1", "title": "Test", "description": "x"},
        message_type="qa_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "task_result"
    assert received[0].content["status"] == "failed"

    await agent.stop()


async def test_qa_ignores_unknown_message_type(bus, kb, workspace, test_runner):
    """Unknown message types don't crash the agent."""
    mock = MockLLMClient()
    agent = QAAgent(mock, bus, kb, workspace, test_runner)
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


async def test_qa_result_is_frozen():
    """QAResult dataclass is immutable."""
    result = QAResult(
        passed=True,
        total_tests=5,
        iterations=1,
        final_failures=(),
        test_files_generated=("tests/test_a.py",),
    )
    with pytest.raises(AttributeError):
        result.passed = False  # type: ignore[misc]


# --- Defensive path tests ---


async def test_qa_rejects_unsafe_paths(bus, kb, workspace, test_runner):
    """Test files with path traversal are rejected."""
    unsafe_response = LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "test_files": [
                        {"path": "../../escape.py", "content": "bad"},
                        {"path": "/etc/shadow", "content": "bad"},
                        {
                            "path": "tests/test_safe.py",
                            "content": "def test_ok():\n    assert True\n",
                        },
                    ]
                },
                id="tc_1",
            ),
        ),
    )
    mock = MockLLMClient([unsafe_response])
    agent = QAAgent(mock, bus, kb, workspace, test_runner)

    result = await agent.execute({"task_id": "t1", "title": "Test", "description": "x"})

    assert len(result.test_files_generated) == 1
    assert result.test_files_generated[0] == "tests/test_safe.py"


async def test_qa_rejects_max_retries_zero(workspace, test_runner):
    """QAAgent rejects max_retries < 1."""
    with pytest.raises(ValueError, match="max_retries must be >= 1"):
        QAAgent(
            MockLLMClient(),
            MessageBus(),
            KnowledgeBase(),
            workspace,
            test_runner,
            max_retries=0,
        )


async def test_qa_raises_on_empty_test_files(bus, kb, workspace, test_runner):
    """QA raises RuntimeError when LLM produces no valid test files."""
    empty_response = LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={"test_files": []},
                id="tc_1",
            ),
        ),
    )
    mock = MockLLMClient([empty_response])
    agent = QAAgent(mock, bus, kb, workspace, test_runner)

    with pytest.raises(RuntimeError, match="no valid test files"):
        await agent.execute({"task_id": "t1", "title": "Test", "description": "x"})
