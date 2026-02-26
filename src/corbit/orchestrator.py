"""Dispatch of issue pipelines — parallel or sequential."""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.table import Table

from corbit import github as github_ops
from corbit import linear as linear_ops
from corbit.github import find_merged_pr_for_branch, merge_pr, poll_pr_merged
from corbit.models import CorbitConfig, EpicPlan, Issue, LinearEpicPlan, MergeStrategy, PipelineState, PipelineStatus
from corbit.pipeline import run_pipeline
from corbit.worktree import cleanup_issue_worktree

console = Console()


async def run_issues(
    issues: list[Issue],
    config: CorbitConfig,
) -> list[PipelineState]:
    """Run pipelines for multiple issues (parallel or sequential)."""
    if config.sequential:
        return await _run_sequential(issues, config)
    return await _run_parallel(issues, config)


async def _clean_worktrees(issue_slugs: list[str]) -> None:
    """Remove existing worktrees for the given issue slugs."""
    for slug in issue_slugs:
        removed = await cleanup_issue_worktree(slug)
        if removed:
            console.print(f"[dim]Cleaned up stale worktree for {slug}[/]")


async def _run_sequential(
    issues: list[Issue],
    config: CorbitConfig,
) -> list[PipelineState]:
    """Process issues one-by-one: implement → review → merge → pull main → next."""
    if config.clean:
        await _clean_worktrees([issue.slug for issue in issues])

    console.print(
        f"[bold]Processing {len(issues)} issue(s) sequentially "
        f"(merge method: {config.merge_method.value})[/]\n"
    )

    states: list[PipelineState] = []

    for idx, issue in enumerate(issues, 1):
        console.print(
            f"[bold cyan]{'─' * 60}[/]\n"
            f"[bold cyan]Issue {idx}/{len(issues)}: {issue.display_id}[/]\n"
            f"[bold cyan]{'─' * 60}[/]"
        )

        state = await run_pipeline(issue, config)
        states.append(state)

        if state.status != PipelineStatus.APPROVED:
            console.print(
                f"[bold red]{issue.display_id}[/] Not approved — skipping merge, "
                f"continuing to next issue"
            )
            continue

        await _merge_step(state, config)

    _print_summary(states)
    return states


async def _already_merged(issue_slug: str) -> PipelineState | None:
    """Return a synthetic MERGED state if a corbit PR for this issue is already merged."""
    pr = await find_merged_pr_for_branch(f"corbit/issue-{issue_slug}")
    if pr is None:
        return None
    return PipelineState(issue_slug=issue_slug, status=PipelineStatus.MERGED, pr=pr)


async def _merge_step(state: PipelineState, config: CorbitConfig) -> bool:
    """Handle the merge phase for a single approved PR.

    Returns True if the PR ended up merged (so caller can update main).
    - auto: corbit merges the PR immediately using the configured merge method
    - wait: poll until the user merges on GitHub
    - skip: no-op, leave PR open
    """
    if config.merge_strategy == MergeStrategy.SKIP or state.pr is None:
        return False

    if config.merge_strategy == MergeStrategy.AUTO:
        console.print(
            f"\n[bold yellow]{state.issue_slug}[/] PR #{state.pr.number} approved — "
            f"merging automatically ({config.merge_method.value})..."
        )
        try:
            await merge_pr(state.pr.number, config.merge_method.value)
        except asyncio.CancelledError as exc:
            raise KeyboardInterrupt("Aborted by user") from exc
    else:  # WAIT
        console.print(
            f"\n[bold yellow]{state.issue_slug}[/] PR #{state.pr.number} approved — "
            f"please merge it on GitHub to continue:\n  {state.pr.url}\n"
        )
        console.print(f"[dim]Polling every 30s until PR #{state.pr.number} is merged...[/]")
        try:
            await poll_pr_merged(state.pr.number)
        except asyncio.CancelledError as exc:
            raise KeyboardInterrupt("Aborted by user") from exc

    state.status = PipelineStatus.MERGED
    console.print(f"[bold green]{state.issue_slug}[/] PR merged — continuing.")
    await _update_main(config.main_branch)
    return True


async def _update_main(main_branch: str) -> None:
    """Fetch and fast-forward the local main branch."""
    proc = await asyncio.create_subprocess_exec(
        "git", "fetch", "origin", main_branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "git", "merge", "--ff-only", f"origin/{main_branch}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        console.print(
            f"[yellow]Warning: could not fast-forward {main_branch}: "
            f"{stderr.decode().strip()}[/]"
        )


