"""Pipeline orchestrator — coordinates the full AgentFlow workflow."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from src.agents.spec_analyst import SpecAnalystAgent, StructuredSpec
from src.llm.client import LLMClientProtocol
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus

logger = logging.getLogger(__name__)


class PipelineStage(Enum):
    """Stages in the AgentFlow pipeline."""

    SPEC_ANALYSIS = "spec_analysis"
    ARCHITECTURE = "architecture"
    DEVELOPMENT = "development"
    QA = "qa"
    INTEGRATION = "integration"


@dataclass(frozen=True)
class PipelineResult:
    """Final result of the full pipeline run.

    Attributes:
        success: Whether the pipeline completed successfully.
        stages_completed: Stages that finished.
        output_path: Path to the final output (None if not reached).
    """

    success: bool
    stages_completed: tuple[PipelineStage, ...]
    output_path: str | None


# (stage_name, status) — e.g. ("spec_analysis", "started")
EventCallback = Callable[[str, str], None]


class Pipeline:
    """Orchestrates the AgentFlow pipeline from spec to delivered software.

    Each stage can be run independently or as part of the full pipeline.
    Progress is reported via an optional event callback.

    Note: A single Pipeline instance must not be shared across concurrent
    coroutines. Create one Pipeline per pipeline run.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        message_bus: MessageBus,
        knowledge_base: KnowledgeBase,
        on_event: EventCallback | None = None,
    ) -> None:
        self._llm = llm_client
        self._bus = message_bus
        self._kb = knowledge_base
        self._on_event = on_event
        self._completed_stages: list[PipelineStage] = []
        self._current_stage: PipelineStage | None = None
        self._lock = asyncio.Lock()

    @property
    def current_stage(self) -> PipelineStage | None:
        """The currently executing stage, or None if idle."""
        return self._current_stage

    @property
    def completed_stages(self) -> tuple[PipelineStage, ...]:
        """Stages that have completed successfully."""
        return tuple(self._completed_stages)

    def _emit(self, stage: str, status: str) -> None:
        """Emit a pipeline event."""
        if self._on_event:
            self._on_event(stage, status)

    async def run_spec_stage(
        self,
        raw_spec: str,
        answers: dict[str, str],
    ) -> StructuredSpec:
        """Run the spec analysis stage: analyze + refine.

        Args:
            raw_spec: The raw specification text from the user.
            answers: Pre-provided answers to clarifying questions.

        Returns:
            The validated StructuredSpec.
        """
        stage = PipelineStage.SPEC_ANALYSIS

        async with self._lock:
            self._current_stage = stage
            self._emit(stage.value, "started")

            try:
                agent = SpecAnalystAgent(self._llm, self._bus, self._kb)

                # Only analyze for questions if no answers are pre-provided
                if not answers:
                    await agent.analyze_spec(raw_spec)

                spec = await agent.refine_spec(raw_spec, answers)

                self._completed_stages.append(stage)
                self._emit(stage.value, "completed")
                return spec
            except Exception:
                self._emit(stage.value, "failed")
                logger.exception("Spec analysis stage failed")
                raise
            finally:
                self._current_stage = None
