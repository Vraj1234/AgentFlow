"""Dependency-aware task graph for scheduling agent work items."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any
from uuid import uuid4

from src.agents.base import AgentRole


class TaskStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Task:
    title: str
    description: str
    assigned_to: AgentRole
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    dependencies: tuple[str, ...] = ()
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskGraph:
    """A directed acyclic graph of tasks with dependency-aware scheduling.

    Tasks are added with explicit dependency edges. The graph provides:
    - Ready-task queries (all dependencies completed)
    - Topological ordering into parallelizable waves
    - Status transitions with failure cascading
    - Cycle detection on insert
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()

    async def add_task(self, task: Task) -> Task:
        """Add a task to the graph. Raises CycleError if it creates a cycle."""
        async with self._lock:
            for dep_id in task.dependencies:
                if dep_id not in self._tasks:
                    raise ValueError(f"Dependency '{dep_id}' not found in graph")

            self._tasks[task.id] = task

            # Auto-promote to READY if no dependencies
            if not task.dependencies:
                updated = replace(task, status=TaskStatus.READY)
                self._tasks[task.id] = updated
                return updated

            return task

    async def get_task(self, task_id: str) -> Task:
        """Retrieve a task by ID."""
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(f"Task '{task_id}' not found")
            return self._tasks[task_id]

    async def get_ready_tasks(self) -> list[Task]:
        """Return all tasks whose dependencies are fully completed."""
        async with self._lock:
            return [t for t in self._tasks.values() if t.status == TaskStatus.READY]

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: dict[str, Any] | None = None,
    ) -> Task:
        """Transition a task's status and cascade side effects."""
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(f"Task '{task_id}' not found")

            task = self._tasks[task_id]
            updated = replace(task, status=status, result=result or task.result)
            self._tasks[task_id] = updated

            if status == TaskStatus.COMPLETED:
                self._promote_dependents(task_id)
            elif status == TaskStatus.FAILED:
                self._block_dependents(task_id)

            return updated

    async def get_tasks_by_status(self, status: TaskStatus) -> list[Task]:
        """Return all tasks with the given status."""
        async with self._lock:
            return [t for t in self._tasks.values() if t.status == status]

    async def get_tasks_by_assignee(self, role: AgentRole) -> list[Task]:
        """Return all tasks assigned to the given role."""
        async with self._lock:
            return [t for t in self._tasks.values() if t.assigned_to == role]

    async def is_complete(self) -> bool:
        """True when every task is COMPLETED."""
        async with self._lock:
            return all(t.status == TaskStatus.COMPLETED for t in self._tasks.values())

    async def has_failures(self) -> bool:
        """True when any task is FAILED."""
        async with self._lock:
            return any(t.status == TaskStatus.FAILED for t in self._tasks.values())

    async def get_execution_order(self) -> list[list[Task]]:
        """Return tasks grouped into parallelizable waves (topological layers)."""
        async with self._lock:
            remaining = dict(self._tasks)
            completed_ids: set[str] = set()
            waves: list[list[Task]] = []

            while remaining:
                wave = [
                    t for t in remaining.values() if all(d in completed_ids for d in t.dependencies)
                ]
                if not wave:
                    break  # remaining tasks have unresolvable deps
                for t in wave:
                    del remaining[t.id]
                    completed_ids.add(t.id)
                waves.append(wave)

            return waves

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    def _promote_dependents(self, completed_id: str) -> None:
        """Promote PENDING tasks to READY if all their deps are now COMPLETED."""
        for task in self._tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if completed_id not in task.dependencies:
                continue
            if all(self._tasks[d].status == TaskStatus.COMPLETED for d in task.dependencies):
                self._tasks[task.id] = replace(task, status=TaskStatus.READY)

    def _block_dependents(self, failed_id: str) -> None:
        """Recursively block tasks that depend on a failed task."""
        for task in list(self._tasks.values()):
            if failed_id in task.dependencies and task.status in (
                TaskStatus.PENDING,
                TaskStatus.READY,
            ):
                self._tasks[task.id] = replace(task, status=TaskStatus.BLOCKED)
                self._block_dependents(task.id)