async def _run_parallel(
    issues: list[Issue],
    config: CorbitConfig,
) -> list[PipelineState]:
    """Run pipelines for multiple issues with bounded parallelism."""
    if config.clean:
        await _clean_worktrees([issue.slug for issue in issues])

    semaphore = asyncio.Semaphore(config.parallel_workers)

    async def _guarded(issue: Issue) -> PipelineState:
        async with semaphore:
            return await run_pipeline(issue, config)

    console.print(
        f"[bold]Processing {len(issues)} issue(s) "
        f"with {config.parallel_workers} parallel worker(s)[/]\n"
    )

    tasks = [asyncio.create_task(_guarded(issue)) for issue in issues]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise KeyboardInterrupt("Aborted by user")

    states: list[PipelineState] = []
    abort = False
    for i, result in enumerate(results):
        if isinstance(result, KeyboardInterrupt):
            abort = True
            states.append(PipelineState(
                issue_slug=issues[i].slug,
                source=issues[i].source,
                status=PipelineStatus.FAILED,
                error="Aborted by user",
            ))
        elif isinstance(result, BaseException):
            state = PipelineState(
                issue_slug=issues[i].slug,
                source=issues[i].source,
                status=PipelineStatus.FAILED,
                error=str(result),
            )
            states.append(state)
        else:
            states.append(result)

    _print_summary(states)

    if abort:
        raise KeyboardInterrupt("Aborted by user")

    return states


async def run_epic_plan(epic_plan: EpicPlan, config: CorbitConfig) -> list[PipelineState]:
    """Execute an epic plan: sequential groups, parallel within each group."""
    total_issues = sum(len(g) for g in epic_plan.groups)
    console.print(
        f"[bold]Epic #{epic_plan.parent_issue}: "
        f"{total_issues} issues across {len(epic_plan.groups)} group(s)[/]"
    )
    for i, group in enumerate(epic_plan.groups, 1):
        console.print(f"  Group {i}: {', '.join(f'#{n}' for n in group)}")
    console.print()

    if config.clean:
        all_slugs = [str(n) for group in epic_plan.groups for n in group]
        await _clean_worktrees(all_slugs)

    all_states: list[PipelineState] = []

    for group_idx, group in enumerate(epic_plan.groups, 1):
        console.print(
            f"[bold cyan]{'─' * 60}[/]\n"
            f"[bold cyan]Group {group_idx}/{len(epic_plan.groups)}: "
            f"{', '.join(f'#{n}' for n in group)}[/]\n"
            f"[bold cyan]{'─' * 60}[/]"
        )

        # Check which issues in this group already have merged PRs.
        skipped: list[PipelineState] = []
        pending_numbers: list[int] = []
        for issue_number in group:
            cached = await _already_merged(str(issue_number))
            if cached is not None:
                console.print(f"[dim]#{issue_number} already merged — skipping.[/]")
                skipped.append(cached)
            else:
                pending_numbers.append(issue_number)

        if not pending_numbers:
            console.print(f"[dim]Group {group_idx} fully complete — skipping.[/]")
            all_states.extend(skipped)
            continue

        # Fetch GitHub issues for pending items (epic is GitHub-only)
        pending_issues = [
            await github_ops.fetch_issue(n) for n in pending_numbers
        ]

        if len(pending_issues) == 1:
            run_states = [await run_pipeline(pending_issues[0], config)]
        else:
            run_states = await _run_parallel(pending_issues, config)

        group_states = skipped + run_states
        all_states.extend(group_states)

        # Handle merges one at a time (no-op when merge_strategy is skip).
        for state in group_states:
            if state.status == PipelineStatus.APPROVED:
                await _merge_step(state, config)

        # Stop if anything in this group failed (implementation or merge).
        # When merge_strategy is skip, APPROVED counts as success so the epic
        # keeps going but each group branches from the same original main.
        success_statuses = {PipelineStatus.MERGED, PipelineStatus.APPROVED}
        failed = [s for s in group_states if s.status not in success_statuses]
        if failed:
            console.print(
                f"[bold red]Group {group_idx} had failures: "
                f"{', '.join(s.issue_slug for s in failed)}[/]\n"
                f"[bold red]Stopping epic execution.[/]"
            )
            break

    # After all child groups succeed, process the parent epic issue itself.
    success_statuses = {PipelineStatus.MERGED, PipelineStatus.APPROVED}
    all_children_ok = all(s.status in success_statuses for s in all_states)
    if all_children_ok:
        parent_merged = await _already_merged(str(epic_plan.parent_issue))
        if parent_merged is not None:
            console.print(f"[dim]Parent #{epic_plan.parent_issue} already merged — skipping.[/]")
            all_states.append(parent_merged)
        else:
            console.print(
                f"\n[bold cyan]{'─' * 60}[/]\n"
                f"[bold cyan]Parent epic #{epic_plan.parent_issue}[/]\n"
                f"[bold cyan]{'─' * 60}[/]"
            )
            parent_issue = await github_ops.fetch_issue(epic_plan.parent_issue)
            parent_state = await run_pipeline(parent_issue, config)
            if parent_state.status == PipelineStatus.APPROVED:
                await _merge_step(parent_state, config)
            all_states.append(parent_state)

    _print_summary(all_states)
    return all_states


