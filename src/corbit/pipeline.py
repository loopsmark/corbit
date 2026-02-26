"""Single-issue lifecycle — the core pipeline loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from corbit import linear as linear_ops
from corbit.agents.registry import get_agent
from corbit.github import create_pull_request, find_pr_for_branch, push_branch
from corbit.models import (
    CorbitConfig,
    Issue,
    IssueSource,
    IterationMode,
    LinearIssue,
    PipelineState,
    PipelineStatus,
    ReviewVerdict,
    WorktreeInfo,
)
from corbit.prompts import CoderContext, build_coder_prompt
from corbit.reviewer import Reviewer
from corbit.worktree import create_worktree, remove_worktree

console = Console()

_STATE_FILE = ".corbit-state.json"


def _state_path(worktree: WorktreeInfo) -> Path:
    return worktree.path / _STATE_FILE


def _save_state(worktree: WorktreeInfo, step: str, **extra: object) -> None:
    """Persist pipeline progress to the worktree."""
    data: dict[str, object] = {"step": step, **extra}
    _state_path(worktree).write_text(json.dumps(data, indent=2) + "\n")


def _load_state(worktree: WorktreeInfo) -> dict[str, object]:
    """Load saved pipeline state, or empty dict if none."""
    path = _state_path(worktree)
    if path.exists():
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    return {}


async def _git_sync(worktree: WorktreeInfo) -> None:
    """Fetch and fast-forward the worktree branch to match the remote."""
    cwd = str(worktree.path)
    # Fetch latest remote refs
    proc = await asyncio.create_subprocess_exec(
        "git", "fetch", "origin",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    # Fast-forward local branch to match remote (handles re-created worktrees)
    proc = await asyncio.create_subprocess_exec(
        "git", "merge", "--ff-only", f"origin/{worktree.branch_name}",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    # Also update local main ref so `git diff main...HEAD` works
    proc = await asyncio.create_subprocess_exec(
        "git", "fetch", "origin", f"{worktree.base_branch}:{worktree.base_branch}",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _rebase_onto_base(worktree: WorktreeInfo) -> None:
    """Rebase the feature branch onto the latest origin/base_branch and force-push.

    This keeps the PR mergeable even when other PRs have landed on main
    since the worktree was created (parallel runs or sequential runs without
    wait_for_merge).  If the rebase produces conflicts the rebase is aborted
    and a RuntimeError is raised so the pipeline can surface a clear failure.
    """
    cwd = str(worktree.path)

    # Abort any in-progress rebase left behind by the coder agent
    abort = await asyncio.create_subprocess_exec(
        "git", "rebase", "--abort",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await abort.communicate()  # ignore errors — no-op if no rebase in progress

    # Fetch the latest base branch
    proc = await asyncio.create_subprocess_exec(
        "git", "fetch", "origin", worktree.base_branch,
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Rebase onto it
    proc = await asyncio.create_subprocess_exec(
        "git", "rebase", f"origin/{worktree.base_branch}",
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Abort so the worktree is left in a clean state
        abort = await asyncio.create_subprocess_exec(
            "git", "rebase", "--abort",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await abort.communicate()
        raise RuntimeError(
            f"Rebase onto origin/{worktree.base_branch} failed (merge conflict). "
            f"Manual resolution required.\n{stderr.decode().strip()}"
        )

    # Force-push to update the remote branch (and any open PR)
    proc = await asyncio.create_subprocess_exec(
        "git", "push", "--force-with-lease", "origin", worktree.branch_name,
        cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Force-push after rebase failed: {stderr.decode().strip()}")


async def _has_uncommitted_changes(worktree: WorktreeInfo) -> bool:
    """Check if the worktree has uncommitted changes (staged or unstaged)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=str(worktree.path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return bool(stdout.decode().strip())


async def _debug_checkpoint(config: CorbitConfig, step: str, detail: str = "") -> None:
    """In debug mode, show step info and wait for user confirmation."""
    if not config.debug:
        return

    body = f"[bold cyan]Next step:[/] {step}"
    if detail:
        body += f"\n\n{detail}"

    console.print(Panel(body, title="[bold yellow]DEBUG[/]", border_style="yellow"))

    response = await asyncio.to_thread(
        input, "  Press Enter to continue, or 'q' to abort: "
    )
    if response.strip().lower() == "q":
        raise KeyboardInterrupt("Aborted by user in debug mode")


async def _maybe_post_linear_comment(
    issue: Issue,
    body: str,
    config: CorbitConfig,
) -> None:
    """Fire-and-forget comment on a Linear issue. Never blocks the pipeline."""
    if not isinstance(issue, LinearIssue) or not config.linear_post_comment:
        return
    try:
        await linear_ops.post_comment(
            issue.identifier,
            body,
            api_key=config.linear_api_key or None,
        )
    except Exception as exc:
        console.print(f"[yellow]Warning: Linear comment failed: {exc}[/]")


async def run_pipeline(issue: Issue, config: CorbitConfig) -> PipelineState:
    """Execute the full pipeline for a single issue."""
    state = PipelineState(issue_slug=issue.slug, source=issue.source)
    agent = get_agent(config.coder_backend, model=config.coder_model, skip_permissions=config.skip_permissions)
    reviewer = Reviewer(backend=config.reviewer_backend, model=config.reviewer_model, skip_permissions=config.skip_permissions)

    try:
        console.print(f"[bold blue]{issue.display_id}[/] {issue.title}")

        if config.debug:
            comment_info = f" | {len(issue.comments)} comment(s)" if issue.comments else ""
            console.print(Panel(
                issue.to_prompt(),
                title=f"Issue fetched{comment_info}",
                border_style="green",
            ))

        # 1. Create worktree
        await _debug_checkpoint(
            config, "Create worktree",
            f"Branch: corbit/issue-{issue.slug}\nBase: {config.main_branch}",
        )
        worktree = await create_worktree(issue.slug, config.main_branch)
        state.worktree = worktree
        console.print(f"[bold blue]{issue.display_id}[/] Worktree ready at {worktree.path}")

        agent_label = f"{issue.display_id} [coder/{config.coder_backend.value}]"
        saved = _load_state(worktree)
        saved_step = str(saved.get("step", ""))

        # 2. Implementation + push + PR (skip if already done)
        pr = await find_pr_for_branch(worktree.branch_name)
        if pr is not None and saved_step in ("implemented", "reviewed", "feedback_applied"):
            agent_label = f"{issue.display_id} PR#{pr.number} [coder/{config.coder_backend.value}]"
            console.print(
                f"[bold green]{agent_label}[/] PR already exists: {pr.url}"
            )
            session_id = str(saved.get("session_id", "")) or None
            state.pr = pr
        else:
            has_partial = await _has_uncommitted_changes(worktree)
            saved_session = str(saved.get("session_id", "")) or None

            prompt = build_coder_prompt(CoderContext(
                branch_name=worktree.branch_name,
                base_branch=worktree.base_branch,
                issue_slug=issue.slug,
                issue_url=issue.url,
                issue_prompt=issue.to_prompt(),
                has_partial_work=has_partial,
                is_resume=bool(saved_session),
            ))

            resume_note = " (resuming session)" if saved_session else (
                " (resuming partial work)" if has_partial else ""
            )
            await _debug_checkpoint(
                config, "Run coder agent",
                f"Backend: {config.coder_backend.value}\n"
                f"Timeout: {config.agent_timeout}s\n"
                f"Worktree: {worktree.path}\n"
                f"Partial work: {'yes' if has_partial else 'no'}\n"
                f"Session: {saved_session or 'new'}\n\n"
                f"[dim]Prompt preview:[/]\n{prompt[:300]}...",
            )
            state.status = PipelineStatus.IMPLEMENTING
            console.print(f"[bold blue]{agent_label}[/] Running coder agent...{resume_note}")
            result = await agent.implement(
                prompt,
                worktree.path,
                session_id=saved_session,
                timeout=config.agent_timeout,
                label=agent_label,
            )

            if not result.success:
                state.status = PipelineStatus.FAILED
                state.error = f"Coder agent failed: {result.error}"
                console.print(f"[bold red]{issue.display_id}[/] {state.error}")
                await _maybe_post_linear_comment(
                    issue,
                    f"❌ Pipeline failed: {state.error}",
                    config,
                )
                return state

            if config.debug:
                output_preview = result.output[:500] if result.output else "(no output)"
                console.print(Panel(
                    f"[green]Success[/]\n\n{output_preview}",
                    title="Coder agent result",
                    border_style="green",
                ))

            session_id = result.session_id

            # Discover the PR the agent created (before rebase so we can
            # save state and allow resumption if rebase fails).
            pr = await find_pr_for_branch(worktree.branch_name)
            if pr is None:
                # Agent didn't create a PR — fall back to creating one ourselves
                console.print(f"[bold yellow]{agent_label}[/] Agent didn't create a PR, creating...")
                await push_branch(worktree.branch_name, str(worktree.path))
                if issue.source == IssueSource.GITHUB:
                    pr_body = (
                        f"Closes #{issue.slug}\n\n"
                        f"Automated implementation by Corbit using `{config.coder_backend.value}`."
                    )
                else:
                    pr_body = (
                        f"Implements {issue.url}\n\n"
                        f"Automated implementation by Corbit using `{config.coder_backend.value}`."
                    )
                pr = await create_pull_request(
                    head_branch=worktree.branch_name,
                    base_branch=worktree.base_branch,
                    title=f"fix: resolve {issue.display_id} — {issue.title}",
                    body=pr_body,
                )

            state.pr = pr
            agent_label = f"{issue.display_id} PR#{pr.number} [coder/{config.coder_backend.value}]"
            console.print(f"[bold blue]{issue.display_id}[/] PR: {pr.url}")
            _save_state(
                worktree, "implemented",
                session_id=session_id or "",
                pr_number=pr.number,
                pr_url=pr.url,
            )
            await _maybe_post_linear_comment(
                issue,
                f"🤖 PR created by Corbit: {pr.url}",
                config,
            )

            # Rebase onto latest base branch so the PR stays mergeable
            # even when other PRs landed on main while being implemented.
            try:
                await _rebase_onto_base(worktree)
            except RuntimeError as exc:
                state.status = PipelineStatus.FAILED
                state.error = str(exc)
                console.print(f"[bold red]{issue.display_id}[/] {state.error}")
                await _maybe_post_linear_comment(issue, f"❌ Pipeline failed: {state.error}", config)
                return state

        # 3. Review loop (skip if single-pass)
        if config.iteration_mode == IterationMode.SINGLE_PASS:
            state.status = PipelineStatus.APPROVED
            console.print(
                f"[bold green]{issue.display_id}[/] Single-pass mode — skipping review"
            )
            return state

        # Determine where to resume in the review loop
        start_round = 1
        pending_feedback = ""
        last_review_comments = ""
        if saved_step == "reviewed":
            start_round = int(saved.get("review_round", 1))
            pending_feedback = str(saved.get("review_comments", ""))
            last_review_comments = pending_feedback
        elif saved_step == "feedback_applied":
            start_round = int(saved.get("review_round", 1)) + 1
            last_review_comments = str(saved.get("review_comments", ""))

        for round_num in range(start_round, config.max_review_rounds + 1):
            # If we have pending feedback from a previous run, apply it first
            if pending_feedback:
                await _debug_checkpoint(
                    config, "Apply review feedback (resumed)",
                    f"Feedback:\n{pending_feedback[:300]}",
                )
                console.print(
                    f"[bold yellow]{issue.display_id}[/] Applying saved review feedback..."
                )
                await _maybe_post_linear_comment(
                    issue,
                    f"🔧 Applying review feedback (round {round_num})...",
                    config,
                )
                state.status = PipelineStatus.IMPLEMENTING
                result = await agent.apply_feedback(
                    pending_feedback,
                    worktree.path,
                    session_id=session_id,
                    timeout=config.agent_timeout,
                    label=agent_label,
                )

                if not result.success:
                    state.status = PipelineStatus.FAILED
                    state.error = f"Coder agent failed on feedback: {result.error}"
                    console.print(f"[bold red]{issue.display_id}[/] {state.error}")
                    await _maybe_post_linear_comment(
                        issue,
                        f"❌ Pipeline failed: {state.error}",
                        config,
                    )
                    return state

                session_id = result.session_id or session_id
                _save_state(
                    worktree, "feedback_applied",
                    session_id=session_id or "",
                    pr_number=pr.number,
                    pr_url=pr.url,
                    review_round=round_num,
                    review_comments=last_review_comments,
                )
                pending_feedback = ""
                continue  # proceed to next round for review

            # Ensure remote refs are up to date before reviewing
            await _git_sync(worktree)

            await _debug_checkpoint(
                config, f"Review round {round_num}/{config.max_review_rounds}",
                f"PR: #{pr.number}\nReviewer backend: {config.reviewer_backend.value}\nReviewer will evaluate the diff",
            )
            state.status = PipelineStatus.REVIEWING
            state.current_round = round_num
            console.print(
                f"[bold blue]{issue.display_id}[/] Review round {round_num}/{config.max_review_rounds}..."
            )

            reviewer_label = f"{issue.display_id} PR#{pr.number} [reviewer/{config.reviewer_backend.value}]"
            review = await reviewer.review(
                pr,
                worktree.path,
                timeout=config.agent_timeout,
                label=reviewer_label,
                round_number=round_num,
                previous_feedback=last_review_comments,
            )
            state.review_history.append(review)

            if config.debug:
                verdict_style = {
                    ReviewVerdict.APPROVED: "green",
                    ReviewVerdict.CHANGES_REQUESTED: "yellow",
                    ReviewVerdict.ERROR: "red",
                }.get(review.verdict, "white")
                console.print(Panel(
                    f"[{verdict_style}]Verdict: {review.verdict.value}[/{verdict_style}]\n\n"
                    f"{review.comments[:500]}",
                    title=f"Review round {round_num}",
                    border_style=verdict_style,
                ))

            if review.verdict == ReviewVerdict.APPROVED:
                state.status = PipelineStatus.APPROVED
                console.print(f"[bold green]{issue.display_id}[/] Approved!")
                await _maybe_post_linear_comment(
                    issue,
                    f"✅ Implementation approved after {round_num} review round(s). PR: {pr.url}",
                    config,
                )
                return state

            if review.verdict == ReviewVerdict.ERROR:
                state.status = PipelineStatus.FAILED
                state.error = f"Reviewer error: {review.comments}"
                console.print(f"[bold red]{issue.display_id}[/] {state.error}")
                await _maybe_post_linear_comment(
                    issue,
                    f"❌ Pipeline failed: {state.error}",
                    config,
                )
                return state

            # Post review round findings to Linear
            if review.items:
                items_text = "\n".join(
                    f"- {item.severity.value}: {item.comment}"
                    for item in review.items[:5]
                )
                round_comment = (
                    f"🔍 Review round {round_num}: changes requested\n\n{items_text}"
                )
            else:
                round_comment = f"🔍 Review round {round_num}: changes requested"
            await _maybe_post_linear_comment(issue, round_comment, config)

            last_review_comments = review.comments

            # Save state after review — so we can resume with feedback
            _save_state(
                worktree, "reviewed",
                session_id=session_id or "",
                pr_number=pr.number,
                pr_url=pr.url,
                review_round=round_num,
                review_comments=review.comments,
            )

            # Changes requested — apply feedback
            await _debug_checkpoint(
                config, "Apply review feedback",
                f"Feedback:\n{review.comments[:300]}",
            )
            console.print(
                f"[bold yellow]{issue.display_id}[/] Changes requested, applying feedback..."
            )
            await _maybe_post_linear_comment(
                issue,
                f"🔧 Applying review feedback (round {round_num})...",
                config,
            )
            state.status = PipelineStatus.IMPLEMENTING
            result = await agent.apply_feedback(
                review.comments,
                worktree.path,
                session_id=session_id,
                timeout=config.agent_timeout,
                label=agent_label,
            )

            if not result.success:
                state.status = PipelineStatus.FAILED
                state.error = f"Coder agent failed on feedback: {result.error}"
                console.print(f"[bold red]{issue.display_id}[/] {state.error}")
                await _maybe_post_linear_comment(
                    issue,
                    f"❌ Pipeline failed: {state.error}",
                    config,
                )
                return state

            session_id = result.session_id or session_id

            _save_state(
                worktree, "feedback_applied",
                session_id=session_id or "",
                pr_number=pr.number,
                pr_url=pr.url,
                review_round=round_num,
                review_comments=review.comments,
            )

        # Exhausted review rounds
        state.status = PipelineStatus.FAILED
        state.error = f"Exhausted {config.max_review_rounds} review rounds without approval"
        console.print(f"[bold red]{issue.display_id}[/] {state.error}")
        await _maybe_post_linear_comment(
            issue,
            f"❌ Pipeline failed: {state.error}",
            config,
        )

    except KeyboardInterrupt:
        state.status = PipelineStatus.FAILED
        state.error = "Aborted by user"
        console.print(f"[bold red]{issue.display_id}[/] Aborted by user")

    except Exception as exc:
        state.status = PipelineStatus.FAILED
        state.error = str(exc)
        console.print(f"[bold red]{issue.display_id}[/] Error: {exc}")
        await _maybe_post_linear_comment(
            issue,
            f"❌ Pipeline failed: {exc}",
            config,
        )

    finally:
        if state.worktree and state.status in (PipelineStatus.APPROVED, PipelineStatus.MERGED):
            # Clean up state file and worktree on success
            sp = _state_path(state.worktree)
            if sp.exists():
                sp.unlink()
            try:
                await remove_worktree(state.worktree)
                console.print(
                    f"[bold blue]{issue.display_id}[/] Worktree cleaned up"
                )
            except Exception:
                pass

    return state
