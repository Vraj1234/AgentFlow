"""Tests for the task graph engine."""

import pytest

from src.agents.base import AgentRole
from src.orchestrator.task_graph import Task, TaskGraph, TaskStatus


async def test_add_task_and_retrieve():
    """Tasks can be added and retrieved by ID."""
    graph = TaskGraph()
    task = Task(
        id="t1",
        title="Build API",
        description="Create REST endpoints",
        assigned_to=AgentRole.DEVELOPER_BACKEND,
    )
    await graph.add_task(task)

    retrieved = await graph.get_task("t1")
    assert retrieved.title == "Build API"
    assert retrieved.assigned_to == AgentRole.DEVELOPER_BACKEND
    assert graph.task_count == 1


async def test_ready_tasks_with_no_dependencies():
    """Tasks without dependencies are immediately READY."""
    graph = TaskGraph()
    t1 = Task(id="t1", title="A", description="", assigned_to=AgentRole.ARCHITECT)
    t2 = Task(id="t2", title="B", description="", assigned_to=AgentRole.QA)

    await graph.add_task(t1)
    await graph.add_task(t2)

    ready = await graph.get_ready_tasks()
    assert len(ready) == 2
    assert {t.id for t in ready} == {"t1", "t2"}


async def test_ready_tasks_respects_dependencies():
    """Tasks with unmet dependencies stay PENDING, not READY."""
    graph = TaskGraph()
    t1 = Task(id="t1", title="Design", description="", assigned_to=AgentRole.ARCHITECT)
    await graph.add_task(t1)

    t2 = Task(
        id="t2",
        title="Implement",
        description="",
        assigned_to=AgentRole.DEVELOPER_BACKEND,
        dependencies=("t1",),
    )
    await graph.add_task(t2)

    ready = await graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "t1"

    # Complete t1 -> t2 should become READY
    await graph.update_status("t1", TaskStatus.COMPLETED)
    ready = await graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "t2"


async def test_execution_order_topological_waves():
    """get_execution_order returns tasks in parallelizable waves."""
    graph = TaskGraph()
    # Wave 1: t1, t2 (no deps)
    # Wave 2: t3 (depends on t1, t2)
    # Wave 3: t4 (depends on t3)
    await graph.add_task(Task(id="t1", title="A", description="", assigned_to=AgentRole.ARCHITECT))
    await graph.add_task(Task(id="t2", title="B", description="", assigned_to=AgentRole.ARCHITECT))
    await graph.add_task(
        Task(
            id="t3",
            title="C",
            description="",
            assigned_to=AgentRole.DEVELOPER_BACKEND,
            dependencies=("t1", "t2"),
        )
    )
    await graph.add_task(
        Task(
            id="t4",
            title="D",
            description="",
            assigned_to=AgentRole.QA,
            dependencies=("t3",),
        )
    )

    waves = await graph.get_execution_order()
    assert len(waves) == 3
    assert {t.id for t in waves[0]} == {"t1", "t2"}
    assert {t.id for t in waves[1]} == {"t3"}
    assert {t.id for t in waves[2]} == {"t4"}


async def test_dependency_chain_is_not_a_cycle():
    """A valid dependency chain (t1 <- t2 <- t3) should not be rejected."""
    graph = TaskGraph()
    await graph.add_task(Task(id="t1", title="A", description="", assigned_to=AgentRole.ARCHITECT))
    await graph.add_task(
        Task(
            id="t2",
            title="B",
            description="",
            assigned_to=AgentRole.ARCHITECT,
            dependencies=("t1",),
        )
    )
    # t3 depends on t2, which depends on t1 — valid chain, not a cycle
    await graph.add_task(
        Task(
            id="t3",
            title="C",
            description="",
            assigned_to=AgentRole.ARCHITECT,
            dependencies=("t2",),
        )
    )
    assert graph.task_count == 3

    waves = await graph.get_execution_order()
    assert len(waves) == 3


