"""Workspace merger for combining agent branches."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from git.exc import GitCommandError

from src.vcs.workspace import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConflictInfo:
    """Information about a merge conflict between two branches.

    Attributes:
        file_path: The conflicting file path (or description if unknown).
        branch_a: First branch name.
        branch_b: Second branch name.
        conflict_type: Type of conflict (e.g. "content", "delete/modify").
    """

    file_path: str
    branch_a: str
    branch_b: str
    conflict_type: str


@dataclass(frozen=True)
class MergeResult:
    """Result of merging multiple branches.

    Attributes:
        success: Whether the merge completed without unresolved conflicts.
        merged_branches: Branches that were successfully merged.
        conflicts: Any conflicts encountered.
        final_sha: The commit SHA of the merged result.
    """

    success: bool
    merged_branches: tuple[str, ...]
    conflicts: tuple[ConflictInfo, ...]
    final_sha: str


class WorkspaceMerger:
    """Merges multiple agent branches into a target integration branch.

    Stateless by design — all state is in the Workspace. This allows
    future configuration (conflict resolution strategy, retry config)
    via constructor parameters without changing the call sites.
    """

    def merge_branches(
        self,
        workspace: Workspace,
        source_branches: list[str],
        target_branch: str = "integration",
    ) -> MergeResult:
        """Merge source branches into a target branch.

        Creates the target branch if it doesn't exist, then merges each
        source branch sequentially. Reports conflicts encountered.
        """
        initial_branch = workspace.current_branch()

        # Create target branch from current HEAD
        try:
            workspace.create_branch(target_branch, initial_branch)
        except (ValueError, GitCommandError):
            logger.debug("Branch %r already exists, reusing", target_branch)

        # Switch inside the try/finally so cleanup is always active
        try:
            workspace.switch_branch(target_branch)

            merged: list[str] = []
            all_conflicts: list[ConflictInfo] = []

            for branch in source_branches:
                conflicts = self._merge_single(workspace, branch, target_branch)
                if conflicts:
                    all_conflicts.extend(conflicts)
                else:
                    merged.append(branch)

            final_sha = workspace.repo.head.commit.hexsha

            return MergeResult(
                success=len(all_conflicts) == 0,
                merged_branches=tuple(merged),
                conflicts=tuple(all_conflicts),
                final_sha=final_sha,
            )
        finally:
            try:
                workspace.switch_branch(initial_branch)
            except (ValueError, GitCommandError):
                logger.warning("Could not restore branch to %r", initial_branch)

    def detect_conflicts(
        self,
        workspace: Workspace,
        branch_a: str,
        branch_b: str,
    ) -> list[ConflictInfo]:
        """Check for conflicts between two branches without actually merging."""
        initial_branch = workspace.current_branch()
        temp_branch = f"_conflict_check_{branch_a.replace('/', '_')}_{branch_b.replace('/', '_')}"

        try:
            try:
                workspace.create_branch(temp_branch, branch_a)
            except (ValueError, GitCommandError):
                logger.debug("Temp branch %r already exists", temp_branch)
            workspace.switch_branch(temp_branch)

            try:
                workspace.repo.git.merge(branch_b, no_commit=True)
                # Successful no-commit merge: must abort to clean up the
                # staged merge state (MERGE_HEAD), otherwise the repo is
                # left in a "merge in progress" state.
                workspace.repo.git.merge("--abort")
                return []
            except GitCommandError:
                try:
                    workspace.repo.git.merge("--abort")
                except GitCommandError:
                    workspace.repo.head.reset("HEAD", index=True, working_tree=True)

                return self._extract_conflict_info(branch_a, branch_b, workspace)
        finally:
            try:
                workspace.switch_branch(initial_branch)
            except (ValueError, GitCommandError):
                logger.warning("Could not restore branch from temp %r", temp_branch)
            # Clean up temp branch (must not be on it)
            try:
                workspace.repo.delete_head(temp_branch, force=True)
            except (ValueError, GitCommandError):
                logger.debug("Could not delete temp branch %r", temp_branch)

    def _merge_single(
        self,
        workspace: Workspace,
        source_branch: str,
        target_branch: str,
    ) -> list[ConflictInfo]:
        """Merge a single branch into the current branch."""
        try:
            workspace.repo.git.merge(
                source_branch,
                m=f"Merge {source_branch} into {target_branch}",
            )
            return []
        except GitCommandError as exc:
            logger.warning("Conflict merging %s: %s", source_branch, exc)
            try:
                workspace.repo.git.merge("--abort")
            except GitCommandError:
                workspace.repo.head.reset("HEAD", index=True, working_tree=True)

            return self._extract_conflict_info(target_branch, source_branch, workspace)

    def _extract_conflict_info(
        self,
        branch_a: str,
        branch_b: str,
        workspace: Workspace,
    ) -> list[ConflictInfo]:
        """Extract conflict details from the workspace after a failed merge."""
        try:
            unmerged = workspace.repo.index.unmerged_blobs()
            if unmerged:
                return [
                    ConflictInfo(
                        file_path=path,
                        branch_a=branch_a,
                        branch_b=branch_b,
                        conflict_type="content",
                    )
                    for path in unmerged
                ]
        except Exception as exc:
            logger.debug("Could not extract unmerged blobs, using fallback: %s", exc)

        return [
            ConflictInfo(
                file_path="(conflict detected)",
                branch_a=branch_a,
                branch_b=branch_b,
                conflict_type="content",
            )
        ]
