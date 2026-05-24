"""Architect Agent — generates structured design artifacts from a spec."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from src.agents.base import Agent, AgentMessage, AgentRole
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus

logger = logging.getLogger(__name__)

MAX_SPEC_TEXT_LENGTH = 40_000


# --- Pydantic models for architecture artifacts ---


class Column(BaseModel):
    """A database column definition."""

    name: str
    type: str
    primary_key: bool = False


class Table(BaseModel):
    """A database table with its columns."""

    name: str
    columns: list[Column]


class Relationship(BaseModel):
    """A relationship between two database tables."""

    from_table: str
    to_table: str
    type: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]


class DatabaseSchema(BaseModel):
    """Database schema artifact: tables and their relationships."""

    tables: list[Table]
    relationships: list[Relationship]

    @field_validator("tables", mode="before")
    @classmethod
    def tables_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("tables must contain at least one entry")
        return v


class Endpoint(BaseModel):
    """An API endpoint definition."""

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    description: str
    request_schema: dict[str, Any] = {}
    response_schema: dict[str, Any] = {}


class APIContract(BaseModel):
    """API contract artifact: list of endpoints."""

    endpoints: list[Endpoint]

    @field_validator("endpoints", mode="before")
    @classmethod
    def endpoints_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("endpoints must contain at least one entry")
        return v


class Module(BaseModel):
    """A software module with its responsibility."""

    name: str
    responsibility: str
    dependencies: list[str] = []


class ComponentArchitecture(BaseModel):
    """Component architecture artifact: module breakdown."""

    modules: list[Module]

    @field_validator("modules", mode="before")
    @classmethod
    def modules_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("modules must contain at least one entry")
        return v


class Service(BaseModel):
    """An infrastructure service definition."""

    name: str
    image: str
    ports: list[str] = []


class InfraBlueprint(BaseModel):
    """Infrastructure blueprint artifact: services and networking."""

    services: list[Service]
    networking: dict[str, Any] = {}

    @field_validator("services", mode="before")
    @classmethod
    def services_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("services must contain at least one entry")
        return v


# --- Artifact definitions for generation ---

# Iteration order matters: tests mock LLM responses in this same sequence.
_ARTIFACTS: list[tuple[str, str, type[BaseModel]]] = [
    ("db_schema", "database schema with tables, columns, and relationships", DatabaseSchema),
    ("api_contract", "API contract with endpoints, methods, and schemas", APIContract),
    (
        "component_architecture",
        "component architecture with modules and responsibilities",
        ComponentArchitecture,
    ),
    ("infra_blueprint", "infrastructure blueprint with services and networking", InfraBlueprint),
]


class ArchitectAgent(Agent):
    """Generates structured design artifacts from a refined spec.

    Reads the spec from the knowledge base and produces four artifacts:
    - db_schema: Database tables, columns, relationships
    - api_contract: API endpoints with request/response schemas
    - component_architecture: Module breakdown with responsibilities
    - infra_blueprint: Services, containerization, networking
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(AgentRole.ARCHITECT, message_bus, knowledge_base, agent_id)
        self._llm = llm_client

    async def process(self, message: AgentMessage) -> None:
        """Handle incoming messages — generates architecture on request."""
        if message.message_type == "architecture_request":
            try:
                await self.execute({})
            except Exception:
                logger.exception("ArchitectAgent failed to generate artifacts")
                await self.send(
                    message.sender,
                    {"error": "architecture generation failed"},
                    message_type="architecture_error",
                )
                return
            await self.send(
                message.sender,
                {"artifacts": [name for name, _, _ in _ARTIFACTS]},
                message_type="architecture_ready",
            )
        else:
            logger.debug(
                "ArchitectAgent received unhandled message type %r from %s",
                message.message_type,
                message.sender,
            )

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate all architecture artifacts from the spec in KB."""
        spec_entry = await self.kb.get("spec")
        if spec_entry is None:
            raise ValueError("spec not found in knowledge base")

        spec = spec_entry.value
        if not isinstance(spec, dict):
            raise TypeError(f"Expected spec to be a dict, got {type(spec).__name__}")

        spec_text = json.dumps(spec, indent=2)
        if len(spec_text) > MAX_SPEC_TEXT_LENGTH:
            raise ValueError(f"Serialized spec exceeds {MAX_SPEC_TEXT_LENGTH} character limit")

        results: dict[str, Any] = {}

        for key, description, model_cls in _ARTIFACTS:
            artifact = await self._generate_artifact(spec_text, key, description, model_cls)
            results[key] = artifact
            await self.kb.put(
                key,
                artifact,
                author=self.agent_id,
                tags=("architecture",),
            )

        return results

    async def _generate_artifact(
        self,
        spec_text: str,
        key: str,
        description: str,
        model_cls: type[BaseModel],
    ) -> dict[str, Any]:
        """Generate a single architecture artifact via LLM."""
        system_prompt = (
            f"You are a software architect. Based on the following spec, "
            f"generate a {description}. Return a JSON object matching this schema."
        )
        messages = [{"role": "user", "content": spec_text}]

        try:
            result = await self._llm.generate_structured(
                system_prompt,
                messages,
                response_schema=model_cls.model_json_schema(),
            )
            validated = model_cls(**result)
            return validated.model_dump()
        except Exception:
            logger.exception("Failed to generate architecture artifact: %s", key)
            raise
