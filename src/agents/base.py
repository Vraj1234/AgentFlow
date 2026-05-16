"""Base Agent class — foundation for all specialized agents in AgentFlow."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


class AgentRole(Enum):
    SPEC_ANALYST = "spec_analyst"
    TECH_LEAD = "tech_lead"
    ARCHITECT = "architect"
    DEVELOPER_BACKEND = "developer_backend"
    DEVELOPER_FRONTEND = "developer_frontend"
    DEVELOPER_INFRA = "developer_infra"
    QA = "qa"
    INTEGRATOR = "integrator"


@dataclass(frozen=True)
class AgentMessage:
    sender: str
    recipient: str
    content: dict[str, Any]
    message_type: str = "task"
    correlation_id: str = field(default_factory=lambda: uuid4().hex)


class Agent(ABC):
    """Base class for all AgentFlow agents.

    Each agent has:
    - A role defining its specialization
    - Access to the shared message bus for inter-agent communication
    - Access to the shared knowledge base for project context
    - An async run loop that processes incoming messages
    """

    def __init__(
        self,
        role: AgentRole,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        agent_id: str | None = None,
    ) -> None:
        self.agent_id = agent_id or f"{role.value}_{uuid4().hex[:8]}"
        self.role = role
        self.bus = message_bus
        self.kb = knowledge_base
        self._running = False

    async def start(self) -> None:
        """Register with the message bus and begin processing."""
        await self.bus.subscribe(self.agent_id, self._handle_message)
        self._running = True

    async def stop(self) -> None:
        """Unsubscribe and stop processing."""
        self._running = False
        await self.bus.unsubscribe(self.agent_id)

    async def send(
        self, recipient: str, content: dict[str, Any], message_type: str = "task"
    ) -> None:
        """Send a message to another agent."""
        msg = AgentMessage(
            sender=self.agent_id,
            recipient=recipient,
            content=content,
            message_type=message_type,
        )
        await self.bus.publish(msg)

    async def _handle_message(self, message: AgentMessage) -> None:
        """Route incoming messages to the agent's process method."""
        if not self._running:
            return
        await self.process(message)

    @abstractmethod
    async def process(self, message: AgentMessage) -> None:
        """Process an incoming message. Implemented by each specialized agent."""
        ...

    @abstractmethod
    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute a standalone task and return the result. Implemented by each specialized agent."""
        ...
