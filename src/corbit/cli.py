"""Typer CLI — run, cleanup, config, status commands."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt

from corbit import __version__
from corbit import github as github_ops
from corbit import linear as linear_ops
from corbit.caffeinate import prevent_sleep
from corbit.config import load_config
from corbit.epic import extract_epic_plan, is_epic
from corbit.models import AgentBackend, CorbitConfig, GitHubIssue, Issue, IssueSource, IterationMode, LinearIssue, MergeMethod, MergeStrategy
from corbit.orchestrator import run_epic_plan, run_issues, run_linear_epic_plan
from corbit.worktree import cleanup_all_worktrees, cleanup_issue_worktree

app = typer.Typer(
    name="corbit",
    help="Orchestrate AI coding agents to implement GitHub issues.",
    no_args_is_help=True,
)
console = Console()

_BACKEND_CHOICES = [b.value for b in AgentBackend]
_MODE_CHOICES = [m.value for m in IterationMode]
_MERGE_CHOICES = [m.value for m in MergeMethod]
_MERGE_STRATEGY_CHOICES = [s.value for s in MergeStrategy]

# Known models per backend: (model_id, description)
_CLAUDE_MODELS: list[tuple[str, str]] = [
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced (recommended)"),
    ("claude-opus-4-6", "Opus 4.6 — most capable"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — fastest"),
]
_CODEX_MODELS: list[tuple[str, str]] = [
    ("o4-mini", "o4-mini — fast and efficient"),
    ("o3", "o3 — most capable"),
    ("gpt-4o", "gpt-4o"),
]

_MODELS_BY_BACKEND: dict[str, list[tuple[str, str]]] = {
    AgentBackend.CLAUDE_CODE.value: _CLAUDE_MODELS,
    AgentBackend.CODEX.value: _CODEX_MODELS,
}


def _pick_model(backend: str, current: str, label: str) -> str:
    """Show a numbered model picker for the given backend. Returns the chosen model ID."""
    known = _MODELS_BY_BACKEND.get(backend, [])
    options: list[tuple[str, str]] = (
        [("", "Default (let backend decide)")]
        + known
        + [("__other__", "Type a model name manually")]
    )

    console.print(f"\n[bold]{label}[/]")
    for i, (model_id, desc) in enumerate(options):
        current_marker = " [green](current)[/]" if model_id == current else ""
        console.print(f"  [cyan]{i}[/]  {desc}{current_marker}")

    default_idx = next(
        (str(i) for i, (m, _) in enumerate(options) if m == current), "0"
    )
    choice = Prompt.ask("Select", choices=[str(i) for i in range(len(options))], default=default_idx)
    model_id, _ = options[int(choice)]

    if model_id == "__other__":
        return Prompt.ask("Model name")
    return model_id

_LINEAR_ID_RE = re.compile(r'^[A-Z]+-\d+$')


def _parse_issue_refs(issue_str: str) -> list[tuple[str, IssueSource]]:
    """Parse a comma-separated string of issue refs. Returns (raw, source) tuples."""
    results: list[tuple[str, IssueSource]] = []
    for token in issue_str.split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            results.append((token, IssueSource.GITHUB))
        elif _LINEAR_ID_RE.match(token):
            results.append((token, IssueSource.LINEAR))
        else:
            console.print(
                f"[red]Invalid issue ID: '{token}'. "
                f"Expected a GitHub issue number (e.g. 123) or a Linear ID (e.g. ENG-123).[/]"
            )
            raise typer.Exit(1)
    return results


def _config_to_toml(cfg: CorbitConfig) -> str:
    """Serialize a CorbitConfig to TOML string."""
    lines = [
        "[corbit]",
        f'coder_backend = "{cfg.coder_backend.value}"',
        f'reviewer_backend = "{cfg.reviewer_backend.value}"',
        f"max_review_rounds = {cfg.max_review_rounds}",
        f'iteration_mode = "{cfg.iteration_mode.value}"',
        f"parallel_workers = {cfg.parallel_workers}",
        f'main_branch = "{cfg.main_branch}"',
        f"agent_timeout = {cfg.agent_timeout}",
        f"sequential = {str(cfg.sequential).lower()}",  # false = parallel mode
        f'merge_method = "{cfg.merge_method.value}"',
        f"linear_post_comment = {str(cfg.linear_post_comment).lower()}",
        f"skip_permissions = {str(cfg.skip_permissions).lower()}",
        f'merge_strategy = "{cfg.merge_strategy.value}"',
    ]
    if cfg.coder_model:
        lines.append(f'coder_model = "{cfg.coder_model}"')
    if cfg.reviewer_model:
        lines.append(f'reviewer_model = "{cfg.reviewer_model}"')
    if cfg.linear_api_key:
        lines.append("# WARNING: This key is sensitive. Prefer setting LINEAR_API_KEY as an env var instead.")
        lines.append(f'linear_api_key = "{cfg.linear_api_key}"')
    return "\n".join(lines) + "\n"


@app.command()
def run(
    issue: Annotated[str, typer.Option("--issue", "-i", help="Issue ID(s), comma-separated. GitHub: 123, Linear: ENG-123")],
    backend: Annotated[Optional[str], typer.Option("--backend", "-b", help="Coder backend")] = None,
    reviewer_backend: Annotated[Optional[str], typer.Option("--reviewer-backend", "-r", help="Reviewer backend")] = None,
    max_rounds: Annotated[Optional[int], typer.Option("--max-rounds", help="Max review rounds")] = None,
    iteration_mode: Annotated[Optional[str], typer.Option("--iteration-mode", help="full or single-pass")] = None,
    workers: Annotated[Optional[int], typer.Option("--workers", "-w", help="Number of parallel workers (implies --parallel)")] = None,
    parallel: Annotated[bool, typer.Option("--parallel", "-p", help="Run issues in parallel instead of sequentially")] = False,
    main_branch: Annotated[Optional[str], typer.Option("--main-branch", help="Base branch")] = None,
    debug: Annotated[bool, typer.Option("--debug", help="Step-by-step confirmation mode")] = False,
    merge_method: Annotated[Optional[str], typer.Option("--merge-method", help="Merge method (squash, merge, rebase)")] = None,
    clean: Annotated[bool, typer.Option("--clean", help="Remove existing worktrees before starting (fresh start)")] = False,
    merge_strategy: Annotated[Optional[str], typer.Option("--merge-strategy", help="auto: corbit merges; wait: poll until you merge; skip: leave PR open")] = None,
) -> None:
    """Run the Corbit pipeline for one or more issues (GitHub or Linear)."""
    issue_refs = _parse_issue_refs(issue)
    if not issue_refs:
        console.print("[red]No valid issue IDs provided.[/]")
        raise typer.Exit(1)

    # Reject mixed sources in a single invocation
    sources = {src for _, src in issue_refs}
    if len(sources) > 1:
        console.print(
            "[red]Cannot mix GitHub and Linear issues in a single invocation. "
            "Run them separately.[/]"
        )
        raise typer.Exit(1)

    config = load_config(
        backend=backend,
        reviewer_backend=reviewer_backend,
        max_rounds=max_rounds,
        iteration_mode=iteration_mode,
        workers=workers,
        parallel=parallel or (workers is not None),
        main_branch=main_branch,
        debug=debug,
        merge_method=merge_method,
        clean=clean,
        merge_strategy=merge_strategy,
    )

    async def _fetch_issues() -> list[Issue]:
        fetched: list[Issue] = []
        for raw, source in issue_refs:
            if source == IssueSource.GITHUB:
                fetched.append(await github_ops.fetch_issue(int(raw)))
            else:
                fetched.append(
                    await linear_ops.fetch_issue(raw, api_key=config.linear_api_key or None)
                )
        return fetched

    async def _run() -> list:
        issues = await _fetch_issues()
        if len(issues) == 1:
            issue = issues[0]
            if isinstance(issue, GitHubIssue) and is_epic(issue):
                plan = extract_epic_plan(issue)
                if plan.groups:
                    return await run_epic_plan(plan, config)
            if isinstance(issue, LinearIssue):
                api_key = config.linear_api_key or None
                plan = await linear_ops.fetch_epic_plan(issue.identifier, api_key=api_key)
                if plan.groups:
                    return await run_linear_epic_plan(plan, config)
        return await run_issues(issues, config)

    try:
        with prevent_sleep():
            states = asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[bold red]Aborted by user.[/]")
        raise typer.Exit(130)

    failed = [s for s in states if s.status.value == "failed"]
    if failed:
        raise typer.Exit(1)


@app.command()
def config() -> None:
    """Interactively configure Corbit for this project."""
    config_path = Path.cwd() / ".corbit.toml"

    console.print(Panel(
        "Configure Corbit for this project.\n"
        f"Settings will be saved to [bold]{config_path}[/]",
        title="[bold]Corbit Config[/]",
        border_style="blue",
    ))

    # Load existing config as defaults
    existing = load_config()

    console.print()

    # Coder backend
    coder_backend = Prompt.ask(
        "[bold]Coder backend[/] (implements issues)",
        choices=_BACKEND_CHOICES,
        default=existing.coder_backend.value,
    )

    # Reviewer backend
    rev_backend = Prompt.ask(
        "[bold]Reviewer backend[/] (reviews PRs)",
        choices=_BACKEND_CHOICES,
        default=existing.reviewer_backend.value,
    )

    # Iteration mode
    mode = Prompt.ask(
        "[bold]Iteration mode[/]\n"
        "  full = coder implements, reviewer reviews, iterate on feedback\n"
        "  single-pass = coder implements, skip review\n"
        "  Choose",
        choices=_MODE_CHOICES,
        default=existing.iteration_mode.value,
    )

    # Max review rounds
    max_rounds = IntPrompt.ask(
        "[bold]Max review rounds[/] (how many review/fix cycles before giving up)",
        default=existing.max_review_rounds,
    )

    # Parallel workers
    parallel = IntPrompt.ask(
        "[bold]Parallel workers[/] (concurrent issue pipelines)",
        default=existing.parallel_workers,
    )

    # Main branch
    main_branch = Prompt.ask(
        "[bold]Main branch[/] (base for worktrees and PRs)",
        default=existing.main_branch,
    )

    # Agent timeout
    timeout = IntPrompt.ask(
        "[bold]Agent timeout[/] (seconds per agent invocation)",
        default=existing.agent_timeout,
    )

    # Model selection
    console.print()
    console.print("[dim]Model overrides — pick from known models or enter a custom name[/]")
    coder_model = _pick_model(coder_backend, existing.coder_model or "", "Coder model")
    reviewer_model = _pick_model(rev_backend, existing.reviewer_model or "", "Reviewer model")

    # Linear integration
    console.print()
    console.print("[dim]Optional: Linear integration (leave API key blank to skip)[/]")
    linear_api_key = Prompt.ask(
        "[bold]Linear API key[/] (or set LINEAR_API_KEY env var)",
        default=existing.linear_api_key or "",
        password=True,
    )
    linear_post_comment_str = Prompt.ask(
        "[bold]Post progress comments on Linear issues?[/]",
        choices=["y", "n"],
        default="y" if existing.linear_post_comment else "n",
    )

    # Merge strategy
    merge_strategy_str = Prompt.ask(
        "[bold]Merge strategy[/]\n"
        "  auto = corbit merges the PR automatically\n"
        "  wait = corbit waits for you to merge on GitHub\n"
        "  skip = leave PR open, do not wait\n"
        "  Choose",
        choices=_MERGE_STRATEGY_CHOICES,
        default=existing.merge_strategy.value,
    )

    # Build config
    cfg = CorbitConfig(
        coder_backend=AgentBackend(coder_backend),
        reviewer_backend=AgentBackend(rev_backend),
        max_review_rounds=max_rounds,
        iteration_mode=IterationMode(mode),
        parallel_workers=parallel,
        main_branch=main_branch,
        agent_timeout=timeout,
        coder_model=coder_model,
        reviewer_model=reviewer_model,
        linear_api_key=linear_api_key,
        linear_post_comment=(linear_post_comment_str == "y"),
        merge_strategy=MergeStrategy(merge_strategy_str),
    )

    # Preview
    toml_content = _config_to_toml(cfg)
    console.print()
    console.print(Panel(toml_content, title=".corbit.toml", border_style="green"))

    # Confirm
    save = Prompt.ask("Save this configuration?", choices=["y", "n"], default="y")
    if save == "y":
        config_path.write_text(toml_content)
        console.print(f"\n[green]Config saved to {config_path}[/]")
    else:
        console.print("\n[yellow]Configuration not saved.[/]")


@app.command()
def cleanup(
    issue: Annotated[Optional[str], typer.Option("--issue", "-i", help="Issue ID to clean up (e.g. 123 or ENG-123)")] = None,
    all_worktrees: Annotated[bool, typer.Option("--all", help="Clean all corbit worktrees")] = False,
) -> None:
    """Clean up worktrees created by Corbit."""
    if not issue and not all_worktrees:
        console.print("[red]Specify --issue <ID> or --all[/]")
        raise typer.Exit(1)

    if all_worktrees:
        removed = asyncio.run(cleanup_all_worktrees())
        if removed:
            console.print(f"[green]Removed {len(removed)} worktree(s)[/]")
        else:
            console.print("No corbit worktrees found.")
    elif issue:
        refs = _parse_issue_refs(issue)
        for raw, _ in refs:
            found = asyncio.run(cleanup_issue_worktree(raw))
            if found:
                console.print(f"[green]Cleaned up worktree for {raw}[/]")
            else:
                console.print(f"No worktree found for {raw}")


@app.command()
def version() -> None:
    """Show Corbit version."""
    console.print(f"corbit {__version__}")


if __name__ == "__main__":
    app()
