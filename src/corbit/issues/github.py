"""GitHub issue provider — fetches issues via the gh CLI."""

from __future__ import annotations

from corbit.github import fetch_comments as _gh_fetch_comments
from corbit.github import fetch_issue as _gh_fetch_issue
from corbit.issues.base import IssueProvider
from corbit.models import Issue, IssueComment


class GitHubIssueProvider(IssueProvider):
    """Fetches GitHub issues. Comment posting is a no-op (comments go via PRs)."""

    async def fetch_issue(self, identifier: str) -> Issue:
        return await _gh_fetch_issue(int(identifier))

    async def post_comment(self, identifier: str, body: str) -> None:
        # GitHub issue comments are posted through PRs, not the issue directly.
        pass

    async def fetch_comments(self, identifier: str) -> list[IssueComment]:
        return await _gh_fetch_comments(int(identifier))
