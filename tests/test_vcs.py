"""Tests for VCS workspace, branch manager, and semantic diff."""

import pytest

from src.agents.base import AgentRole
from src.vcs.branch_manager import BranchManager
from src.vcs.semantic_diff import SemanticDiff
from src.vcs.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    """Create a disposable git-backed workspace."""
    return Workspace(tmp_path)


def test_workspace_create_and_checkpoint(workspace):
    """Files can be written and checkpointed as git commits."""
    workspace.write_file("app.py", "print('hello')\n")
    sha = workspace.checkpoint("Add app.py")

    assert len(sha) == 40
    assert "app.py" in workspace.list_files()


def test_workspace_branch_lifecycle(workspace):
    """Branches can be created and switched."""
    # Initial branch is either main or master depending on git config
    initial = workspace.current_branch()

    workspace.create_branch("feature/auth", initial)
    workspace.switch_branch("feature/auth")

    assert workspace.current_branch() == "feature/auth"

    workspace.switch_branch(initial)
    assert workspace.current_branch() == initial


def test_workspace_rollback(workspace):
    """Rollback restores the workspace to a prior checkpoint."""
    workspace.write_file("data.txt", "version 1\n")
    sha1 = workspace.checkpoint("v1")

    workspace.write_file("data.txt", "version 2\n")
    workspace.checkpoint("v2")

    assert workspace.read_file("data.txt") == "version 2\n"

    workspace.rollback(sha1)
    assert workspace.read_file("data.txt") == "version 1\n"


def test_workspace_diff(workspace):
    """get_diff returns raw diff between two commits."""
    workspace.write_file("file.py", "line1\n")
    sha1 = workspace.checkpoint("first")

    workspace.write_file("file.py", "line1\nline2\n")
    sha2 = workspace.checkpoint("second")

    diff = workspace.get_diff(sha1, sha2)
    assert "+line2" in diff


def test_branch_manager_create_agent_branch(workspace):
    """BranchManager creates branches with the agent/{role} naming convention."""
    manager = BranchManager(workspace)
    branch = manager.create_agent_branch(AgentRole.DEVELOPER_BACKEND)

    assert branch == "agent/developer_backend"
    # Verify it actually exists by switching to it
    workspace.switch_branch(branch)
    assert workspace.current_branch() == "agent/developer_backend"


def test_branch_manager_list_branches(workspace):
    """All created agent branches are tracked."""
    manager = BranchManager(workspace)
    manager.create_agent_branch(AgentRole.DEVELOPER_BACKEND)
    manager.create_agent_branch(AgentRole.DEVELOPER_FRONTEND)
    manager.create_agent_branch(AgentRole.QA)

    branches = manager.list_agent_branches()
    assert len(branches) == 3
    assert branches[AgentRole.DEVELOPER_BACKEND] == "agent/developer_backend"
    assert branches[AgentRole.DEVELOPER_FRONTEND] == "agent/developer_frontend"
    assert branches[AgentRole.QA] == "agent/qa"


def test_branch_manager_get_unknown_role(workspace):
    """Requesting a branch for an unregistered role raises KeyError."""
    manager = BranchManager(workspace)

    with pytest.raises(KeyError, match="No branch created"):
        manager.get_agent_branch(AgentRole.ARCHITECT)


def test_semantic_diff_summary():
    """SemanticDiff parses a raw diff into structured DiffSummary."""
    raw_diff = """\
diff --git a/src/app.py b/src/app.py
index abc1234..def5678 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,5 @@
 import os
+import sys
+import json

 def main():
-    pass
+    print("hello")
"""
    sd = SemanticDiff()
    summary = sd.summarize_diff(raw_diff)

    assert summary.files_changed == ("src/app.py",)
    assert summary.additions >= 2
    assert summary.deletions >= 1
    assert "file(s)" in summary.summary


def test_semantic_diff_empty():
    """Empty diff returns zeroed DiffSummary."""
    sd = SemanticDiff()
    summary = sd.summarize_diff("")

    assert summary.files_changed == ()
    assert summary.additions == 0
    assert summary.deletions == 0
    assert summary.summary == "No changes"
