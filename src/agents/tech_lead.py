"""Tech Lead Agent — decomposes specs into tasks and orchestrates execution."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

from src.agents.base import Agent, AgentMessage, AgentRole
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.orchestrator.task_graph import Task, TaskGraph, TaskStatus

logger = logging.getLogger(__name__)

MAX_CONTEXT_LENGTH = 40_000
MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 5_000

# Map role value strings to AgentRole enum members
_ROLE_MAP: dict[str, AgentRole] = {r.value: r for r in AgentRole}


class TechLeadAgent(Agent):
    """Decomposes a spec into parallelizable tasks and orchestrates their execution.

    Reads the spec and architecture from the KB, calls the LLM to break
    work into tasks with dependencies and role assignments, populates the
    TaskGraph, and dispatches ready tasks to developer agents via the bus.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        task_graph: TaskGraph,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(AgentRole.TECH_LEAD, message_bus, knowledge_base, agent_id)
        self._llm = llm_client
        self._task_graph = task_graph

    async def process(self, message: AgentMessage) -> None:
        """Handle incoming messages."""
        if message.message_type == "decompose_request":
            try:
                await self.execute({})
            except Exception:
                logger.exception("TechLeadAgent failed to decompose spec")
                await self.send(
                    message.sender,
                    {"error": "decomposition failed"},
                    message_type="decompose_error",
                )
                return
            await self.send(
                message.sender,
                {"task_count": self._task_graph.task_count},
                message_type="decompose_ready",
            )
        elif message.message_type == "task_result":
            await self._handle_task_result(message)
        else:
            logger.debug(
                "TechLeadAgent received unhandled message type %r from %s",
                message.message_type,
                message.sender,
            )

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Read spec + architecture from KB, decompose into tasks."""
        spec_entry = await self.kb.get("spec")
        if spec_entry is None:
            raise ValueError("spec not found in knowledge base")

        spec = spec_entry.value
        if not isinstance(spec, dict):
            raise TypeError(f"Expected spec to be a dict, got {type(spec).__name__}")

        # Gather architecture context if available, with type validation
        architecture: dict[str, Any] = {}
        for key in ("api_contract", "component_architecture", "db_schema", "infra_blueprint"):
            entry = await self.kb.get(key)
            if entry is not None:
                if not isinstance(entry.value, dict):
                    logger.warning(
                        "Skipping architecture entry %s: expected dict, got %s",
                        key,
                        type(entry.value).__name__,
                    )
                    continue
                architecture[key] = entry.value

        tasks = await self.decompose_spec(spec, architecture)

        # Write task manifest to KB for audit and downstream agents
        task_manifest = [
            {
                "id": t.id,
                "title": t.title,
                "assigned_to": t.assigned_to.value,
                "status": t.status.value,
            }
            for t in tasks
        ]
        await self.kb.put(
            "task_manifest",
            task_manifest,
            author=self.agent_id,
            tags=("orchestration",),
        )

        return {"tasks": task_manifest}

    async def decompose_spec(
        self,
        spec: dict[str, Any],
        architecture: dict[str, Any] | None = None,
    ) -> list[Task]:
        """Call LLM to break the spec into tasks and populate the task graph."""
        context = json.dumps({"spec": spec, "architecture": architecture or {}}, indent=2)
        if len(context) > MAX_CONTEXT_LENGTH:
            raise ValueError(f"Context exceeds {MAX_CONTEXT_LENGTH} character limit")

        system_prompt = (
            "You are a tech lead. Decompose the following spec and architecture "
            "into development tasks. For each task provide: title, description, "
            "assigned_to (one of: developer_backend, developer_frontend, developer_infra), "
            "and dependencies (list of task titles this task depends on). "
            "Return a JSON object with a 'tasks' array."
        )
        messages = [{"role": "user", "content": context}]

        result = await self._llm.generate_structured(
            system_prompt,
            messages,
            response_schema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "assigned_to": {"type": "string"},
                                "dependencies": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["title", "description", "assigned_to"],
                        },
                    }
                },
                "required": ["tasks"],
            },
        )

        raw_tasks = result.get("tasks", [])
        return await self._build_task_graph(raw_tasks)

    async def dispatch_ready_tasks(self) -> list[Task]:
        """Send messages to developer agents for all READY tasks."""
        ready = await self._task_graph.get_ready_tasks()
        dispatched: list[Task] = []

        for task in ready:
            await self._task_graph.update_status(task.id, TaskStatus.IN_PROGRESS)
            recipient = f"dev_{task.assigned_to.value.removeprefix('developer_')}"
            try:
                await self.send(
                    recipient,
                    {
                        "task_id": task.id,
                        "title": task.title,
                        "description": task.description,
                        "assigned_to": task.assigned_to.value,
                    },
                    message_type="development_task",
                )
                dispatched.append(task)
            except Exception:
                logger.exception(
                    "Failed to dispatch task %s to %s, reverting to READY",
                    task.id,
                    recipient,
                )
                await self._task_graph.update_status(task.id, TaskStatus.READY)

        return dispatched

    async def _handle_task_result(self, message: AgentMessage) -> None:
        """Update the task graph based on a developer's result."""
        task_id = message.content.get("task_id")
        status_str = message.content.get("status", "")
        result = message.content.get("result")

        if not task_id:
            logger.warning("task_result message missing task_id from %s", message.sender)
            return

        try:
            if status_str == "completed":
                await self._task_graph.update_status(task_id, TaskStatus.COMPLETED, result=result)
            elif status_str == "failed":
                await self._task_graph.update_status(
                    task_id,
                    TaskStatus.FAILED,
                    result={"error": message.content.get("error", "unknown")},
                )
            else:
                logger.warning("Unknown task status %r for task %s", status_str, task_id)
        except KeyError:
            logger.warning("task_result referenced unknown task_id: %s", task_id)

    async def _build_task_graph(self, raw_tasks: list[dict[str, Any]]) -> list[Task]:
        """Convert raw LLM task dicts into Task objects in the graph.

        Dependencies are specified by title and resolved to task IDs.
        Tasks are added in dependency order (no-dep tasks first).
        """
        # First pass: create Task objects without dependencies, build title->id map
        title_to_id: dict[str, str] = {}
        task_objects: list[tuple[dict[str, Any], Task]] = []

        for raw in raw_tasks:
            role = _ROLE_MAP.get(raw.get("assigned_to", ""), AgentRole.DEVELOPER_BACKEND)
            title = self._sanitize_field(raw.get("title", "Untitled"), MAX_TITLE_LENGTH)
            description = self._sanitize_field(raw.get("description", ""), MAX_DESCRIPTION_LENGTH)
            task = Task(
                title=title,
                description=description,
                assigned_to=role,
            )
            title_to_id[task.title] = task.id
            task_objects.append((raw, task))

        # Second pass: resolve dependencies and add to graph in order
        added: list[Task] = []
        no_deps = [(r, t) for r, t in task_objects if not r.get("dependencies")]
        has_deps = [(r, t) for r, t in task_objects if r.get("dependencies")]

        for _, task in no_deps:
            added_task = await self._task_graph.add_task(task)
            added.append(added_task)

        for raw, task in has_deps:
            dep_titles = raw.get("dependencies", [])
            dep_ids = tuple(title_to_id[title] for title in dep_titles if title in title_to_id)
            task_with_deps = replace(task, dependencies=dep_ids)
            added_task = await self._task_graph.add_task(task_with_deps)
            added.append(added_task)

        return added

    def _sanitize_field(self, text: str, max_length: int) -> str:
        """Strip null bytes and cap length on a string field."""
        text = text.replace("\x00", "")
        if len(text) > max_length:
            text = text[:max_length]
        return text
