"""Tests for the Developer agents."""

import pytest

from src.agents.base import AgentMessage, AgentRole
from src.agents.developer import CodeGenerationResult, DeveloperAgent
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.vcs.workspace import Workspace


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
async def kb():
    """KB with architecture artifacts the developer reads."""
    kb = KnowledgeBase()
    await kb.put(
        "api_contract",
        {
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/tasks",
                    "description": "Create a task",
                    "request_schema": {"title": "string"},
                    "response_schema": {"id": "string"},
                }
            ]
        },
        author="architect",
        tags=("architecture",),
    )
    await kb.put(
        "component_architecture",
        {
            "modules": [
                {
                    "name": "TaskList",
                    "responsibility": "Display tasks",
                    "dependencies": [],
                }
            ]
        },
        author="architect",
        tags=("architecture",),
    )
    await kb.put(
        "infra_blueprint",
        {
            "services": [{"name": "api", "image": "python:3.11", "ports": ["8000:8000"]}],
            "networking": {},
        },
        author="architect",
        tags=("architecture",),
    )
    return kb


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


def _make_code_response(files: list[dict[str, str]]) -> LLMResponse:
    """Canned LLM response returning generated code files."""
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={"files": files},
                id="tc_1",
            ),
        ),
    )


def _backend_code_response() -> LLMResponse:
    return _make_code_response(
        [
            {"path": "src/models/user.py", "content": "class User:\n    pass\n"},
            {"path": "src/api/tasks.py", "content": "# task endpoints\n"},
        ]
    )


def _frontend_code_response() -> LLMResponse:
    return _make_code_response(
        [
            {
                "path": "src/components/TaskList.tsx",
                "content": "export const TaskList = () => null;\n",
            },
        ]
    )


def _infra_code_response() -> LLMResponse:
    return _make_code_response(
        [
            {"path": "Dockerfile", "content": "FROM python:3.11\n"},
            {"path": "docker-compose.yml", "content": "services:\n  api:\n    build: .\n"},
        ]
    )


# --- Happy path tests ---


async def test_developer_creates_branch_on_execute(bus, kb, workspace):
    """Developer creates its agent branch before writing code."""
    mock = MockLLMClient([_backend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)

    result = await agent.execute(
        {"task_id": "t1", "title": "Build API", "description": "Create endpoints"}
    )

    assert result.branch == "agent/developer_backend"


async def test_developer_backend_reads_api_contract(bus, kb, workspace):
    """Backend developer includes api_contract in its LLM prompt."""
    mock = MockLLMClient([_backend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)

    await agent.execute({"task_id": "t1", "title": "Build API", "description": "x"})

    call = mock.calls[0]
    assert "api_contract" in call["messages"][0]["content"]


async def test_developer_frontend_reads_component_architecture(bus, kb, workspace):
    """Frontend developer includes component_architecture in its LLM prompt."""
    mock = MockLLMClient([_frontend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_FRONTEND)

    await agent.execute({"task_id": "t1", "title": "Build UI", "description": "x"})

    call = mock.calls[0]
    assert "component_architecture" in call["messages"][0]["content"]


async def test_developer_infra_reads_infra_blueprint(bus, kb, workspace):
    """Infra developer includes infra_blueprint in its LLM prompt."""
    mock = MockLLMClient([_infra_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_INFRA)

    await agent.execute({"task_id": "t1", "title": "Build infra", "description": "x"})

    call = mock.calls[0]
    assert "infra_blueprint" in call["messages"][0]["content"]


async def test_developer_writes_generated_files(bus, kb, workspace):
    """Developer writes LLM-generated files to the workspace."""
    mock = MockLLMClient([_backend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)

    await agent.execute({"task_id": "t1", "title": "Build", "description": "x"})

    workspace.switch_branch("agent/developer_backend")
    content = workspace.read_file("src/models/user.py")
    assert "class User" in content
    content2 = workspace.read_file("src/api/tasks.py")
    assert "task endpoints" in content2


async def test_developer_checkpoints_after_generation(bus, kb, workspace):
    """Developer creates a git checkpoint after writing files."""
    mock = MockLLMClient([_backend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)

    result = await agent.execute({"task_id": "t1", "title": "Build", "description": "x"})

    assert len(result.checkpoint_sha) == 40


async def test_developer_returns_code_generation_result(bus, kb, workspace):
    """execute() returns a CodeGenerationResult dataclass."""
    mock = MockLLMClient([_backend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)

    result = await agent.execute({"task_id": "t1", "title": "Build", "description": "x"})

    assert isinstance(result, CodeGenerationResult)
    assert len(result.files_created) == 2
    assert "src/models/user.py" in result.files_created
    assert result.branch == "agent/developer_backend"


async def test_developer_message_flow(bus, kb, workspace):
    """Sending a development_task message triggers code generation and reply."""
    mock = MockLLMClient([_backend_code_response()])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("tech_lead", capture)

    msg = AgentMessage(
        sender="tech_lead",
        recipient=agent.agent_id,
        content={"task_id": "t1", "title": "Build API", "description": "Create endpoints"},
        message_type="development_task",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "task_result"
    assert received[0].content["status"] == "completed"

    await agent.stop()


# --- Error path tests ---


async def test_developer_process_error_reply(bus, kb, workspace):
    """process() sends error reply when code generation fails."""
    mock = MockLLMClient()  # empty queue -> will raise
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("tech_lead", capture)

    msg = AgentMessage(
        sender="tech_lead",
        recipient=agent.agent_id,
        content={"task_id": "t1", "title": "Build", "description": "x"},
        message_type="development_task",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "task_result"
    assert received[0].content["status"] == "failed"

    await agent.stop()


async def test_developer_ignores_unknown_message_type(bus, kb, workspace):
    """Unknown message types don't crash the agent."""
    mock = MockLLMClient()
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)
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


async def test_developer_rejects_unsafe_paths(bus, kb, workspace):
    """Files with path traversal or absolute paths are skipped."""
    unsafe_response = _make_code_response(
        [
            {"path": "../etc/passwd", "content": "bad"},
            {"path": "/etc/shadow", "content": "bad"},
            {"path": "safe.py", "content": "# safe\n"},
        ]
    )
    mock = MockLLMClient([unsafe_response])
    agent = DeveloperAgent(mock, bus, kb, workspace, specialty=AgentRole.DEVELOPER_BACKEND)

    result = await agent.execute({"task_id": "t1", "title": "Build", "description": "x"})

    assert len(result.files_created) == 1
    assert result.files_created[0] == "safe.py"


async def test_developer_rejects_invalid_specialty():
    """DeveloperAgent rejects non-developer specialties."""
    with pytest.raises(ValueError, match="Invalid specialty"):
        DeveloperAgent(
            MockLLMClient(),
            MessageBus(),
            KnowledgeBase(),
            Workspace.__new__(Workspace),  # dummy, won't be used
            specialty=AgentRole.QA,
        )


async def test_code_generation_result_is_frozen():
    """CodeGenerationResult dataclass is immutable."""
    result = CodeGenerationResult(
        files_created=("a.py",),
        branch="main",
        checkpoint_sha="abc123",
        summary="done",
    )
    with pytest.raises(AttributeError):
        result.branch = "other"  # type: ignore[misc]
