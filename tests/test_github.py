"""Tests for GitHub module (unit tests with mocked subprocess)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import corbit.github
from corbit.github import fetch_issue, get_repo_info


@pytest.fixture(autouse=True)
def _reset_repo_slug_cache() -> None:
    """Reset the cached repo slug between tests."""
    corbit.github._repo_slug = None


@pytest.mark.asyncio
async def test_get_repo_info() -> None:
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (
        json.dumps({"owner": {"login": "loopsmark"}, "name": "corbit"}).encode(),
        b"",
    )

    with patch("corbit.github.asyncio.create_subprocess_exec", return_value=mock_proc):
        owner, repo = await get_repo_info()
        assert owner == "loopsmark"
        assert repo == "corbit"


@pytest.mark.asyncio
async def test_fetch_issue() -> None:
    issue_data = {
        "number": 42,
        "title": "Fix the bug",
        "body": "Something is broken",
        "labels": [{"name": "bug"}],
        "url": "https://github.com/loopsmark/corbit/issues/42",
        "comments": [
            {"author": {"login": "alice"}, "body": "I can reproduce this"},
            {"author": {"login": "bob"}, "body": "Same here"},
        ],
    }
    repo_data = {"owner": {"login": "loopsmark"}, "name": "corbit"}

    mock_proc = AsyncMock()
    mock_proc.returncode = 0

    call_count = 0

    async def mock_communicate() -> tuple[bytes, bytes]:
        nonlocal call_count
        call_count += 1
        # Call 1: _ensure_repo_slug → repo view
        # Call 2: fetch_issue → issue view
        # Call 3: get_repo_info → repo view (uses cached slug)
        if call_count == 2:
            return json.dumps(issue_data).encode(), b""
        return json.dumps(repo_data).encode(), b""

    mock_proc.communicate = mock_communicate

    with patch("corbit.github.asyncio.create_subprocess_exec", return_value=mock_proc):
        issue = await fetch_issue(42)
        assert issue.number == 42
        assert issue.title == "Fix the bug"
        assert issue.labels == ["bug"]
        assert len(issue.comments) == 2
        assert issue.comments[0].author == "alice"
        assert issue.comments[0].body == "I can reproduce this"
        assert issue.repo_owner == "loopsmark"


@pytest.mark.asyncio
async def test_gh_command_failure() -> None:
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (b"", b"not found")

    with patch("corbit.github.asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="not found"):
            await get_repo_info()
