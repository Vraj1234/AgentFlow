"""Async LLM client wrapping the Anthropic SDK."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import anthropic


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]
    id: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    model: str = ""


class LLMClient:
    """Async wrapper around the Anthropic Claude API.

    Provides retry with exponential backoff and cumulative token tracking.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a completion request to Claude with retry logic."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._call_with_retry(**kwargs)
        return self._parse_response(response)

    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a structured response by using tool_use to enforce a schema.

        The schema is presented as a tool; the model's tool call input becomes
        the structured output.
        """
        tool_def = {
            "name": "structured_output",
            "description": "Return the response in the required structured format.",
            "input_schema": response_schema,
        }
        response = await self.generate(system_prompt, messages, tools=[tool_def])

        for tc in response.tool_calls:
            if tc.name == "structured_output":
                return tc.input

        # Fallback: try to parse content as JSON
        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, TypeError):
            return {"raw": response.content}

    async def _call_with_retry(self, **kwargs: Any) -> anthropic.types.Message:
        """Call the API with exponential backoff on transient errors."""
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                return await self._client.messages.create(**kwargs)
            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as exc:
                last_error = exc
                if attempt < self._max_retries - 1:
                    delay = self._base_delay * (2**attempt)
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    def _parse_response(self, response: anthropic.types.Message) -> LLMResponse:
        """Convert an Anthropic Message into our frozen LLMResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(name=block.name, input=block.input, id=block.id))

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        self._total_input_tokens += usage.input_tokens
        self._total_output_tokens += usage.output_tokens

        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=usage,
            model=response.model,
        )