async def test_missing_dependency_prevents_implicit_cycle():
    """Dependencies must exist before they can be referenced, preventing cycles."""
    graph = TaskGraph()

    # Can't reference a task that doesn't exist — this prevents cycles
    # because you can never create A->B->A (B would need A to exist first,
    # and A would need B to exist first).
    with pytest.raises(ValueError, match="not found in graph"):
        await graph.add_task(
            Task(
                id="t1",
                title="A",
                description="",
                assigned_to=AgentRole.ARCHITECT,
                dependencies=("t2",),
            )
        )


async def test_status_transitions():
    """Tasks transition through the expected status lifecycle."""
    graph = TaskGraph()
    task = Task(id="t1", title="A", description="", assigned_to=AgentRole.ARCHITECT)
    added = await graph.add_task(task)
    assert added.status == TaskStatus.READY  # no deps -> auto READY

    updated = await graph.update_status("t1", TaskStatus.IN_PROGRESS)
    assert updated.status == TaskStatus.IN_PROGRESS

    result = {"output": "done"}
    completed = await graph.update_status("t1", TaskStatus.COMPLETED, result=result)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.result == result


async def test_is_complete():
    """is_complete is True only when all tasks are COMPLETED."""
    graph = TaskGraph()
    await graph.add_task(Task(id="t1", title="A", description="", assigned_to=AgentRole.ARCHITECT))
    await graph.add_task(Task(id="t2", title="B", description="", assigned_to=AgentRole.QA))

    assert not await graph.is_complete()

    await graph.update_status("t1", TaskStatus.COMPLETED)
    assert not await graph.is_complete()

    await graph.update_status("t2", TaskStatus.COMPLETED)
    assert await graph.is_complete()


async def test_failed_task_blocks_dependents():
    """When a task fails, its dependents are recursively BLOCKED."""
    graph = TaskGraph()
    await graph.add_task(Task(id="t1", title="A", description="", assigned_to=AgentRole.ARCHITECT))
    await graph.add_task(
        Task(
            id="t2",
            title="B",
            description="",
            assigned_to=AgentRole.DEVELOPER_BACKEND,
            dependencies=("t1",),
        )
    )
    await graph.add_task(
        Task(
            id="t3",
            title="C",
            description="",
            assigned_to=AgentRole.QA,
            dependencies=("t2",),
        )
    )

    await graph.update_status("t1", TaskStatus.FAILED)

    t2 = await graph.get_task("t2")
    t3 = await graph.get_task("t3")
    assert t2.status == TaskStatus.BLOCKED
    assert t3.status == TaskStatus.BLOCKED
    assert await graph.has_failures()


async def test_get_tasks_by_assignee():
    """Tasks can be filtered by assigned AgentRole."""
    graph = TaskGraph()
    await graph.add_task(
        Task(id="t1", title="A", description="", assigned_to=AgentRole.DEVELOPER_BACKEND)
    )
    await graph.add_task(
        Task(id="t2", title="B", description="", assigned_to=AgentRole.DEVELOPER_FRONTEND)
    )
    await graph.add_task(
        Task(id="t3", title="C", description="", assigned_to=AgentRole.DEVELOPER_BACKEND)
    )

    backend = await graph.get_tasks_by_assignee(AgentRole.DEVELOPER_BACKEND)
    assert len(backend) == 2
    assert {t.id for t in backend} == {"t1", "t3"}

    frontend = await graph.get_tasks_by_assignee(AgentRole.DEVELOPER_FRONTEND)
    assert len(frontend) == 1
    assert frontend[0].id == "t2"


async def test_missing_dependency_raises():
    """Referencing a non-existent dependency raises ValueError."""
    graph = TaskGraph()

    with pytest.raises(ValueError, match="not found in graph"):
        await graph.add_task(
            Task(
                id="t1",
                title="A",
                description="",
                assigned_to=AgentRole.ARCHITECT,
                dependencies=("nonexistent",),
            )
        )
