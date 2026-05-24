"""Tests for the Spec Analyst agent."""

import json

import pytest
from pydantic import ValidationError

from src.agents.base import AgentMessage
from src.agents.spec_analyst import Question, SpecAnalystAgent, StructuredSpec
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def kb():
    return KnowledgeBase()


def _make_questions_response() -> LLMResponse:
    """Canned LLM response containing clarifying questions as JSON."""
    questions = [
        {"id": "q1", "text": "What auth method?", "options": ["OAuth", "JWT"], "category": "auth"},
        {"id": "q2", "text": "Target scale?", "options": ["<100", "100-10K"], "category": "scale"},
    ]
    return LLMResponse(content=json.dumps(questions))


def _make_spec_response() -> LLMResponse:
    """Canned LLM response for structured spec generation."""
    spec_data = {
        "title": "Task Manager API",
        "features": ["user auth", "task CRUD"],
        "constraints": ["must use PostgreSQL"],
        "tech_stack": ["Python", "FastAPI"],
        "acceptance_criteria": ["users can create tasks"],
        "non_functional_requirements": ["<200ms response time"],
    }
    return LLMResponse(
        content="",
        tool_calls=(ToolCall(name="structured_output", input=spec_data, id="tc_1"),),
    )


async def test_spec_analyst_generates_questions(bus, kb):
    """analyze_spec calls LLM and returns parsed Question objects."""
    mock = MockLLMClient([_make_questions_response()])
    agent = SpecAnalystAgent(mock, bus, kb)

    questions = await agent.analyze_spec("Build a task manager with auth")

    assert len(questions) == 2
    assert questions[0].id == "q1"
    assert questions[0].text == "What auth method?"
    assert questions[0].options == ("OAuth", "JWT")
    assert questions[0].category == "auth"
    assert mock.call_count == 1


async def test_spec_analyst_refines_spec(bus, kb):
    """refine_spec produces a StructuredSpec from raw spec + answers."""
    mock = MockLLMClient([_make_spec_response()])
    agent = SpecAnalystAgent(mock, bus, kb)

    spec = await agent.refine_spec(
        "Build a task manager",
        {"q1": "OAuth", "q2": "<100"},
    )

    assert isinstance(spec, StructuredSpec)
    assert spec.title == "Task Manager API"
    assert "user auth" in spec.features
    assert mock.call_count == 1


async def test_spec_analyst_publishes_to_knowledge_base(bus, kb):
    """refine_spec stores the structured spec in the knowledge base."""
    mock = MockLLMClient([_make_spec_response()])
    agent = SpecAnalystAgent(mock, bus, kb)

    await agent.refine_spec("Build a task manager", {"q1": "OAuth"})

    entry = await kb.get("spec")
    assert entry is not None
    assert entry.value["title"] == "Task Manager API"
    assert "spec" in entry.tags


async def test_spec_analyst_message_flow(bus, kb):
    """Sending a spec_request message triggers analysis and KB write."""
    mock = MockLLMClient([_make_questions_response()])
    agent = SpecAnalystAgent(mock, bus, kb)
    await agent.start()

    msg = AgentMessage(
        sender="human",
        recipient=agent.agent_id,
        content={"raw_spec": "Build a task manager"},
        message_type="spec_request",
    )
    await bus.publish(msg)

    entry = await kb.get("spec_questions")
    assert entry is not None
    assert len(entry.value) == 2
    assert entry.value[0]["text"] == "What auth method?"

    await agent.stop()


async def test_spec_analyst_execute_returns_questions(bus, kb):
    """execute() returns questions as dicts."""
    mock = MockLLMClient([_make_questions_response()])
    agent = SpecAnalystAgent(mock, bus, kb)

    result = await agent.execute({"raw_spec": "Build something"})

    assert "questions" in result
    assert len(result["questions"]) == 2
    assert result["questions"][0]["id"] == "q1"


async def test_spec_analyst_handles_non_json_response(bus, kb):
    """When LLM returns non-JSON, a single raw question is created."""
    mock = MockLLMClient([LLMResponse(content="I need more details about the project")])
    agent = SpecAnalystAgent(mock, bus, kb)

    questions = await agent.analyze_spec("vague spec")

    assert len(questions) == 1
    assert questions[0].category == "raw"
    assert "more details" in questions[0].text


async def test_structured_spec_validation():
    """StructuredSpec Pydantic model rejects invalid data."""
    with pytest.raises(ValidationError):
        StructuredSpec(title="Test")  # missing required fields


async def test_question_is_frozen():
    """Question dataclass is immutable."""
    q = Question(id="q1", text="test?")
    with pytest.raises(AttributeError):
        q.text = "modified"  # type: ignore[misc]


# --- Tests for HIGH/MEDIUM review fixes ---


async def test_process_replies_to_sender(bus, kb):
    """process() sends a reply message after writing questions to KB."""
    mock = MockLLMClient([_make_questions_response()])
    agent = SpecAnalystAgent(mock, bus, kb)
    await agent.start()

    # Track messages received by the sender
    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("human", capture)

    msg = AgentMessage(
        sender="human",
        recipient=agent.agent_id,
        content={"raw_spec": "Build a task manager"},
        message_type="spec_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "spec_questions_ready"

    await agent.stop()


async def test_process_ignores_unknown_message_type(bus, kb):
    """process() handles unknown message types without crashing."""
    mock = MockLLMClient()
    agent = SpecAnalystAgent(mock, bus, kb)
    await agent.start()

    msg = AgentMessage(
        sender="someone",
        recipient=agent.agent_id,
        content={"data": "irrelevant"},
        message_type="unknown_type",
    )
    # Should not raise
    await bus.publish(msg)
    # No LLM calls should have been made
    assert mock.call_count == 0

    await agent.stop()


async def test_execute_writes_questions_to_kb(bus, kb):
    """execute() also writes questions to the knowledge base."""
    mock = MockLLMClient([_make_questions_response()])
    agent = SpecAnalystAgent(mock, bus, kb)

    await agent.execute({"raw_spec": "Build something"})

    entry = await kb.get("spec_questions")
    assert entry is not None
    assert len(entry.value) == 2


async def test_parse_questions_with_malformed_json_items(bus, kb):
    """JSON list items missing 'text' key fall back to raw question."""
    bad_json = json.dumps([{"id": "q1", "no_text_field": "oops"}])
    mock = MockLLMClient([LLMResponse(content=bad_json)])
    agent = SpecAnalystAgent(mock, bus, kb)

    questions = await agent.analyze_spec("some spec")

    # Should fall back to raw since items lack 'text'
    assert len(questions) == 1
    assert questions[0].category == "raw"


async def test_structured_spec_rejects_empty_features():
    """StructuredSpec rejects empty features list."""
    with pytest.raises(ValidationError):
        StructuredSpec(
            title="Test",
            features=[],
            constraints=["some constraint"],
            tech_stack=["Python"],
            acceptance_criteria=[],
            non_functional_requirements=["fast"],
        )


async def test_raw_spec_length_limit(bus, kb):
    """Oversized raw specs are rejected."""
    mock = MockLLMClient()
    agent = SpecAnalystAgent(mock, bus, kb)

    oversized = "x" * 50_001
    with pytest.raises(ValueError, match="exceeds maximum length"):
        await agent.analyze_spec(oversized)
