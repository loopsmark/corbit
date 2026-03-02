"""Abstract base class for issue providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from corbit.models import Issue, IssueComment


class IssueProvider(ABC):
    """Interface for fetching issues and posting comments."""

    @abstractmethod
    async def fetch_issue(self, identifier: str) -> Issue: ...

    @abstractmethod
    async def post_comment(self, identifier: str, body: str) -> None: ...

    @abstractmethod
    async def fetch_comments(self, identifier: str) -> list[IssueComment]: ...