async def run_linear_epic_plan(epic_plan: LinearEpicPlan, config: CorbitConfig) -> list[PipelineState]:
    """Execute a Linear epic plan: sequential groups, parallel within each group."""
    total_issues = sum(len(g) for g in epic_plan.groups)
    console.print(
        f"[bold]Linear Epic {epic_plan.parent_identifier}: "
        f"{total_issues} sub-issue(s) across {len(epic_plan.groups)} group(s)[/]"
    )
    for i, group in enumerate(epic_plan.groups, 1):
        console.print(f"  Group {i}: {', '.join(group)}")
    console.print()

    if config.clean:
        all_slugs = [slug for group in epic_plan.groups for slug in group]
        await _clean_worktrees(all_slugs)

    all_states: list[PipelineState] = []

    for group_idx, group in enumerate(epic_plan.groups, 1):
        console.print(
            f"[bold cyan]{'─' * 60}[/]\n"
            f"[bold cyan]Group {group_idx}/{len(epic_plan.groups)}: "
            f"{', '.join(group)}[/]\n"
            f"[bold cyan]{'─' * 60}[/]"
        )

        skipped: list[PipelineState] = []
        pending_identifiers: list[str] = []
        for identifier in group:
            cached = await _already_merged(identifier)
            if cached is not None:
                console.print(f"[dim]{identifier} already merged — skipping.[/]")
                skipped.append(cached)
            else:
                pending_identifiers.append(identifier)

        if not pending_identifiers:
            console.print(f"[dim]Group {group_idx} fully complete — skipping.[/]")
            all_states.extend(skipped)
            continue

        api_key = config.linear_api_key or None
        pending_issues = [
            await linear_ops.fetch_issue(ident, api_key=api_key)
            for ident in pending_identifiers
        ]

        if len(pending_issues) == 1:
            run_states = [await run_pipeline(pending_issues[0], config)]
        else:
            run_states = await _run_parallel(pending_issues, config)

        group_states = skipped + run_states
        all_states.extend(group_states)

        for state in group_states:
            if state.status == PipelineStatus.APPROVED:
                await _merge_step(state, config)

        success_statuses = {PipelineStatus.MERGED, PipelineStatus.APPROVED}
        failed = [s for s in group_states if s.status not in success_statuses]
        if failed:
            console.print(
                f"[bold red]Group {group_idx} had failures: "
                f"{', '.join(s.issue_slug for s in failed)}[/]\n"
                f"[bold red]Stopping epic execution.[/]"
            )
            break

    # After all child groups succeed, process the parent epic issue itself.
    success_statuses = {PipelineStatus.MERGED, PipelineStatus.APPROVED}
    all_children_ok = all(s.status in success_statuses for s in all_states)
    if all_children_ok:
        parent_merged = await _already_merged(epic_plan.parent_identifier)
        if parent_merged is not None:
            console.print(f"[dim]Parent {epic_plan.parent_identifier} already merged — skipping.[/]")
            all_states.append(parent_merged)
        else:
            console.print(
                f"\n[bold cyan]{'─' * 60}[/]\n"
                f"[bold cyan]Parent epic {epic_plan.parent_identifier}[/]\n"
                f"[bold cyan]{'─' * 60}[/]"
            )
            api_key = config.linear_api_key or None
            parent_issue = await linear_ops.fetch_issue(epic_plan.parent_identifier, api_key=api_key)
            parent_state = await run_pipeline(parent_issue, config)
            if parent_state.status == PipelineStatus.APPROVED:
                await _merge_step(parent_state, config)
            all_states.append(parent_state)

    _print_summary(all_states)
    return all_states


def _print_summary(states: list[PipelineState]) -> None:
    """Print a summary table of all pipeline results."""
    table = Table(title="\nCorbit Summary")
    table.add_column("Issue", style="bold")
    table.add_column("Status")
    table.add_column("Rounds")
    table.add_column("PR")
    table.add_column("Error")

    for state in states:
        status_style = {
            PipelineStatus.APPROVED: "green",
            PipelineStatus.MERGED: "green",
            PipelineStatus.FAILED: "red",
        }.get(state.status, "yellow")

        pr_url = state.pr.url if state.pr else "—"
        error = state.error[:60] if state.error else "—"
        display = f"#{state.issue_slug}" if state.issue_slug.isdigit() else state.issue_slug

        table.add_row(
            display,
            f"[{status_style}]{state.status.value}[/{status_style}]",
            str(state.current_round),
            pr_url,
            error,
        )

    console.print(table)
