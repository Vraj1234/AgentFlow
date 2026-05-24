"""Tests for the Integration agent and WorkspaceMerger."""

import pytest

from src.agents.base import AgentMessage
from src.agents.integrator import IntegrationAgent, IntegrationResult
from src.llm.mock import MockLLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus
from src.sandbox.executor import SandboxExecutor
from src.sandbox.test_runner import PytestRunner
from src.vcs.merger import ConflictInfo, MergeResult, WorkspaceMerger
from src.vcs.workspace import Workspace


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
async def kb():
    kb = KnowledgeBase()
    await kb.put("spec", {"title": "Test App"}, author="test", tags=("spec",))
    return kb


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def merger():
    return WorkspaceMerger()


@pytest.fixture
def test_runner():
    return PytestRunner(SandboxExecutor())


def _setup_agent_branches(workspace):
    """Create two agent branches with non-conflicting files."""
    initial = workspace.current_branch()

    workspace.create_branch("agent/developer_backend", initial)
    workspace.switch_branch("agent/developer_backend")
    workspace.write_file("src/api.py", "# backend API\n")
    workspace.checkpoint("backend code")

    workspace.switch_branch(initial)
    workspace.create_branch("agent/developer_frontend", initial)
    workspace.switch_branch("agent/developer_frontend")
    workspace.write_file("src/ui.tsx", "// frontend UI\n")
    workspace.checkpoint("frontend code")

    workspace.switch_branch(initial)
    return ["agent/developer_backend", "agent/developer_frontend"]


def _setup_conflicting_branches(workspace):
    """Create two branches that modify the same file."""
    initial = workspace.current_branch()

    workspace.create_branch("agent/branch_a", initial)
    workspace.switch_branch("agent/branch_a")
    workspace.write_file("shared.py", "# version A\n")
    workspace.checkpoint("branch a change")

    workspace.switch_branch(initial)
    workspace.create_branch("agent/branch_b", initial)
    workspace.switch_branch("agent/branch_b")
    workspace.write_file("shared.py", "# version B\n")
    workspace.checkpoint("branch b change")

    workspace.switch_branch(initial)
    return ["agent/branch_a", "agent/branch_b"]


# --- WorkspaceMerger tests ---


def test_merger_merges_clean_branches(workspace):
    """Non-conflicting branches merge cleanly."""
    branches = _setup_agent_branches(workspace)
    merger = WorkspaceMerger()

    result = merger.merge_branches(workspace, branches, "integration")

    assert result.success
    assert len(result.merged_branches) == 2
    assert len(result.conflicts) == 0
    assert len(result.final_sha) == 40


def test_merger_detects_conflicts(workspace):
    """Conflicting branches are detected."""
    branches = _setup_conflicting_branches(workspace)
    merger = WorkspaceMerger()

    conflicts = merger.detect_conflicts(workspace, branches[0], branches[1])

    assert len(conflicts) >= 1
    assert len(conflicts) >= 1
    assert conflicts[0].branch_a == branches[0]
    assert conflicts[0].branch_b == branches[1]
    assert conflicts[0].conflict_type == "content"


def test_merger_handles_conflict_in_merge(workspace):
    """Merger reports conflicts when branches can't be cleanly merged."""
    branches = _setup_conflicting_branches(workspace)
    merger = WorkspaceMerger()

    result = merger.merge_branches(workspace, branches, "integration")

    assert isinstance(result, MergeResult)
    assert not result.success
    assert len(result.conflicts) >= 1


def test_merge_result_is_frozen():
    """MergeResult dataclass is immutable."""
    result = MergeResult(
        success=True,
        merged_branches=("a", "b"),
        conflicts=(),
        final_sha="abc123",
    )
    with pytest.raises(AttributeError):
        result.success = False  # type: ignore[misc]


def test_conflict_info_is_frozen():
    """ConflictInfo dataclass is immutable."""
    info = ConflictInfo(
        file_path="shared.py",
        branch_a="a",
        branch_b="b",
        conflict_type="content",
    )
    with pytest.raises(AttributeError):
        info.file_path = "other.py"  # type: ignore[misc]


# --- IntegrationAgent tests ---


async def test_integration_agent_merges_all_branches(bus, kb, workspace, merger, test_runner):
    """Integration agent merges agent branches into an integration branch."""
    branches = _setup_agent_branches(workspace)
    mock = MockLLMClient()
    agent = IntegrationAgent(mock, bus, kb, workspace, merger, test_runner)

    result = await agent.execute({"branches": branches})

    assert isinstance(result, IntegrationResult)
    assert result.success
    assert len(result.merged_branches) == 2


async def test_integration_agent_runs_final_tests(bus, kb, workspace, merger, test_runner):
    """Integration agent runs pytest on the merged codebase."""
    branches = _setup_agent_branches(workspace)

    # Write a passing test on one branch so there's something to run
    workspace.switch_branch("agent/developer_backend")
    workspace.write_file("tests/__init__.py", "")
    workspace.write_file("tests/test_api.py", "def test_ok():\n    assert True\n")
    workspace.checkpoint("add tests")
    workspace.switch_branch(workspace.repo.heads[0].name)

    mock = MockLLMClient()
    agent = IntegrationAgent(mock, bus, kb, workspace, merger, test_runner)

    result = await agent.execute({"branches": branches})

    assert result.test_result is not None


async def test_integration_agent_message_flow(bus, kb, workspace, merger, test_runner):
    """Sending integration_request triggers merge and reply."""
    branches = _setup_agent_branches(workspace)
    mock = MockLLMClient()
    agent = IntegrationAgent(mock, bus, kb, workspace, merger, test_runner)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("orchestrator", capture)

    msg = AgentMessage(
        sender="orchestrator",
        recipient=agent.agent_id,
        content={"branches": branches},
        message_type="integration_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "integration_ready"

    await agent.stop()


async def test_integration_agent_process_error_reply(bus, kb, workspace, merger, test_runner):
    """process() sends error reply when merge fails."""
    mock = MockLLMClient()
    agent = IntegrationAgent(mock, bus, kb, workspace, merger, test_runner)
    await agent.start()

    received: list[AgentMessage] = []

    async def capture(msg: AgentMessage) -> None:
        received.append(msg)

    await bus.subscribe("orchestrator", capture)

    # Missing branches key -> execute raises ValueError
    msg = AgentMessage(
        sender="orchestrator",
        recipient=agent.agent_id,
        content={},  # no "branches" key
        message_type="integration_request",
    )
    await bus.publish(msg)

    assert len(received) == 1
    assert received[0].message_type == "integration_error"

    await agent.stop()


async def test_integration_agent_ignores_unknown_message(bus, kb, workspace, merger, test_runner):
    """Unknown message types don't crash the agent."""
    mock = MockLLMClient()
    agent = IntegrationAgent(mock, bus, kb, workspace, merger, test_runner)
    await agent.start()

    msg = AgentMessage(
        sender="someone",
        recipient=agent.agent_id,
        content={},
        message_type="unknown_type",
    )
    await bus.publish(msg)
    assert mock.call_count == 0

    await agent.stop()


async def test_integration_result_is_frozen():
    """IntegrationResult dataclass is immutable."""
    result = IntegrationResult(
        success=True,
        merged_branches=("a",),
        test_result=None,
        final_sha="abc",
    )
    with pytest.raises(AttributeError):
        result.success = False  # type: ignore[misc]
