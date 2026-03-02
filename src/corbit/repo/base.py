"""Abstract base class for repo-hosting providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from corbit.models import PullRequestInfo


class PrPollResult(str, Enum):
    MERGED = "merged"
    USER_COMMENT = "user_comment"


class RepoProvider(ABC):
    """Interface for repo-hosting operations (PRs, pushes, reviews)."""

    @abstractmethod
    async def find_pr_for_branch(self, branch: str) -> PullRequestInfo | None: ...

    @abstractmethod
    async def find_merged_pr_for_branch(self, branch: str) -> PullRequestInfo | None: ...

    @abstractmethod
    async def create_pull_request(
        self, head: str, base: str, title: str, body: str,
    ) -> PullRequestInfo: ...

    @abstractmethod
    async def push_branch(self, branch: str, worktree_path: str) -> None: ...

    @abstractmethod
    async def post_review(self, pr_number: int, verdict: str, body: str) -> None: ...

    @abstractmethod
    async def post_comment(self, pr_number: int, body: str) -> None: ...

    @abstractmethod
    async def merge_pr(self, pr_number: int, method: str) -> None: ...

    @abstractmethod
    async def count_pr_interactions(self, pr_number: int) -> int: ...

    @abstractmethod
    async def check_pr_for_event(
        self, pr_number: int, initial_interaction_count: int,
    ) -> tuple[PrPollResult, str] | None: ...

    @abstractmethod
    async def poll_pr_for_event(self, pr_number: int) -> tuple[PrPollResult, str]: ...

    @abstractmethod
    async def poll_pr_merged(self, pr_number: int) -> None: ...
