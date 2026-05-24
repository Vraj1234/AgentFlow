"""Developer Agent — generates code in an isolated workspace branch."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from git.exc import GitCommandError

from src.agents.base import Agent, AgentMessage, AgentRole
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.vcs.workspace import Workspace

logger = logging.getLogger(__name__)

MAX_CONTEXT_LENGTH = 40_000
MAX_FILES_PER_TASK = 50
MAX_FILE_CONTENT_BYTES = 500_000

# Maps each developer specialty to the KB artifact keys it reads
_SPECIALTY_ARTIFACTS: dict[AgentRole, list[str]] = {
    AgentRole.DEVELOPER_BACKEND: ["api_contract", "db_schema"],
    AgentRole.DEVELOPER_FRONTEND: ["component_architecture"],
    AgentRole.DEVELOPER_INFRA: ["infra_blueprint"],
}

_VALID_SPECIALTIES = frozenset(_SPECIALTY_ARTIFACTS)


@dataclass(frozen=True)
class CodeGenerationResult:
    """Result of a code generation task.

    Attributes:
        files_created: Paths of files written to the workspace.
        branch: The git branch the code was written on.
        checkpoint_sha: The git commit SHA after checkpointing.
        summary: A short description of what was generated.
    """

    files_created: tuple[str, ...]
    branch: str
    checkpoint_sha: str
    summary: str


class DeveloperAgent(Agent):
    """Generates code in an isolated git branch based on architecture artifacts.

    A single class parameterized by ``specialty`` (DEVELOPER_BACKEND,
    DEVELOPER_FRONTEND, or DEVELOPER_INFRA). Each specialty reads
    different architecture artifacts from the KB.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        workspace: Workspace,
        specialty: AgentRole,
        agent_id: str | None = None,
    ) -> None:
        if specialty not in _VALID_SPECIALTIES:
            raise ValueError(
                f"Invalid specialty {specialty!r}. Must be one of {_VALID_SPECIALTIES}"
            )
        super().__init__(specialty, message_bus, knowledge_base, agent_id)
        self._llm = llm_client
        self._workspace = workspace
        self._specialty = specialty

    async def process(self, message: AgentMessage) -> None:
        """Handle incoming messages — generates code on development_task."""
        if message.message_type == "development_task":
            try:
                result = await self.execute(message.content)
            except Exception:
                logger.exception("DeveloperAgent failed to generate code")
                await self.send(
                    message.sender,
                    {
                        "task_id": message.content.get("task_id", ""),
                        "status": "failed",
                        "error": "code generation failed",
                    },
                    message_type="task_result",
                )
                return
            await self.send(
                message.sender,
                {
                    "task_id": message.content.get("task_id", ""),
                    "status": "completed",
                    "result": {
                        "files_created": list(result.files_created),
                        "branch": result.branch,
                        "checkpoint_sha": result.checkpoint_sha,
                        "summary": result.summary,
                    },
                },
                message_type="task_result",
            )
        else:
            logger.warning(
                "DeveloperAgent received unhandled message type %r from %s",
                message.message_type,
                message.sender,
            )

    async def execute(self, task: dict[str, Any]) -> CodeGenerationResult:
        """Generate code for a task in an isolated branch."""
        branch_name = f"agent/{self._specialty.value}"
        initial_branch = self._workspace.current_branch()

        # Create branch if it doesn't exist, then switch to it
        try:
            self._workspace.create_branch(branch_name, initial_branch)
        except (ValueError, GitCommandError):
            logger.debug("Branch %r already exists, reusing", branch_name)
        self._workspace.switch_branch(branch_name)

        try:
            return await self._generate_code(task, branch_name)
        finally:
            # Restore workspace to initial branch so other agents aren't affected
            try:
                self._workspace.switch_branch(initial_branch)
            except (ValueError, GitCommandError):
                logger.debug("Could not restore branch to %r", initial_branch)

    async def _generate_code(self, task: dict[str, Any], branch_name: str) -> CodeGenerationResult:
        """Core code generation logic, called while on the agent branch."""
        # Build context from architecture artifacts
        context = await self._build_context(task)

        # Generate code via LLM
        system_prompt = (
            f"You are a {self._specialty.value.replace('_', ' ')}. "
            "Based on the task and architecture context, generate the necessary code files. "
            "Return a JSON object with a 'files' array, where each item has "
            "'path' (relative file path) and 'content' (file content as string)."
        )
        messages = [{"role": "user", "content": context}]

        result = await self._llm.generate_structured(
            system_prompt,
            messages,
            response_schema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    }
                },
                "required": ["files"],
            },
        )

        # Validate files list type
        files_list = result.get("files", [])
        if not isinstance(files_list, list):
            raise TypeError(f"LLM returned unexpected 'files' type: {type(files_list).__name__}")

        # Write files to workspace
        files_created: list[str] = []
        for file_info in files_list[:MAX_FILES_PER_TASK]:
            if not isinstance(file_info, dict):
                logger.warning("Skipping non-dict file entry: %r", file_info)
                continue
            raw_path = file_info.get("path", "")
            raw_content = file_info.get("content", "")
            if not isinstance(raw_path, str) or not isinstance(raw_content, str):
                logger.warning("Skipping file entry with non-string fields: %r", file_info)
                continue
            path = self._sanitize_path(raw_path)
            if not path:
                continue
            if len(raw_content.encode()) > MAX_FILE_CONTENT_BYTES:
                logger.warning("Skipping oversized file %s (%d bytes)", path, len(raw_content))
                continue
            self._workspace.write_file(path, raw_content)
            files_created.append(path)

        if len(files_list) > MAX_FILES_PER_TASK:
            logger.warning(
                "LLM returned %d files, capped at %d",
                len(files_list),
                MAX_FILES_PER_TASK,
            )

        # Checkpoint
        title = re.sub(r"[\x00-\x1f]", "", task.get("title", "code generation"))[:200]
        checkpoint_sha = self._workspace.checkpoint(f"[{self._specialty.value}] {title}")

        return CodeGenerationResult(
            files_created=tuple(files_created),
            branch=branch_name,
            checkpoint_sha=checkpoint_sha,
            summary=f"Generated {len(files_created)} file(s) for: {title}",
        )

    async def _build_context(self, task: dict[str, Any]) -> str:
        """Assemble the prompt context from KB artifacts and the task."""
        artifact_keys = _SPECIALTY_ARTIFACTS.get(self._specialty, [])
        artifacts: dict[str, Any] = {}

        for key in artifact_keys:
            entry = await self.kb.get(key)
            if entry is not None and isinstance(entry.value, dict):
                artifacts[key] = entry.value

        context_data = {
            "task": {
                "title": task.get("title", ""),
                "description": task.get("description", ""),
            },
            "architecture": artifacts,
        }

        context = json.dumps(context_data, indent=2)
        if len(context) > MAX_CONTEXT_LENGTH:
            raise ValueError(f"Context exceeds {MAX_CONTEXT_LENGTH} character limit")
        return context

    def _sanitize_path(self, path: str) -> str:
        """Validate and sanitize a file path from LLM output."""
        # Strip all control characters
        path = re.sub(r"[\x00-\x1f\x7f]", "", path).strip()
        # Reject absolute paths and path traversal
        if path.startswith("/") or ".." in path:
            logger.warning("Rejected unsafe file path from LLM: %s", path)
            return ""
        return path
