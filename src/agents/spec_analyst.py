"""Spec Analyst Agent — interviews the human and outputs a structured spec."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, field_validator

from src.agents.base import Agent, AgentMessage, AgentRole
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus

logger = logging.getLogger(__name__)

MAX_SPEC_LENGTH = 50_000


class StructuredSpec(BaseModel):
    """Formal specification produced after the interview."""

    title: str
    features: list[str]
    constraints: list[str]
    tech_stack: list[str]
    acceptance_criteria: list[str]
    non_functional_requirements: list[str]

    @field_validator(
        "features",
        "constraints",
        "tech_stack",
        "acceptance_criteria",
        "non_functional_requirements",
        mode="before",
    )
    @classmethod
    def must_not_be_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("must contain at least one entry")
        return v


@dataclass(frozen=True)
class Question:
    """A clarifying question generated from spec analysis.

    Attributes:
        id: Unique identifier for this question.
        text: The question text presented to the human.
        options: Suggested answer choices (empty if open-ended).
        category: Domain grouping (e.g. "auth", "scale", "general").
    """

    id: str
    text: str
    options: tuple[str, ...] = ()
    category: str = "general"


class SpecAnalystAgent(Agent):
    """Conducts a structured interview to refine a raw spec into a formal document.

    Workflow:
    1. Receive raw spec text
    2. Call LLM to identify ambiguities and generate clarifying questions
    3. Receive answers from the human
    4. Call LLM to produce a StructuredSpec
    5. Publish the spec to the knowledge base
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(AgentRole.SPEC_ANALYST, message_bus, knowledge_base, agent_id)
        self._llm = llm_client

    async def process(self, message: AgentMessage) -> None:
        """Handle incoming messages — runs spec analysis on spec_request."""
        if message.message_type == "spec_request":
            raw_spec = message.content.get("raw_spec", "")
            questions = await self.analyze_spec(raw_spec)
            questions_data = [
                {
                    "id": q.id,
                    "text": q.text,
                    "options": list(q.options),
                    "category": q.category,
                }
                for q in questions
            ]
            await self.kb.put(
                "spec_questions",
                questions_data,
                author=self.agent_id,
                tags=("spec",),
            )
            await self.send(
                message.sender,
                {"spec_questions_key": "spec_questions"},
                message_type="spec_questions_ready",
            )
        else:
            logger.debug(
                "SpecAnalystAgent received unhandled message type %r from %s",
                message.message_type,
                message.sender,
            )

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Entry point: analyze a raw spec and return questions."""
        raw_spec = task.get("raw_spec", "")
        questions = await self.analyze_spec(raw_spec)
        questions_data = [
            {
                "id": q.id,
                "text": q.text,
                "options": list(q.options),
                "category": q.category,
            }
            for q in questions
        ]
        await self.kb.put(
            "spec_questions",
            questions_data,
            author=self.agent_id,
            tags=("spec",),
        )
        return {"questions": questions_data}

    async def analyze_spec(self, raw_spec: str) -> list[Question]:
        """Call LLM to identify ambiguities and generate clarifying questions."""
        raw_spec = self._sanitize_input(raw_spec)

        system_prompt = (
            "You are a requirements analyst. Analyze the following software spec "
            "and generate clarifying questions to resolve ambiguities. "
            "Return a JSON array of objects with fields: id, text, options (array of strings), category."
        )
        messages = [{"role": "user", "content": raw_spec}]

        response = await self._llm.generate(system_prompt, messages)

        return self._parse_questions(response.content)

    async def refine_spec(self, raw_spec: str, answers: dict[str, str]) -> StructuredSpec:
        """Take the raw spec + user answers and produce a formal StructuredSpec."""
        raw_spec = self._sanitize_input(raw_spec)
        sanitized_answers = {k: self._sanitize_input(v) for k, v in answers.items()}

        system_prompt = (
            "You are a requirements analyst. Given the original spec and answers to "
            "clarifying questions, produce a formal structured specification. "
            "Return a JSON object with fields: title, features (array), constraints (array), "
            "tech_stack (array), acceptance_criteria (array), non_functional_requirements (array)."
        )
        content = (
            f"Original spec:\n{raw_spec}\n\nAnswers:\n{json.dumps(sanitized_answers, indent=2)}"
        )
        messages = [{"role": "user", "content": content}]

        try:
            result = await self._llm.generate_structured(
                system_prompt,
                messages,
                response_schema=StructuredSpec.model_json_schema(),
            )
            spec = StructuredSpec(**result)
        except Exception:
            logger.exception("Failed to generate or validate structured spec")
            raise

        await self.kb.put(
            "spec",
            spec.model_dump(),
            author=self.agent_id,
            tags=("spec",),
        )

        return spec

    def _sanitize_input(self, text: str) -> str:
        """Validate and clean raw input text."""
        text = text.replace("\x00", "")
        if len(text) > MAX_SPEC_LENGTH:
            raise ValueError(f"Input exceeds maximum length of {MAX_SPEC_LENGTH} characters")
        return text

    def _parse_questions(self, content: str) -> list[Question]:
        """Parse LLM response into Question objects."""
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return [
                    Question(
                        id=item.get("id", uuid4().hex),
                        text=item["text"],
                        options=tuple(item.get("options", ())),
                        category=item.get("category", "general"),
                    )
                    for item in data
                ]
        except json.JSONDecodeError:
            logger.debug("LLM returned non-JSON content; using raw fallback")
        except (KeyError, TypeError) as exc:
            logger.warning("LLM JSON missing expected fields (%s); using raw fallback", exc)

        return [
            Question(
                id=uuid4().hex,
                text=content,
                category="raw",
            )
        ]
