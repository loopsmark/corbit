"""GitHub repo provider — implements RepoProvider via the gh CLI."""

from __future__ import annotations

import asyncio
import json

from corbit.models import PullRequestInfo
from corbit.repo.base import PrPollResult, RepoProvider

_PR_POLL_INTERVAL = 30  # seconds between GitHub PR state checks


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


class GitHubRepoProvider(RepoProvider):
    """GitHub implementation of RepoProvider using the ``gh`` CLI."""

    def __init__(self) -> None:
        self._repo_slug: str | None = None
        self._gh_username: str | None = None

    async def _ensure_repo_slug(self) -> str:
        """Resolve and cache the owner/repo slug for the current repository."""
        if self._repo_slug is None:
            raw = await _run_gh("repo", "view", "--json", "owner,name")
            data = json.loads(raw)
            self._repo_slug = f"{data['owner']['login']}/{data['name']}"
        return self._repo_slug

    async def _run_gh_repo(self, *args: str) -> str:
        """Run a gh command with --repo owner/repo to avoid cwd ambiguity."""
        slug = await self._ensure_repo_slug()
        return await _run_gh(*args, "--repo", slug)

    async def get_repo_info(self) -> tuple[str, str]:
        """Return (owner, repo) for the current repository."""
        slug = await self._ensure_repo_slug()
        owner, repo = slug.split("/", 1)
        return owner, repo

    async def find_pr_for_branch(self, branch: str) -> PullRequestInfo | None:
        try:
            raw = await self._run_gh_repo(
                "pr", "view", branch,
                "--json", "number,url,headRefName,baseRefName",
            )
        except RuntimeError:
            return None
        data = json.loads(raw)
        return PullRequestInfo(
            number=data["number"],
            url=data["url"],
            head_branch=data["headRefName"],
            base_branch=data["baseRefName"],
        )

    async def find_merged_pr_for_branch(self, branch: str) -> PullRequestInfo | None:
        try:
            raw = await self._run_gh_repo(
                "pr", "list",
                "--head", branch,
                "--state", "merged",
                "--json", "number,url,headRefName,baseRefName",
                "--limit", "1",
            )
        except RuntimeError:
            return None
        items = json.loads(raw)
        if not items:
            return None
        item = items[0]
        return PullRequestInfo(
            number=item["number"],
            url=item["url"],
            head_branch=item["headRefName"],
            base_branch=item["baseRefName"],
        )

    async def create_pull_request(
        self, head: str, base: str, title: str, body: str,
    ) -> PullRequestInfo:
        url = await self._run_gh_repo(
            "pr", "create",
            "--head", head,
            "--base", base,
            "--title", title,
            "--body", body,
        )
        pr = await self.find_pr_for_branch(head)
        if pr is not None:
            return pr
        pr_number = int(url.rstrip("/").split("/")[-1])
        return PullRequestInfo(
            number=pr_number,
            url=url,
            head_branch=head,
            base_branch=base,
        )

    async def push_branch(self, branch: str, worktree_path: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "--set-upstream", "origin", branch,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git push failed: {stderr.decode().strip()}")

    async def post_review(self, pr_number: int, verdict: str, body: str) -> None:
        if verdict == "approved":
            try:
                await self._run_gh_repo(
                    "pr", "review", str(pr_number), "--approve",
                    "--body", body or "LGTM",
                )
            except RuntimeError:
                await self._run_gh_repo(
                    "pr", "comment", str(pr_number),
                    "--body", f"✅ **Approved**\n\n{body}" if body else "✅ **Approved** — LGTM",
                )
            return

        try:
            await self._run_gh_repo(
                "pr", "review", str(pr_number), "--request-changes",
                "--body", body,
            )
        except RuntimeError:
            await self._run_gh_repo(
                "pr", "comment", str(pr_number), "--body", body,
            )

    async def post_comment(self, pr_number: int, body: str) -> None:
        await self._run_gh_repo("pr", "comment", str(pr_number), "--body", body)

    async def merge_pr(self, pr_number: int, method: str = "squash") -> None:
        await self._run_gh_repo(
            "pr", "merge", str(pr_number),
            f"--{method}",
            "--delete-branch",
        )

    async def count_pr_interactions(self, pr_number: int) -> int:
        username = await self._get_gh_username()
        raw = await self._run_gh_repo(
            "pr", "view", str(pr_number),
            "--json", "state,comments,reviews",
        )
        data = json.loads(raw)
        return _count_user_interactions(data, username)

    async def check_pr_for_event(
        self, pr_number: int, initial_interaction_count: int,
    ) -> tuple[PrPollResult, str] | None:
        username = await self._get_gh_username()
        raw = await self._run_gh_repo(
            "pr", "view", str(pr_number),
            "--json", "state,comments,reviews",
        )
        data = json.loads(raw)

        if data["state"] == "MERGED":
            return PrPollResult.MERGED, ""

        current_count = _count_user_interactions(data, username)
        if current_count > initial_interaction_count:
            new_comment = _extract_latest_user_comment(data, username)
            return PrPollResult.USER_COMMENT, new_comment

        return None

    async def poll_pr_for_event(self, pr_number: int) -> tuple[PrPollResult, str]:
        initial_count = await self.count_pr_interactions(pr_number)

        while True:
            await asyncio.sleep(_PR_POLL_INTERVAL)
            result = await self.check_pr_for_event(pr_number, initial_count)
            if result is not None:
                return result

    async def poll_pr_merged(self, pr_number: int) -> None:
        while True:
            raw = await self._run_gh_repo(
                "pr", "view", str(pr_number), "--json", "state",
            )
            data = json.loads(raw)
            if data["state"] == "MERGED":
                return
            await asyncio.sleep(_PR_POLL_INTERVAL)

    async def _get_gh_username(self) -> str:
        """Return the authenticated GitHub username (cached after first call)."""
        if self._gh_username is None:
            raw = await _run_gh("api", "user", "--jq", ".login")
            self._gh_username = raw.strip()
        return self._gh_username


def _count_user_interactions(data: dict[str, object], bot_username: str) -> int:
    """Count comments and reviews not authored by the bot user."""
    count = 0
    comments = data.get("comments", [])
    assert isinstance(comments, list)
    for c in comments:
        assert isinstance(c, dict)
        author = c.get("author", {})
        assert isinstance(author, dict)
        if author.get("login") != bot_username:
            count += 1
    reviews = data.get("reviews", [])
    assert isinstance(reviews, list)
    for r in reviews:
        assert isinstance(r, dict)
        author = r.get("author", {})
        assert isinstance(author, dict)
        if author.get("login") != bot_username and r.get("body", ""):
            count += 1
    return count


def _extract_latest_user_comment(data: dict[str, object], bot_username: str) -> str:
    """Return the body of the most recent non-bot comment or review."""
    latest = ""
    comments = data.get("comments", [])
    assert isinstance(comments, list)
    for c in comments:
        assert isinstance(c, dict)
        author = c.get("author", {})
        assert isinstance(author, dict)
        if author.get("login") != bot_username:
            body = c.get("body", "")
            assert isinstance(body, str)
            if body.strip():
                latest = body.strip()
    reviews = data.get("reviews", [])
    assert isinstance(reviews, list)
    for r in reviews:
        assert isinstance(r, dict)
        author = r.get("author", {})
        assert isinstance(author, dict)
        if author.get("login") != bot_username:
            body = r.get("body", "")
            assert isinstance(body, str)
            if body.strip():
                latest = body.strip()
    return latest
