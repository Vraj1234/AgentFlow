"""QA Agent — generates tests, runs them, and validates code quality."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from src.agents.base import Agent, AgentMessage, AgentRole
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.sandbox.test_runner import PytestRunner
from src.vcs.workspace import Workspace

logger = logging.getLogger(__name__)

MAX_CONTEXT_LENGTH = 40_000
MAX_TEST_FILES = 20
# Test files are expected to be smaller than implementation files
MAX_FILE_CONTENT_BYTES = 200_000


@dataclass(frozen=True)
class QAResult:
    """Result of a QA validation cycle.

    Attributes:
        passed: Whether all tests passed.
        total_tests: Number of tests run.
        iterations: Number of test-fix iterations attempted.
        final_failures: Remaining failures after all iterations.
        test_files_generated: Paths of test files written.
    """

    passed: bool
    total_tests: int
    iterations: int
    final_failures: tuple[str, ...]
    test_files_generated: tuple[str, ...]


class QAAgent(Agent):
    """Generates tests from spec/architecture, runs them, and reports results.

    Supports a closed-loop retry cycle: if tests fail, the agent can
    re-run up to ``max_retries`` times (future: route failures back to
    developer agents for fixes between iterations).

    Callers must include a ``task_id`` in ``qa_request`` content for
    correct error correlation in replies.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        workspace: Workspace,
        test_runner: PytestRunner,
        max_retries: int = 3,
        agent_id: str | None = None,
    ) -> None:
        if max_retries < 1:
            raise ValueError(f"max_retries must be >= 1, got {max_retries}")
        super().__init__(AgentRole.QA, message_bus, knowledge_base, agent_id)
        self._llm = llm_client
        self._workspace = workspace
        self._test_runner = test_runner
        self._max_retries = max_retries

    async def process(self, message: AgentMessage) -> None:
        """Handle incoming messages — runs QA on qa_request."""
        if message.message_type == "qa_request":
            try:
                result = await self.execute(message.content)
            except Exception:
                logger.exception("QAAgent failed during test generation/execution")
                await self.send(
                    message.sender,
                    {
                        "task_id": message.content.get("task_id", ""),
                        "status": "failed",
                        "error": "qa execution failed",
                    },
                    message_type="task_result",
                )
                return

            status = "completed" if result.passed else "failed"
            await self.send(
                message.sender,
                {
                    "task_id": message.content.get("task_id", ""),
                    "status": status,
                    "result": {
                        "passed": result.passed,
                        "total_tests": result.total_tests,
                        "iterations": result.iterations,
                        "failures": list(result.final_failures),
                        "test_files": list(result.test_files_generated),
                    },
                },
                message_type="task_result",
            )
        else:
            logger.warning(
                "QAAgent received unhandled message type %r from %s",
                message.message_type,
                message.sender,
            )

    async def execute(self, task: dict[str, Any]) -> QAResult:
        """Generate tests, run them, and return results."""
        # Build context from spec + architecture
        context = await self._build_context(task)

        # Generate test files via LLM
        test_files = await self._generate_tests(context)

        if len(test_files) > MAX_TEST_FILES:
            logger.warning(
                "LLM returned %d test files, capped at %d",
                len(test_files),
                MAX_TEST_FILES,
            )

        # Write test files to workspace
        written_files: list[str] = []
        for file_info in test_files[:MAX_TEST_FILES]:
            if not isinstance(file_info, dict):
                logger.warning("Skipping non-dict test file entry: %r", file_info)
                continue
            raw_path = file_info.get("path", "")
            raw_content = file_info.get("content", "")
            if not isinstance(raw_path, str) or not isinstance(raw_content, str):
                logger.warning("Skipping test file with non-string fields: %r", file_info)
                continue
            path = self._sanitize_path(raw_path)
            if not path:
                continue
            if len(raw_content.encode()) > MAX_FILE_CONTENT_BYTES:
                logger.warning("Skipping oversized test file %s", path)
                continue
            self._workspace.write_file(path, raw_content)
            written_files.append(path)

        if not written_files:
            raise RuntimeError("LLM produced no valid test files")

        self._workspace.checkpoint(
            f"[qa] Generate tests for: {self._sanitize_title(task.get('title', 'qa'))}"
        )

        # Run tests with retry loop
        return await self._run_test_loop(written_files)

    async def _generate_tests(self, context: str) -> list[dict[str, str]]:
        """Call LLM to generate test files."""
        system_prompt = (
            "You are a QA engineer. Based on the spec and architecture context, "
            "generate test files that validate the acceptance criteria. "
            "Return a JSON object with a 'test_files' array, where each item has "
            "'path' (relative file path) and 'content' (test file content as string)."
        )
        messages = [{"role": "user", "content": context}]

        result = await self._llm.generate_structured(
            system_prompt,
            messages,
            response_schema={
                "type": "object",
                "properties": {
                    "test_files": {
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
                "required": ["test_files"],
            },
        )

        test_files = result.get("test_files", [])
        if not isinstance(test_files, list):
            raise TypeError(
                f"LLM returned unexpected 'test_files' type: {type(test_files).__name__}"
            )
        return test_files

    async def _run_test_loop(self, written_files: list[str]) -> QAResult:
        """Run pytest and retry up to max_retries times."""
        test_files_tuple = tuple(written_files)

        for iteration in range(1, self._max_retries + 1):
            test_result = await self._test_runner.run_pytest(
                self._workspace.root, test_path=written_files
            )

            if test_result.failed == 0 and test_result.errors == 0:
                return QAResult(
                    passed=True,
                    total_tests=test_result.total,
                    iterations=iteration,
                    final_failures=(),
                    test_files_generated=test_files_tuple,
                )

            logger.info(
                "QA iteration %d/%d: %d passed, %d failed, %d errors",
                iteration,
                self._max_retries,
                test_result.passed,
                test_result.failed,
                test_result.errors,
            )

        # All retries exhausted
        failure_names = tuple(f.test_name for f in test_result.failures)
        return QAResult(
            passed=False,
            total_tests=test_result.total,
            iterations=self._max_retries,
            final_failures=failure_names,
            test_files_generated=test_files_tuple,
        )

    async def _build_context(self, task: dict[str, Any]) -> str:
        """Assemble prompt context from KB artifacts and the task."""
        artifacts: dict[str, Any] = {}
        for key in ("spec", "api_contract", "component_architecture"):
            entry = await self.kb.get(key)
            if entry is not None and isinstance(entry.value, dict):
                artifacts[key] = entry.value

        context_data = {
            "task": {
                "title": task.get("title", ""),
                "description": task.get("description", ""),
            },
            "context": artifacts,
        }

        context = json.dumps(context_data, indent=2)
        if len(context) > MAX_CONTEXT_LENGTH:
            raise ValueError(f"Context exceeds {MAX_CONTEXT_LENGTH} character limit")
        return context

    def _sanitize_path(self, path: str) -> str:
        """Validate and sanitize a file path from LLM output."""
        path = re.sub(r"[\x00-\x1f\x7f]", "", path).strip()
        if path.startswith("/") or ".." in path:
            logger.warning("Rejected unsafe test file path: %s", path)
            return ""
        normalised = os.path.normpath(path)
        if normalised.startswith(".."):
            logger.warning("Rejected path traversal after normalisation: %s", path)
            return ""
        return normalised

    def _sanitize_title(self, title: str) -> str:
        """Sanitize a title for use in git commit messages."""
        return re.sub(r"[\x00-\x1f\x7f]", "", title)[:200]
