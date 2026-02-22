"""All Pydantic models and enums for Corbit."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class IterationMode(str, Enum):
    FULL = "full"
    SINGLE_PASS = "single-pass"


class AgentBackend(str, Enum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


class ReviewVerdict(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes-requested"
    ERROR = "error"


class PipelineStatus(str, Enum):
    PENDING = "pending"
    FETCHING = "fetching"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    MERGED = "merged"
    FAILED = "failed"


class IssueSource(str, Enum):
    GITHUB = "github"
    LINEAR = "linear"


class IssueComment(BaseModel):
    author: str
    body: str


class Issue(BaseModel):
    """Abstract base for all issue types."""

    title: str
    url: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    comments: list[IssueComment] = Field(default_factory=list)

    @property
    def slug(self) -> str:
        raise NotImplementedError

    @property
    def display_id(self) -> str:
        raise NotImplementedError

    @property
    def source(self) -> IssueSource:
        raise NotImplementedError

    def to_prompt(self) -> str:
        raise NotImplementedError


class GitHubIssue(Issue):
    number: int
    repo_owner: str = ""
    repo_name: str = ""

    @property
    def slug(self) -> str:
        return str(self.number)

    @property
    def display_id(self) -> str:
        return f"#{self.number}"

    @property
    def source(self) -> IssueSource:
        return IssueSource.GITHUB

    def to_prompt(self) -> str:
        label_str = f"\nLabels: {', '.join(self.labels)}" if self.labels else ""
        comments_str = ""
        if self.comments:
            formatted = "\n\n".join(
                f"**{c.author}:**\n{c.body}" for c in self.comments
            )
            comments_str = f"\n\n---\n\n### Comments\n\n{formatted}"
        return (
            f"GitHub Issue #{self.number}: {self.title}\n"
            f"URL: {self.url}\n"
            f"{label_str}\n\n"
            f"{self.body}"
            f"{comments_str}"
        )


class LinearIssue(Issue):
    identifier: str  # e.g. "ENG-123"
    team_key: str = ""
    state: str = ""

    @property
    def slug(self) -> str:
        return self.identifier

    @property
    def display_id(self) -> str:
        return self.identifier

    @property
    def source(self) -> IssueSource:
        return IssueSource.LINEAR

    def to_prompt(self) -> str:
        state_str = f"\nState: {self.state}" if self.state else ""
        label_str = f"\nLabels: {', '.join(self.labels)}" if self.labels else ""
        comments_str = ""
        if self.comments:
            formatted = "\n\n".join(
                f"**{c.author}:**\n{c.body}" for c in self.comments
            )
            comments_str = f"\n\n---\n\n### Comments\n\n{formatted}"
        return (
            f"Linear Issue {self.identifier}: {self.title}\n"
            f"URL: {self.url}\n"
            f"{state_str}{label_str}\n\n"
            f"{self.body}"
            f"{comments_str}"
        )


class MergeMethod(str, Enum):
    SQUASH = "squash"
    MERGE = "merge"
    REBASE = "rebase"


class CorbitConfig(BaseModel):
    coder_backend: AgentBackend = AgentBackend.CLAUDE_CODE
    reviewer_backend: AgentBackend = AgentBackend.CLAUDE_CODE
    max_review_rounds: int = 4
    iteration_mode: IterationMode = IterationMode.FULL
    parallel_workers: int = 2
    main_branch: str = "main"
    agent_timeout: int = 600
    coder_model: str = ""
    reviewer_model: str = ""
    debug: bool = False
    sequential: bool = True
    merge_method: MergeMethod = MergeMethod.SQUASH
    clean: bool = False
    wait_for_merge: bool = False
    linear_api_key: str = ""
    linear_post_comment: bool = True
    skip_permissions: bool = True


class WorktreeInfo(BaseModel):
    issue_slug: str
    branch_name: str
    path: Path
    base_branch: str


class AgentResult(BaseModel):
    success: bool
    output: str = ""
    error: str = ""
    session_id: str | None = None


class ReviewSeverity(str, Enum):
    BUG = "bug"  # Incorrect behavior, data loss, security
    CORRECTNESS = "correctness"  # Missing edge case, wrong assumption
    DESIGN = "design"  # Poor abstraction, bolted-on change, maintainability
    TESTING = "testing"  # Missing or insufficient tests
    NIT = "nit"  # Style, naming, minor improvement


class ReviewItem(BaseModel):
    file: str
    comment: str
    severity: ReviewSeverity = ReviewSeverity.CORRECTNESS


class ReviewResult(BaseModel):
    verdict: ReviewVerdict
    comments: str = ""
    items: list[ReviewItem] = Field(default_factory=list)


class PullRequestInfo(BaseModel):
    number: int
    url: str
    head_branch: str
    base_branch: str


class PipelineState(BaseModel):
    issue_slug: str
    source: IssueSource = IssueSource.GITHUB
    status: PipelineStatus = PipelineStatus.PENDING
    current_round: int = 0
    review_history: list[ReviewResult] = Field(default_factory=list)
    error: str = ""
    pr: PullRequestInfo | None = None
    worktree: WorktreeInfo | None = None


class EpicPlan(BaseModel):
    """A GitHub epic broken into sequential groups of parallelizable child issues."""
    parent_issue: int
    groups: list[list[int]]  # Sequential groups; issues within each group run in parallel


class LinearEpicPlan(BaseModel):
    """A Linear parent issue broken into sequential groups of parallelizable child issues."""
    parent_identifier: str  # e.g. "ENG-100"
    groups: list[list[str]]  # e.g. [["ENG-101", "ENG-102"], ["ENG-103"]]
