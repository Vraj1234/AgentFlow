"""Tests for the Architect agent."""

import pytest
from pydantic import ValidationError

from src.agents.base import AgentMessage
from src.agents.architect import (
    APIContract,
    ArchitectAgent,
    ComponentArchitecture,
    DatabaseSchema,
    InfraBlueprint,
)
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
async def kb():
    """KB pre-loaded with a spec (Architect reads this)."""
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
    return kb


def _make_db_schema_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "tables": [
                        {
                            "name": "users",
                            "columns": [
                                {"name": "id", "type": "UUID", "primary_key": True},
                                {"name": "email", "type": "VARCHAR(255)", "primary_key": False},
                            ],
                        }
                    ],
                    "relationships": [
                        {"from_table": "tasks", "to_table": "users", "type": "many_to_one"}
                    ],
                },
                id="tc_1",
            ),
        ),
    )


def _make_api_contract_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "endpoints": [
                        {
                            "method": "POST",
                            "path": "/api/tasks",
                            "description": "Create a task",
                            "request_schema": {"title": "string"},
                            "response_schema": {"id": "string", "title": "string"},
                        }
                    ]
                },
                id="tc_2",
            ),
        ),
    )


def _make_component_architecture_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "modules": [
                        {
                            "name": "auth",
                            "responsibility": "User authentication and authorization",
                            "dependencies": [],
                        }
                    ]
                },
                id="tc_3",
            ),
        ),
    )


def _make_infra_blueprint_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "services": [
                        {"name": "api", "image": "python:3.11-slim", "ports": ["8000:8000"]}
                    ],
                    "networking": {"internal_network": "agentflow-net"},
                },
                id="tc_4",
            ),
        ),
    )


def _make_all_responses() -> list[LLMResponse]:
    """All 4 architecture artifact responses in expected call order."""
    return [
        _make_db_schema_response(),
        _make_api_contract_response(),
        _make_component_architecture_response(),
        _make_infra_blueprint_response(),
    ]


async def test_architect_reads_spec_from_kb(bus, kb):
    """Architect reads the spec from the knowledge base before generating."""
    mock = MockLLMClient(_make_all_responses())
    agent = ArchitectAgent(mock, bus, kb)

    await agent.execute({})

    # All 4 LLM calls should reference the spec in their prompts
    assert mock.call_count == 4
    first_call = mock.calls[0]
    assert "Task Manager" in first_call["messages"][0]["content"]


async def test_architect_generates_db_schema(bus, kb):
    """Architect produces a valid DatabaseSchema artifact."""
    mock = MockLLMClient(_make_all_responses())
    agent = ArchitectAgent(mock, bus, kb)

    result = await agent.execute({})

    schema = DatabaseSchema(**result["db_schema"])
    assert len(schema.tables) == 1
    assert schema.tables[0].name == "users"
    assert len(schema.relationships) == 1


async def test_architect_generates_api_contract(bus, kb):
    """Architect produces a valid APIContract artifact."""
    mock = MockLLMClient(_make_all_responses())
    agent = ArchitectAgent(mock, bus, kb)

    result = await agent.execute({})

    contract = APIContract(**result["api_contract"])
    assert len(contract.endpoints) == 1
    assert contract.endpoints[0].method == "POST"
    assert contract.endpoints[0].path == "/api/tasks"


async def test_architect_publishes_all_artifacts(bus, kb):
    """All 4 artifacts are written to the KB with 'architecture' tag."""
    mock = MockLLMClient(_make_all_responses())
    agent = ArchitectAgent(mock, bus, kb)

    await agent.execute({})

    for key in ("db_schema", "api_contract", "component_architecture", "infra_blueprint"):
        entry = await kb.get(key)
        assert entry is not None, f"Missing KB entry: {key}"
        assert "architecture" in entry.tags, f"Missing architecture tag on {key}"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: DatabaseSchema(tables=[], relationships=[]),
        lambda: APIContract(endpoints=[]),
        lambda: ComponentArchitecture(modules=[]),
        lambda: InfraBlueprint(services=[]),
    ],
    ids=["empty_tables", "empty_endpoints", "empty_modules", "empty_services"],
)
async def test_architect_pydantic_models_reject_empty(factory):
    """All architecture Pydantic models reject empty required lists."""
    with pytest.raises(ValidationError):
        factory()


async def test_architect_message_flow(bus, kb):
    """Sending an architecture_request message triggers generation and reply."""
    mock = MockLLMClient(_make_all_responses())
    agent = ArchitectAgent(mock, bus, kb)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("orchestrator", capture)

    msg = AgentMessage(
        sender="orchestrator",
        recipient=agent.agent_id,
        content={},
        message_type="architecture_request",
    )
    await bus.publish(msg)

    # Should have published all 4 artifacts to KB
    entry = await kb.get("db_schema")
    assert entry is not None

    # Should have sent a reply
    assert len(received) == 1
    assert received[0].message_type == "architecture_ready"

    await agent.stop()


async def test_architect_raises_without_spec(bus):
    """Architect raises when spec is missing from KB."""
    empty_kb = KnowledgeBase()
    mock = MockLLMClient()
    agent = ArchitectAgent(mock, bus, empty_kb)

    with pytest.raises(ValueError, match="spec.*not found"):
        await agent.execute({})


async def test_architect_logs_unknown_message_type(bus, kb):
    """Unknown message types don't crash the agent."""
    mock = MockLLMClient()
    agent = ArchitectAgent(mock, bus, kb)
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


async def test_architect_process_sends_error_reply_on_failure(bus):
    """process() sends architecture_error reply when execute() fails."""
    empty_kb = KnowledgeBase()  # no spec -> execute() will raise
    mock = MockLLMClient()
    agent = ArchitectAgent(mock, bus, empty_kb)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("orchestrator", capture)

    msg = AgentMessage(
        sender="orchestrator",
        recipient=agent.agent_id,
        content={},
        message_type="architecture_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "architecture_error"
    assert "error" in received[0].content

    await agent.stop()


async def test_architect_raises_on_non_dict_spec(bus):
    """Architect raises TypeError when spec value is not a dict."""
    bad_kb = KnowledgeBase()
    await bad_kb.put("spec", "just a string", author="test", tags=("spec",))
    mock = MockLLMClient()
    agent = ArchitectAgent(mock, bus, bad_kb)

    with pytest.raises(TypeError, match="Expected spec to be a dict"):
        await agent.execute({})


async def test_architect_raises_on_oversized_spec(bus):
    """Architect raises ValueError when serialized spec exceeds length limit."""
    huge_kb = KnowledgeBase()
    huge_spec = {"title": "x" * 50_000, "features": ["a"], "data": "y" * 50_000}
    await huge_kb.put("spec", huge_spec, author="test", tags=("spec",))
    mock = MockLLMClient()
    agent = ArchitectAgent(mock, bus, huge_kb)

    with pytest.raises(ValueError, match="exceeds.*character limit"):
        await agent.execute({})
