"""Tests for the pipeline orchestrator."""

import json

import pytest

from src.interface.pipeline import Pipeline, PipelineResult, PipelineStage
from src.llm.client import LLMResponse, ToolCall
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


def _make_questions_response() -> LLMResponse:
    questions = [
        {"id": "q1", "text": "What auth?", "options": ["OAuth"], "category": "auth"},
    ]
    return LLMResponse(content=json.dumps(questions))


def _make_spec_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=(
            ToolCall(
                name="structured_output",
                input={
                    "title": "Test App",
                    "features": ["auth"],
                    "constraints": ["postgres"],
                    "tech_stack": ["Python"],
                    "acceptance_criteria": ["login works"],
                    "non_functional_requirements": ["fast"],
                },
                id="tc_1",
            ),
        ),
    )


def _build_mock_with_answers() -> MockLLMClient:
    """Mock for pipeline with pre-provided answers (skips analyze_spec)."""
    return MockLLMClient(
        [
            _make_spec_response(),  # spec analyst: refine_spec only
        ]
    )


def _build_mock_without_answers() -> MockLLMClient:
    """Mock for pipeline without answers (runs analyze_spec + refine_spec)."""
    return MockLLMClient(
        [
            _make_questions_response(),  # spec analyst: analyze_spec
            _make_spec_response(),  # spec analyst: refine_spec
        ]
    )


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def kb():
    return KnowledgeBase()


async def test_pipeline_spec_stage(bus, kb):
    """Pipeline runs spec analysis stage and stores spec in KB."""
    mock = _build_mock_with_answers()
    pipeline = Pipeline(mock, bus, kb)

    result = await pipeline.run_spec_stage("Build a task manager", {"q1": "OAuth"})

    assert result is not None
    assert result.title == "Test App"

    entry = await kb.get("spec")
    assert entry is not None
    assert entry.value["title"] == "Test App"


async def test_pipeline_spec_stage_without_answers(bus, kb):
    """Pipeline runs analyze_spec when no answers are provided."""
    mock = _build_mock_without_answers()
    pipeline = Pipeline(mock, bus, kb)

    result = await pipeline.run_spec_stage("Build something", {})

    assert result.title == "Test App"
    # Two LLM calls: analyze_spec + refine_spec
    assert mock.call_count == 2


async def test_pipeline_tracks_stages(bus, kb):
    """Pipeline tracks which stages have completed."""
    mock = _build_mock_with_answers()
    pipeline = Pipeline(mock, bus, kb)

    assert pipeline.current_stage is None

    await pipeline.run_spec_stage("Build something", {"q1": "OAuth"})

    assert PipelineStage.SPEC_ANALYSIS in pipeline.completed_stages


async def test_pipeline_result_is_frozen():
    """PipelineResult dataclass is immutable."""
    result = PipelineResult(
        success=True,
        stages_completed=(PipelineStage.SPEC_ANALYSIS,),
        output_path=None,
    )
    with pytest.raises(AttributeError):
        result.success = False  # type: ignore[misc]


async def test_pipeline_stage_enum_count():
    """PipelineStage has exactly 5 stages."""
    assert len(PipelineStage) == 5


async def test_pipeline_emits_events(bus, kb):
    """Pipeline emits stage events via callbacks."""
    mock = _build_mock_with_answers()
    events: list[tuple[str, str]] = []

    def on_event(stage: str, status: str) -> None:
        events.append((stage, status))

    pipeline = Pipeline(mock, bus, kb, on_event=on_event)

    await pipeline.run_spec_stage("Build something", {"q1": "OAuth"})

    assert len(events) >= 2
    assert events[0] == ("spec_analysis", "started")
    assert events[-1] == ("spec_analysis", "completed")


async def test_pipeline_spec_stage_error_emits_failed(bus, kb):
    """Pipeline emits failed event when spec stage errors."""
    mock = MockLLMClient()  # empty -> will raise
    events: list[tuple[str, str]] = []

    def on_event(stage: str, status: str) -> None:
        events.append((stage, status))

    pipeline = Pipeline(mock, bus, kb, on_event=on_event)

    with pytest.raises(Exception):
        await pipeline.run_spec_stage("Build something", {"q1": "x"})

    assert ("spec_analysis", "failed") in events


async def test_pipeline_current_stage_none_after_completion(bus, kb):
    """current_stage is None after a stage finishes."""
    mock = _build_mock_with_answers()
    pipeline = Pipeline(mock, bus, kb)

    await pipeline.run_spec_stage("Build something", {"q1": "OAuth"})

    assert pipeline.current_stage is None
