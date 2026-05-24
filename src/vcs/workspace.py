"""Git-based workspace for isolated agent development."""

from __future__ import annotations

from pathlib import Path

from git import Repo


class Workspace:
    """Wraps a git repository to provide workspace operations for agents.

    Each agent works on an isolated branch. The workspace supports
    checkpointing (commits), rollback, and diffing.
    """

    def __init__(self, root_path: Path) -> None:
        self._root = root_path
        if (root_path / ".git").exists():
            self._repo = Repo(root_path)
        else:
            self._repo = Repo.init(root_path)
            # Create an initial commit so branches work
            readme = root_path / "README.md"
            readme.write_text("# AgentFlow Workspace\n")
            self._repo.index.add(["README.md"])
            self._repo.index.commit("Initial workspace setup")

    @property
    def repo(self) -> Repo:
        return self._repo

    @property
    def root(self) -> Path:
        return self._root

    def create_branch(self, branch_name: str, from_branch: str = "main") -> None:
        """Create a new branch from the specified base branch."""
        base_ref = self._resolve_branch(from_branch)
        self._repo.create_head(branch_name, base_ref.commit)

    def switch_branch(self, branch_name: str) -> None:
        """Check out the given branch."""
        branch = self._resolve_branch(branch_name)
        branch.checkout()

    def current_branch(self) -> str:
        """Return the name of the currently checked-out branch."""
        return str(self._repo.active_branch)

    def write_file(self, path: str, content: str) -> None:
        """Write a file relative to the workspace root."""
        full_path = self._root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

    def read_file(self, path: str) -> str:
        """Read a file relative to the workspace root."""
        full_path = self._root / path
        return full_path.read_text()

    def list_files(self) -> list[str]:
        """List all tracked and untracked files in the workspace."""
        # index.entries keys are (path, stage) tuples
        tracked = {key[0] for key in self._repo.index.entries}
        untracked = set(self._repo.untracked_files)
        return sorted(tracked | untracked)

    def checkpoint(self, message: str) -> str:
        """Stage all changes and commit. Returns the commit SHA."""
        self._repo.git.add(A=True)
        commit = self._repo.index.commit(message)
        return commit.hexsha

    def rollback(self, sha: str) -> None:
        """Reset the current branch to a prior commit."""
        self._repo.head.reset(sha, index=True, working_tree=True)

    def get_diff(self, from_ref: str, to_ref: str) -> str:
        """Return the raw diff between two refs."""
        return self._repo.git.diff(from_ref, to_ref)

    def _resolve_branch(self, name: str) -> "git.Head":  # type: ignore[name-defined]  # noqa: F821
        """Resolve a branch name, falling back to master if main doesn't exist."""
        for head in self._repo.heads:
            if head.name == name:
                return head
        raise ValueError(f"Branch '{name}' not found")
