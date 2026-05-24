"""Version control and workspace isolation for AgentFlow."""

from src.vcs.branch_manager import BranchManager
from src.vcs.merger import ConflictInfo, MergeResult, WorkspaceMerger
from src.vcs.semantic_diff import DiffSummary, SemanticDiff
from src.vcs.workspace import Workspace

__all__ = [
    "BranchManager",
    "ConflictInfo",
    "DiffSummary",
    "MergeResult",
    "SemanticDiff",
    "Workspace",
    "WorkspaceMerger",
]
