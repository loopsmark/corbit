"""GitHub operations via the gh CLI."""

from __future__ import annotations

import asyncio
import json

from corbit.models import GitHubIssue, IssueComment, PullRequestInfo

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


async def get_repo_info() -> tuple[str, str]:
    """Return (owner, repo) for the current repository."""
    raw = await _run_gh(
        "repo", "view", "--json", "owner,name",
    )
    data = json.loads(raw)
    return data["owner"]["login"], data["name"]


async def fetch_issue(issue_number: int) -> GitHubIssue:
    """Fetch a GitHub issue by number, including comments."""
    raw = await _run_gh(
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


async def find_pr_for_branch(branch: str) -> PullRequestInfo | None:
    """Find an open PR for the given head branch, or None."""
    try:
        raw = await _run_gh(
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


async def create_pull_request(
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> PullRequestInfo:
    """Create a pull request and return its info."""
    # gh pr create returns the PR URL on stdout
    url = await _run_gh(
        "pr", "create",
        "--head", head_branch,
        "--base", base_branch,
        "--title", title,
        "--body", body,
    )
    # Fetch the PR details by branch to get number etc.
    pr = await find_pr_for_branch(head_branch)
    if pr is not None:
        return pr
    # Fallback: parse URL for PR number
    pr_number = int(url.rstrip("/").split("/")[-1])
    return PullRequestInfo(
        number=pr_number,
        url=url,
        head_branch=head_branch,
        base_branch=base_branch,
    )


async def post_pr_review(
    pr_number: int,
    verdict: str,
    body: str,
) -> None:
    """Post a review on a PR (approve or request changes).

    Falls back to a plain comment if the authenticated user owns the PR
    (GitHub disallows request-changes on your own PR).
    """
    if verdict == "approved":
        try:
            await _run_gh("pr", "review", str(pr_number), "--approve", "--body", body or "LGTM")
        except RuntimeError:
            # Own PR — GitHub disallows approving your own PR, post as comment
            await _run_gh("pr", "comment", str(pr_number), "--body", f"✅ **Approved**\n\n{body}" if body else "✅ **Approved** — LGTM")
        return

    try:
        await _run_gh("pr", "review", str(pr_number), "--request-changes", "--body", body)
    except RuntimeError:
        # Own PR — post as a regular comment instead
        await _run_gh("pr", "comment", str(pr_number), "--body", body)


async def post_pr_comment(pr_number: int, body: str) -> None:
    """Post a single comment on a PR."""
    await _run_gh("pr", "comment", str(pr_number), "--body", body)


async def get_pr_review_comments(pr_number: int) -> str:
    """Fetch all review comments on a PR as a formatted string."""
    raw = await _run_gh(
        "pr", "view", str(pr_number),
        "--json", "reviews,comments",
    )
    data = json.loads(raw)
    parts: list[str] = []
    for review in data.get("reviews", []):
        body = review.get("body", "").strip()
        if body:
            state = review.get("state", "COMMENTED")
            parts.append(f"[{state}] {body}")
    for comment in data.get("comments", []):
        body = comment.get("body", "").strip()
        if body:
            parts.append(f"[COMMENT] {body}")
    return "\n\n---\n\n".join(parts) if parts else ""


async def find_merged_pr_for_branch(branch: str) -> PullRequestInfo | None:
    """Return PR info if a merged PR exists for the given head branch, else None."""
    try:
        raw = await _run_gh(
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


async def poll_pr_merged(pr_number: int) -> None:
    """Block until the PR is merged on GitHub (polls every _PR_POLL_INTERVAL seconds)."""
    while True:
        raw = await _run_gh("pr", "view", str(pr_number), "--json", "state")
        data = json.loads(raw)
        if data["state"] == "MERGED":
            return
        await asyncio.sleep(_PR_POLL_INTERVAL)


async def merge_pr(pr_number: int, method: str = "squash") -> None:
    """Merge a pull request using the given method (squash, merge, rebase)."""
    await _run_gh(
        "pr", "merge", str(pr_number),
        f"--{method}",
        "--delete-branch",
    )


async def push_branch(branch_name: str, worktree_path: str) -> None:
    """Push a branch to origin from within a worktree."""
    proc = await asyncio.create_subprocess_exec(
        "git", "push", "--set-upstream", "origin", branch_name,
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git push failed: {stderr.decode().strip()}")
