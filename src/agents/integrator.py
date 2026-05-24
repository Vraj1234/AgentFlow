"""Integration Agent — merges agent branches and validates the result."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from git.exc import GitCommandError

from src.agents.base import Agent, AgentMessage, AgentRole
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.sandbox.test_runner import PytestRunner, TestResult
from src.vcs.merger import WorkspaceMerger
from src.vcs.workspace import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntegrationResult:
    """Result of the integration process.

    Attributes:
        success: Whether integration completed successfully.
        merged_branches: Branches that were merged.
        test_result: Result of running tests on the merged code (None if skipped).
        final_sha: The final commit SHA.
    """

    success: bool
    merged_branches: tuple[str, ...]
    test_result: TestResult | None
    final_sha: str


class IntegrationAgent(Agent):
    """Merges all agent branches into a unified codebase and validates it.

    Workflow:
    1. Receive list of agent branches to merge
    2. Use WorkspaceMerger to combine them
    3. Run full test suite on merged result
    4. Report results via message bus
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        workspace: Workspace,
        merger: WorkspaceMerger,
        test_runner: PytestRunner,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(AgentRole.INTEGRATOR, message_bus, knowledge_base, agent_id)
        self._llm = llm_client
        self._workspace = workspace
        self._merger = merger
        self._test_runner = test_runner

    async def process(self, message: AgentMessage) -> None:
        """Handle incoming messages — merges on integration_request."""
        if message.message_type == "integration_request":
            try:
                result = await self.execute(message.content)
            except Exception:
                logger.exception("IntegrationAgent failed during merge")
                await self.send(
                    message.sender,
                    {"error": "integration failed"},
                    message_type="integration_error",
                )
                return
            await self.send(
                message.sender,
                {
                    "success": result.success,
                    "merged_branches": list(result.merged_branches),
                    "final_sha": result.final_sha,
                },
                message_type="integration_ready",
            )
        else:
            logger.warning(
                "IntegrationAgent received unhandled message type %r from %s",
                message.message_type,
                message.sender,
            )

    async def execute(self, task: dict[str, Any]) -> IntegrationResult:
        """Merge branches, run tests, and return results."""
        branches = task.get("branches", [])
        if not isinstance(branches, list) or not branches:
            raise ValueError("task must contain a non-empty 'branches' list")

        # Merge all branches
        merge_result = self._merger.merge_branches(self._workspace, branches, "integration")

        if not merge_result.success:
            logger.warning(
                "Merge had %d conflict(s), reporting partial result",
                len(merge_result.conflicts),
            )
            return IntegrationResult(
                success=False,
                merged_branches=merge_result.merged_branches,
                test_result=None,
                final_sha=merge_result.final_sha,
            )

        # Run tests on the merged codebase
        test_result = await self._run_integration_tests()

        # Write result to KB
        await self.kb.put(
            "integration_result",
            {
                "success": merge_result.success,
                "merged_branches": list(merge_result.merged_branches),
                "test_passed": test_result.passed if test_result else 0,
                "test_failed": test_result.failed if test_result else 0,
                "final_sha": merge_result.final_sha,
            },
            author=self.agent_id,
            tags=("integration",),
        )

        all_tests_pass = (
            test_result is not None and test_result.failed == 0 and test_result.errors == 0
        )

        return IntegrationResult(
            success=all_tests_pass,
            merged_branches=merge_result.merged_branches,
            test_result=test_result,
            final_sha=merge_result.final_sha,
        )

    async def _run_integration_tests(self) -> TestResult | None:
        """Run pytest on the integration branch."""
        initial_branch = self._workspace.current_branch()

        try:
            self._workspace.switch_branch("integration")
        except (ValueError, GitCommandError):
            logger.warning("Integration branch not found, skipping tests")
            return None

        try:
            result = await self._test_runner.run_pytest(self._workspace.root)
            return result
        finally:
            try:
                self._workspace.switch_branch(initial_branch)
            except (ValueError, GitCommandError):
                logger.debug("Could not restore branch to %r", initial_branch)
