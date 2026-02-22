"""Tests for worktree module."""

from corbit.worktree import branch_name_for


def test_branch_name_for() -> None:
    assert branch_name_for("42") == "corbit/issue-42"
    assert branch_name_for("1") == "corbit/issue-1"
    assert branch_name_for("999") == "corbit/issue-999"
    assert branch_name_for("ENG-123") == "corbit/issue-ENG-123"
