# Corbit — Developer Reference

This document covers architecture, internals, and contributing guidelines.

## Architecture

```
┌──────────────────────────────────────────────────┐
│                   CLI (Typer)                    │
│         auto-detects epics on single issue       │
├──────────────────────────────────────────────────┤
│               Orchestrator                       │
│          asyncio.gather + Semaphore              │
├──────────────────────────────────────────────────┤
│  Regular issues          │  Epic issues          │
│  ────────────────────    │  ─────────────────    │
│  parallel or sequential  │  Group 1 (parallel)   │
│                          │    → merge + pull main │
│                          │  Group 2 (parallel)   │
│                          │    → merge + pull main │
│                          │  ...                  │
├────────────┬─────────────┴───────────────────────┤
│ Pipeline 1 │ Pipeline 2  │ Pipeline N ...        │
│ (issue 42) │ (ENG-123)   │                       │
├────────────┴─────────────┴───────────────────────┤
│  1. Fetch issue (gh CLI or Linear GraphQL)       │
│  2. Create worktree (git)                        │
│  3. Coder agent implements (Claude/Codex)        │
│  4. Push branch + create PR (gh CLI)             │
│  5. Review loop:                                 │
│     a. Reviewer evaluates PR (Claude/Codex)      │
│     b. If approved → done                        │
│     c. If changes requested → coder fixes → push │
│  6. Cleanup worktree                             │
└──────────────────────────────────────────────────┘
```

## Source Layout

```
src/corbit/
  cli.py          — Typer commands, issue ID parsing, pre-flight fetches
  config.py       — 3-layer config: .corbit.toml < env vars < CLI flags
  models.py       — Pydantic models and enums (Issue, PipelineState, ...)
  pipeline.py     — Single-issue lifecycle loop
  orchestrator.py — Multi-issue dispatch (parallel, sequential, epic)
  github.py       — gh CLI wrapper (fetch, PR, review, merge)
  linear.py       — Linear GraphQL client (fetch issue, post comment)
  worktree.py     — git worktree create/cleanup
  prompts.py      — All agent prompt templates
  reviewer.py     — Reviewer agent invocation and JSON parsing
  epic.py         — Epic detection and dependency parsing
  agents/         — Coder agent backends (Claude Code, Codex)
  caffeinate.py   — macOS sleep prevention
```

## Issue Model

All issue types extend the `Issue` base Pydantic model, which the pipeline works with exclusively:

```python
class Issue(BaseModel):
    title: str
    url: str
    body: str
    labels: list[str]
    comments: list[IssueComment]

    @property
    def slug(self) -> str: ...        # branch/worktree safe name
    @property
    def display_id(self) -> str: ...  # human-readable (e.g. "#42", "ENG-123")
    @property
    def source(self) -> IssueSource: ...
    def to_prompt(self) -> str: ...
```

`GitHubIssue` and `LinearIssue` both extend `Issue`. The pipeline only calls `issue.slug`, `issue.display_id`, `issue.to_prompt()`, and `issue.source` — no `isinstance` checks except where source-specific behavior is needed (PR body, Linear comments).

## Issue ID Parsing

The CLI parses issue IDs before fetching:

- `str.isdigit()` → GitHub issue number → `gh` CLI fetch
- `[A-Z]+-\d+` (e.g. `ENG-123`) → Linear identifier → GraphQL fetch
- Mixed sources in one invocation → error

## Epic Issues

Epic detection and parsing is GitHub-only (`epic.py`). Three parsing strategies, tried in order:

1. **"Suggested Implementation Order" section** — numbered list; multiple issues per line (e.g. `#3 + #4`) run in parallel.
2. **Dependency table** — markdown table with "Depends on" column; topologically sorted into groups.
3. **Fallback** — all `#N` refs in the body, each as its own sequential group.

Groups run sequentially; issues within a group run in parallel bounded by `parallel_workers`.

## Agent Backends

Both the coder and reviewer use the same `CoderAgent` interface:

```python
class CoderAgent:
    async def implement(prompt, path, session_id, timeout, label) -> AgentResult
    async def apply_feedback(feedback, path, session_id, timeout, label) -> AgentResult
```

| Backend | CLI binary | Session resumption |
|---|---|---|
| Claude Code | `claude` | `--resume <session_id>` |
| Codex | `codex` | `codex exec resume <thread_id>` |

## Pipeline State Persistence

Each worktree stores `.corbit-state.json` so interrupted pipelines can resume:

| `step` value | Meaning |
|---|---|
| `implemented` | Code committed, PR open, ready to review |
| `reviewed` | Review done, feedback pending application |
| `feedback_applied` | Feedback applied, ready for next review round |

## Linear Comments

When `linear_post_comment = true`, the pipeline posts fire-and-forget comments to the Linear issue at:

- PR created
- Each review round (verdict + top findings)
- Applying review feedback
- Final approval
- Pipeline failure

Comment failures are logged as warnings and never block the pipeline.

## Debug Mode

`--debug` pauses before each pipeline step and shows detailed info. Press Enter to continue, `q` to abort.

## Development Setup

```bash
git clone https://github.com/loopsmark/corbit.git
cd corbit
uv sync

uv run corbit --help
uv run pytest
```

## Contributing

1. Fork the repo
2. Create a feature branch
3. Run tests: `uv run pytest`
4. Open a PR

## License

MIT
