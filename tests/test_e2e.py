"""End-to-end tests for the AgentFlow pipeline with mock LLM."""

import json

import pytest

from src.interface.pipeline import Pipeline, PipelineStage
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


def _questions_response() -> LLMResponse:
    return LLMResponse(
        content=json.dumps(
            [
                {
                    "id": "q1",
                    "text": "Auth method?",
                    "options": ["OAuth", "JWT"],
                    "category": "auth",
                },
                {"id": "q2", "text": "Scale?", "options": ["small", "medium"], "category": "scale"},
            ]
        )
    )


def _spec_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "title": "Task Manager API",
                    "features": ["user auth", "task CRUD", "notifications"],
                    "constraints": ["PostgreSQL", "REST API"],
                    "tech_stack": ["Python", "FastAPI", "SQLAlchemy"],
                    "acceptance_criteria": [
                        "users can register and login",
                        "users can create, read, update, delete tasks",
                    ],
                    "non_functional_requirements": ["<200ms response time", "99.9% uptime"],
                },
                id="tc_spec",
            ),
        ),
    )


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def kb():
    return KnowledgeBase()


async def test_full_spec_stage_with_mock_llm(bus, kb):
    """Full spec analysis stage: questions -> answers -> structured spec -> KB."""
    mock = MockLLMClient([_spec_response()])
    pipeline = Pipeline(mock, bus, kb)

    spec = await pipeline.run_spec_stage(
        "Build a task management API with user auth and notifications",
        {"q1": "OAuth", "q2": "medium"},
    )

    # Verify structured spec
    assert spec.title == "Task Manager API"
    assert len(spec.features) == 3
    assert "user auth" in spec.features
    assert len(spec.acceptance_criteria) == 2

    # Verify KB was populated
    entry = await kb.get("spec")
    assert entry is not None
    assert entry.value["title"] == "Task Manager API"
    assert "spec" in entry.tags

    # Verify stage tracking
    assert PipelineStage.SPEC_ANALYSIS in pipeline.completed_stages
    assert pipeline.current_stage is None


async def test_spec_rejection_and_revision(bus, kb):
    """Spec stage can be re-run with different answers to revise the spec."""
    first_spec = LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "title": "V1 App",
                    "features": ["basic auth"],
                    "constraints": ["SQLite"],
                    "tech_stack": ["Python"],
                    "acceptance_criteria": ["login works"],
                    "non_functional_requirements": ["fast"],
                },
                id="tc_v1",
            ),
        ),
    )
    revised_spec = LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "title": "V2 App",
                    "features": ["OAuth", "RBAC"],
                    "constraints": ["PostgreSQL"],
                    "tech_stack": ["Python", "FastAPI"],
                    "acceptance_criteria": ["OAuth login works", "roles enforced"],
                    "non_functional_requirements": ["<100ms"],
                },
                id="tc_v2",
            ),
        ),
    )

    mock = MockLLMClient([first_spec, revised_spec])
    pipeline = Pipeline(mock, bus, kb)

    # First pass
    v1 = await pipeline.run_spec_stage("Build an app", {"q1": "basic"})
    assert v1.title == "V1 App"

    # "Reject" and re-run with different answers
    v2 = await pipeline.run_spec_stage("Build an app", {"q1": "OAuth"})
    assert v2.title == "V2 App"

    # KB should have the latest version
    entry = await kb.get("spec")
    assert entry.value["title"] == "V2 App"

    # Both stages should be tracked
    assert pipeline.completed_stages.count(PipelineStage.SPEC_ANALYSIS) == 2


async def test_pipeline_events_through_full_flow(bus, kb):
    """Events are emitted correctly through the spec stage."""
    mock = MockLLMClient([_spec_response()])
    events: list[tuple[str, str]] = []

    pipeline = Pipeline(mock, bus, kb, on_event=lambda s, st: events.append((s, st)))

    await pipeline.run_spec_stage("Build something", {"q1": "x"})

    assert ("spec_analysis", "started") in events
    assert ("spec_analysis", "completed") in events
    # No failed events
    assert ("spec_analysis", "failed") not in events


async def test_knowledge_base_versioning_through_pipeline(bus, kb):
    """KB maintains version history when spec is updated."""
    responses = [_spec_response(), _spec_response()]
    mock = MockLLMClient(responses)
    pipeline = Pipeline(mock, bus, kb)

    await pipeline.run_spec_stage("v1 spec", {"q1": "a"})
    await pipeline.run_spec_stage("v2 spec", {"q1": "b"})

    history = await kb.get_history("spec")
    assert len(history) == 2
