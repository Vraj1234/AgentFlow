"""Inter-agent message bus — async pub/sub for agent communication."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

MessageHandler = Callable[[Any], Coroutine[Any, Any, None]]


class MessageBus:
    """Async message bus for inter-agent communication.

    Agents subscribe with their ID and receive messages addressed to them.
    Supports direct messaging (point-to-point) and broadcast.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, MessageHandler] = {}
        self._history: list[Any] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, agent_id: str, handler: MessageHandler) -> None:
        """Register an agent to receive messages."""
        async with self._lock:
            self._subscribers[agent_id] = handler

    async def unsubscribe(self, agent_id: str) -> None:
        """Remove an agent from the bus."""
        async with self._lock:
            self._subscribers.pop(agent_id, None)

    async def publish(self, message: Any) -> None:
        """Send a message to its recipient. Stores in history for audit."""
        self._history.append(message)

        async with self._lock:
            handler = self._subscribers.get(message.recipient)

        if handler:
            await handler(message)

    async def broadcast(self, message: Any, exclude: str | None = None) -> None:
        """Send a message to all subscribers except the excluded agent."""
        self._history.append(message)

        async with self._lock:
            handlers = [(aid, h) for aid, h in self._subscribers.items() if aid != exclude]

        await asyncio.gather(*(h(message) for _, h in handlers))

    def get_history(self, limit: int | None = None) -> list[Any]:
        """Return message history, optionally limited to last N messages."""
        if limit:
            return self._history[-limit:]
        return list(self._history)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
