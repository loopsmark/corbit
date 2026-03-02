"""GitHub issue operations via the gh CLI.

Repo-hosting operations (PRs, pushes, reviews) have been moved to
``corbit.repo.github.GitHubRepoProvider``.
"""

from __future__ import annotations

import asyncio
import json

from corbit.models import GitHubIssue, IssueComment


# Cached repo slug (owner/repo) — resolved once, used by all gh commands
_repo_slug: str | None = None


async def _run_gh(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _ensure_repo_slug() -> str:
    """Resolve and cache the owner/repo slug for the current repository."""
    global _repo_slug  # noqa: PLW0603
    if _repo_slug is None:
        raw = await _run_gh("repo", "view", "--json", "owner,name")
        data = json.loads(raw)
        _repo_slug = f"{data['owner']['login']}/{data['name']}"
    return _repo_slug


async def _run_gh_repo(*args: str) -> str:
    """Run a gh command with --repo owner/repo to avoid cwd ambiguity."""
    slug = await _ensure_repo_slug()
    return await _run_gh(*args, "--repo", slug)


async def get_repo_info() -> tuple[str, str]:
    """Return (owner, repo) for the current repository."""
    slug = await _ensure_repo_slug()
    owner, repo = slug.split("/", 1)
    return owner, repo


async def fetch_issue(issue_number: int) -> GitHubIssue:
    """Fetch a GitHub issue by number, including comments."""
    raw = await _run_gh_repo(
        "issue", "view", str(issue_number),
        "--json", "number,title,body,labels,url,comments",
    )
    data = json.loads(raw)
    owner, repo = await get_repo_info()
    comments = [
        IssueComment(
            author=c.get("author", {}).get("login", "unknown"),
            body=c.get("body", "").strip(),
        )
        for c in data.get("comments", [])
        if c.get("body", "").strip()
    ]
    return GitHubIssue(
        number=data["number"],
        title=data["title"],
        body=data.get("body", "") or "",
        labels=[label["name"] for label in data.get("labels", [])],
        comments=comments,
        url=data.get("url", ""),
        repo_owner=owner,
        repo_name=repo,
    )


async def fetch_comments(issue_number: int) -> list[IssueComment]:
    """Fetch comments for a GitHub issue by number."""
    raw = await _run_gh_repo(
        "issue", "view", str(issue_number),
        "--json", "comments",
    )
    data = json.loads(raw)
    return [
        IssueComment(
            author=c.get("author", {}).get("login", "unknown"),
            body=c.get("body", "").strip(),
        )
        for c in data.get("comments", [])
        if c.get("body", "").strip()
    ]
