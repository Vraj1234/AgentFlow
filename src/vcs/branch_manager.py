"""Branch-per-agent lifecycle management."""

from __future__ import annotations

from src.agents.base import AgentRole
from src.vcs.workspace import Workspace


class BranchManager:
    """Manages agent branch lifecycle with a consistent naming convention.

    Each agent gets a branch named ``agent/{role_value}``
    (e.g. ``agent/developer_backend``).
    """

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._branches: dict[AgentRole, str] = {}

    def create_agent_branch(self, role: AgentRole, from_branch: str = "main") -> str:
        """Create a branch for the given agent role and return its name."""
        branch_name = f"agent/{role.value}"
        self._workspace.create_branch(branch_name, from_branch)
        self._branches[role] = branch_name
        return branch_name

    def get_agent_branch(self, role: AgentRole) -> str:
        """Return the branch name for the given role."""
        if role not in self._branches:
            raise KeyError(f"No branch created for role '{role.value}'")
        return self._branches[role]

    def list_agent_branches(self) -> dict[AgentRole, str]:
        """Return a copy of all agent-to-branch mappings."""
        return dict(self._branches)
