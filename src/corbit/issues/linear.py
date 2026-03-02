"""Linear issue provider — fetches issues and posts comments via GraphQL."""

from __future__ import annotations

from corbit import linear as linear_ops
from corbit.issues.base import IssueProvider
from corbit.models import Issue, IssueComment


class LinearIssueProvider(IssueProvider):
    """Fetches Linear issues and posts comments."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def fetch_issue(self, identifier: str) -> Issue:
        return await linear_ops.fetch_issue(identifier, api_key=self._api_key)

    async def post_comment(self, identifier: str, body: str) -> None:
        await linear_ops.post_comment(identifier, body, api_key=self._api_key)

    async def fetch_comments(self, identifier: str) -> list[IssueComment]:
        return await linear_ops.fetch_comments(identifier, api_key=self._api_key)
