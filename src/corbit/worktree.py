"""Git worktree create/cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path

from corbit.models import WorktreeInfo

_WORKTREE_PREFIX = "corbit/issue-"
_WORKTREE_DIR = ".corbit-worktrees"


def _worktree_base() -> Path:
    return Path.cwd() / _WORKTREE_DIR


def branch_name_for(issue_slug: str) -> str:
    return f"{_WORKTREE_PREFIX}{issue_slug}"


async def _run_git(*args: str, cwd: str | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _branch_exists(branch: str) -> bool:
    """Check if a local branch exists."""
    try:
        await _run_git("rev-parse", "--verify", f"refs/heads/{branch}")
        return True
    except RuntimeError:
        return False


async def _rebase_worktree_onto_base(worktree_path: Path, branch: str, base_branch: str) -> None:
    """Rebase the worktree branch onto the latest origin/base_branch.

    If the rebase conflicts (e.g. stale partial work), abort and reset the
    branch to origin/base_branch so the coder starts from a clean slate.
    In both cases, force-push so the remote branch matches — otherwise the
    coder will find a diverged remote and try to merge stale history back in.
    """
    cwd = str(worktree_path)
    # Abort any in-progress rebase left behind by a previous run or coder agent
    abort = await asyncio.create_subprocess_exec(
        "git", "rebase", "--abort",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await abort.communicate()  # ignore errors — no-op if no rebase in progress

    needs_force_push = False

    proc = await asyncio.create_subprocess_exec(
        "git", "rebase", f"origin/{base_branch}",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode != 0:
        # Abort the failed rebase
        abort = await asyncio.create_subprocess_exec(
            "git", "rebase", "--abort",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await abort.communicate()
        # Reset to latest base so the coder starts fresh on current main
        await _run_git("reset", "--hard", f"origin/{base_branch}", cwd=cwd)
        needs_force_push = True
    else:
        # Check if rebase rewrote any commits (local diverged from remote)
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", branch,
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        local_out, _ = await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", f"origin/{branch}",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        remote_out, _ = await proc.communicate()
        if proc.returncode == 0 and local_out.strip() != remote_out.strip():
            needs_force_push = True

    # Force-push to sync the remote branch so the coder doesn't encounter
    # a diverged remote and try to merge stale commits back in.
    # We use --force (not --force-with-lease) because we intentionally
    # rewrote history and didn't fetch the feature branch ref.
    if needs_force_push:
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "--force", "origin", branch,
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # Ignore push errors — remote branch may not exist yet


async def create_worktree(issue_slug: str, base_branch: str) -> WorktreeInfo:
    """Create a git worktree for the given issue slug, or reuse an existing one."""
    branch = branch_name_for(issue_slug)
    worktree_path = _worktree_base() / f"issue-{issue_slug}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    # Fetch latest
    await _run_git("fetch", "origin", base_branch)

    if worktree_path.exists():
        # Worktree already exists — rebase onto latest base so the coder
        # doesn't start from a stale main (which causes conflicts later).
        await _rebase_worktree_onto_base(worktree_path, branch, base_branch)
        return WorktreeInfo(
            issue_slug=issue_slug,
            branch_name=branch,
            path=worktree_path,
            base_branch=base_branch,
        )

    if await _branch_exists(branch):
        # Branch exists but worktree directory is gone — re-attach
        await _run_git(
            "worktree", "add",
            str(worktree_path),
            branch,
        )
        # Rebase onto latest base to avoid stale-main conflicts
        await _rebase_worktree_onto_base(worktree_path, branch, base_branch)
    else:
        # Fresh start — create worktree with new branch from base
        await _run_git(
            "worktree", "add",
            "-b", branch,
            str(worktree_path),
            f"origin/{base_branch}",
        )

    return WorktreeInfo(
        issue_slug=issue_slug,
        branch_name=branch,
        path=worktree_path,
        base_branch=base_branch,
    )


async def remove_worktree(worktree: WorktreeInfo) -> None:
    """Remove a worktree and its branch."""
    try:
        await _run_git("worktree", "remove", str(worktree.path), "--force")
    except RuntimeError:
        pass  # Already removed

    try:
        await _run_git("branch", "-D", worktree.branch_name)
    except RuntimeError:
        pass  # Branch may already be deleted


async def cleanup_all_worktrees() -> list[str]:
    """Remove all corbit worktrees. Returns list of removed paths."""
    removed: list[str] = []
    raw = await _run_git("worktree", "list", "--porcelain")
    current_path: str | None = None
    current_branch: str | None = None

    for line in raw.splitlines():
        if line.startswith("worktree "):
            current_path = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current_branch = line.split(" ", 1)[1]
            ref_prefix = f"refs/heads/{_WORKTREE_PREFIX}"
            if current_branch.startswith(ref_prefix) and current_path:
                branch = current_branch.removeprefix("refs/heads/")
                info = WorktreeInfo(
                    issue_slug="",
                    branch_name=branch,
                    path=Path(current_path),
                    base_branch="",
                )
                await remove_worktree(info)
                removed.append(current_path)
        elif line == "":
            current_path = None
            current_branch = None

    return removed


async def cleanup_issue_worktree(issue_slug: str) -> bool:
    """Remove the worktree for a specific issue slug. Returns True if found and removed."""
    branch = branch_name_for(issue_slug)
    raw = await _run_git("worktree", "list", "--porcelain")
    current_path: str | None = None

    for line in raw.splitlines():
        if line.startswith("worktree "):
            current_path = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current_branch = line.split(" ", 1)[1]
            if current_branch == f"refs/heads/{branch}" and current_path:
                info = WorktreeInfo(
                    issue_slug=issue_slug,
                    branch_name=branch,
                    path=Path(current_path),
                    base_branch="",
                )
                await remove_worktree(info)
                return True
        elif line == "":
            current_path = None

    return False
