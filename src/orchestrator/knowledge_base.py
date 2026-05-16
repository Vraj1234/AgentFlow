"""Shared knowledge base — project context accessible by all agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class KnowledgeEntry:
    key: str
    value: Any
    author: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: tuple[str, ...] = ()


class KnowledgeBase:
    """Shared, append-friendly knowledge base for project context.

    All agents can read from and write to the knowledge base.
    Entries are immutable once written — updates create new versions.
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[KnowledgeEntry]] = {}
        self._lock = asyncio.Lock()

    async def put(self, key: str, value: Any, author: str, tags: tuple[str, ...] = ()) -> None:
        """Store a value. Multiple versions are kept per key."""
        entry = KnowledgeEntry(key=key, value=value, author=author, tags=tags)
        async with self._lock:
            if key not in self._entries:
                self._entries[key] = []
            self._entries[key].append(entry)

    async def get(self, key: str) -> KnowledgeEntry | None:
        """Get the latest version of a key."""
        async with self._lock:
            versions = self._entries.get(key)
            if versions:
                return versions[-1]
            return None

    async def get_history(self, key: str) -> list[KnowledgeEntry]:
        """Get all versions of a key."""
        async with self._lock:
            return list(self._entries.get(key, []))

    async def search(self, tag: str) -> list[KnowledgeEntry]:
        """Find all latest entries matching a tag."""
        results = []
        async with self._lock:
            for versions in self._entries.values():
                if versions and tag in versions[-1].tags:
                    results.append(versions[-1])
        return results

    async def keys(self) -> list[str]:
        """List all keys in the knowledge base."""
        async with self._lock:
            return list(self._entries.keys())
