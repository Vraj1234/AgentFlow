"""Mock LLM client for deterministic testing."""

from __future__ import annotations

from collections import deque
from typing import Any

from src.llm.client import LLMResponse


class MockLLMClient:
    """A test double for LLMClient that returns pre-queued responses.

    Usage::

        mock = MockLLMClient([
            LLMResponse(content="first answer"),
            LLMResponse(content="second answer"),
        ])
        result = await mock.generate("system", [{"role": "user", "content": "hi"}])
        assert result.content == "first answer"
    """

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses: deque[LLMResponse] = deque(responses or [])
        self._calls: list[dict[str, Any]] = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def calls(self) -> list[dict[str, Any]]:
        """All calls made to generate/generate_structured, for assertion."""
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def enqueue(self, response: LLMResponse) -> None:
        """Add a response to the end of the queue."""
        self._responses.append(response)

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Return the next queued response, recording the call."""
        self._calls.append(
            {
                "method": "generate",
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
            }
        )

        if not self._responses:
            raise IndexError(
                "MockLLMClient response queue is empty. "
                f"Received call #{self.call_count} but no more responses are queued."
            )

        response = self._responses.popleft()
        self._total_input_tokens += response.usage.input_tokens
        self._total_output_tokens += response.usage.output_tokens
        return response

    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the next queued response, extracting structured data.

        If the response has tool_calls with name 'structured_output', returns
        that tool call's input. Otherwise returns {"raw": content}.
        """
        self._calls.append(
            {
                "method": "generate_structured",
                "system_prompt": system_prompt,
                "messages": messages,
                "response_schema": response_schema,
            }
        )

        if not self._responses:
            raise IndexError(
                "MockLLMClient response queue is empty. "
                f"Received call #{self.call_count} but no more responses are queued."
            )

        response = self._responses.popleft()
        self._total_input_tokens += response.usage.input_tokens
        self._total_output_tokens += response.usage.output_tokens

        for tc in response.tool_calls:
            if tc.name == "structured_output":
                return tc.input

        return {"raw": response.content}
