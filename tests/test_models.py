"""Tests for Corbit data models."""

from pathlib import Path

from corbit.models import (
    AgentBackend,
    AgentResult,
    CorbitConfig,
    GitHubIssue,
    IssueComment,
    IterationMode,
    PipelineState,
    PipelineStatus,
    PullRequestInfo,
    ReviewItem,
    ReviewResult,
    ReviewSeverity,
    ReviewVerdict,
    WorktreeInfo,
)


def test_github_issue_to_prompt() -> None:
    issue = GitHubIssue(
        number=42,
        title="Fix login bug",
        body="The login form crashes on submit.",
        labels=["bug", "urgent"],
        url="https://github.com/owner/repo/issues/42",
        repo_owner="owner",
        repo_name="repo",
    )
    prompt = issue.to_prompt()
    assert "#42" in prompt
    assert "Fix login bug" in prompt
    assert "login form crashes" in prompt
    assert "bug" in prompt
    assert "urgent" in prompt


def test_github_issue_to_prompt_with_comments() -> None:
    issue = GitHubIssue(
        number=42,
        title="Fix login bug",
        body="The login form crashes on submit.",
        labels=["bug"],
        comments=[
            IssueComment(author="alice", body="I can reproduce this"),
            IssueComment(author="bob", body="Same here, on Chrome"),
        ],
        url="https://github.com/owner/repo/issues/42",
    )
    prompt = issue.to_prompt()
    assert "Comments" in prompt
    assert "alice" in prompt
    assert "I can reproduce this" in prompt
    assert "bob" in prompt
    assert "Same here, on Chrome" in prompt


def test_github_issue_to_prompt_no_labels() -> None:
    issue = GitHubIssue(number=1, title="Test", body="Body")
    prompt = issue.to_prompt()
    assert "#1" in prompt
    assert "Labels:" not in prompt


def test_corbit_config_defaults() -> None:
    config = CorbitConfig()
    assert config.coder_backend == AgentBackend.CLAUDE_CODE
    assert config.max_review_rounds == 4
    assert config.iteration_mode == IterationMode.FULL
    assert config.parallel_workers == 2
    assert config.main_branch == "main"
    assert config.agent_timeout == 600


def test_corbit_config_custom() -> None:
    config = CorbitConfig(
        coder_backend=AgentBackend.CODEX,
        max_review_rounds=5,
        iteration_mode=IterationMode.SINGLE_PASS,
        parallel_workers=4,
    )
    assert config.coder_backend == AgentBackend.CODEX
    assert config.max_review_rounds == 5
    assert config.iteration_mode == IterationMode.SINGLE_PASS
    assert config.parallel_workers == 4


def test_agent_result() -> None:
    result = AgentResult(success=True, output="done", session_id="abc")
    assert result.success
    assert result.session_id == "abc"

    failed = AgentResult(success=False, error="timeout")
    assert not failed.success
    assert failed.error == "timeout"


def test_review_result() -> None:
    approved = ReviewResult(verdict=ReviewVerdict.APPROVED, comments="LGTM")
    assert approved.verdict == ReviewVerdict.APPROVED

    changes = ReviewResult(
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        comments="Fix the type error",
    )
    assert changes.verdict == ReviewVerdict.CHANGES_REQUESTED


def test_pipeline_state_defaults() -> None:
    state = PipelineState(issue_slug="42")
    assert state.status == PipelineStatus.PENDING
    assert state.current_round == 0
    assert state.review_history == []
    assert state.pr is None
    assert state.worktree is None


def test_pipeline_state_with_pr() -> None:
    pr = PullRequestInfo(
        number=10,
        url="https://github.com/owner/repo/pull/10",
        head_branch="corbit/issue-42",
        base_branch="main",
    )
    state = PipelineState(
        issue_slug="42",
        status=PipelineStatus.APPROVED,
        pr=pr,
        current_round=2,
    )
    assert state.pr is not None
    assert state.pr.number == 10
    assert state.current_round == 2


def test_worktree_info() -> None:
    info = WorktreeInfo(
        issue_slug="42",
        branch_name="corbit/issue-42",
        path=Path("/tmp/worktree"),
        base_branch="main",
    )
    assert info.issue_slug == "42"
    assert info.branch_name == "corbit/issue-42"


def test_review_item_severity_default() -> None:
    item = ReviewItem(file="foo.py", comment="fix this")
    assert item.severity == ReviewSeverity.CORRECTNESS


def test_review_item_severity_explicit() -> None:
    item = ReviewItem(file="foo.py", comment="crash", severity=ReviewSeverity.BUG)
    assert item.severity == ReviewSeverity.BUG


def test_review_severity_enum() -> None:
    assert ReviewSeverity("bug") == ReviewSeverity.BUG
    assert ReviewSeverity("correctness") == ReviewSeverity.CORRECTNESS
    assert ReviewSeverity("nit") == ReviewSeverity.NIT


def test_enums() -> None:
    assert AgentBackend("claude-code") == AgentBackend.CLAUDE_CODE
    assert AgentBackend("codex") == AgentBackend.CODEX
    assert IterationMode("full") == IterationMode.FULL
    assert IterationMode("single-pass") == IterationMode.SINGLE_PASS
    assert ReviewVerdict("approved") == ReviewVerdict.APPROVED
    assert ReviewVerdict("changes-requested") == ReviewVerdict.CHANGES_REQUESTED
