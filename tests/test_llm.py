"""Tests for the LLM client wrapper and mock."""

import pytest

from src.llm.client import LLMResponse, ToolCall, Usage
from src.llm.mock import MockLLMClient


async def test_mock_client_returns_queued_responses():
    """MockLLMClient pops responses in FIFO order."""
    responses = [
        LLMResponse(content="first"),
        LLMResponse(content="second"),
        LLMResponse(content="third"),
    ]
    mock = MockLLMClient(responses)

    r1 = await mock.generate("sys", [{"role": "user", "content": "a"}])
    r2 = await mock.generate("sys", [{"role": "user", "content": "b"}])
    r3 = await mock.generate("sys", [{"role": "user", "content": "c"}])

    assert r1.content == "first"
    assert r2.content == "second"
    assert r3.content == "third"
    assert mock.call_count == 3


async def test_mock_client_structured_output():
    """generate_structured extracts tool call input when name matches."""
    structured_data = {"title": "My App", "features": ["auth", "dashboard"]}
    response = LLMResponse(
        content="",
        tool_calls=(ToolCall(name="structured_output", input=structured_data, id="tc_1"),),
    )
    mock = MockLLMClient([response])

    result = await mock.generate_structured("sys", [{"role": "user", "content": "q"}], {})

    assert result == structured_data


async def test_mock_client_structured_output_fallback():
    """generate_structured returns raw content when no matching tool call."""
    response = LLMResponse(content="plain text response")
    mock = MockLLMClient([response])

    result = await mock.generate_structured("sys", [{"role": "user", "content": "q"}], {})

    assert result == {"raw": "plain text response"}


async def test_mock_client_raises_on_empty_queue():
    """MockLLMClient raises IndexError when response queue is exhausted."""
    mock = MockLLMClient([])

    with pytest.raises(IndexError, match="response queue is empty"):
        await mock.generate("sys", [{"role": "user", "content": "q"}])


async def test_llm_response_is_frozen():
    """LLMResponse, ToolCall, and Usage are immutable frozen dataclasses."""
    response = LLMResponse(content="hello", usage=Usage(10, 20), model="test")

    with pytest.raises(AttributeError):
        response.content = "modified"  # type: ignore[misc]

    usage = Usage(10, 20)
    with pytest.raises(AttributeError):
        usage.input_tokens = 99  # type: ignore[misc]

    tc = ToolCall(name="tool", input={"key": "val"}, id="t1")
    with pytest.raises(AttributeError):
        tc.name = "other"  # type: ignore[misc]


async def test_mock_client_token_tracking():
    """MockLLMClient accumulates token usage across calls."""
    responses = [
        LLMResponse(content="a", usage=Usage(100, 50)),
        LLMResponse(content="b", usage=Usage(200, 75)),
    ]
    mock = MockLLMClient(responses)

    await mock.generate("sys", [{"role": "user", "content": "q1"}])
    assert mock.total_input_tokens == 100
    assert mock.total_output_tokens == 50

    await mock.generate("sys", [{"role": "user", "content": "q2"}])
    assert mock.total_input_tokens == 300
    assert mock.total_output_tokens == 125


async def test_mock_client_records_calls():
    """MockLLMClient records all call details for assertion."""
    mock = MockLLMClient([LLMResponse(content="ok")])

    await mock.generate(
        "system prompt",
        [{"role": "user", "content": "hello"}],
        tools=[{"name": "my_tool"}],
    )

    assert len(mock.calls) == 1
    call = mock.calls[0]
    assert call["method"] == "generate"
    assert call["system_prompt"] == "system prompt"
    assert call["messages"] == [{"role": "user", "content": "hello"}]
    assert call["tools"] == [{"name": "my_tool"}]


async def test_mock_client_enqueue():
    """Responses can be added after construction with enqueue()."""
    mock = MockLLMClient()
    mock.enqueue(LLMResponse(content="late addition"))

    result = await mock.generate("sys", [{"role": "user", "content": "q"}])

    assert result.content == "late addition"
