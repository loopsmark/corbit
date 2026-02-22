# Corbit

Orchestrate AI coding agents to autonomously implement GitHub and Linear issues.

Corbit fetches an issue, spins up an isolated git worktree, runs a coder agent to implement it and open a PR, then runs a reviewer agent to evaluate the PR and iterate on feedback — all automatically.

## Prerequisites

- Python 3.11+
- [`gh` CLI](https://cli.github.com/) (authenticated)
- At least one agent backend:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI)
  - [OpenAI Codex](https://github.com/openai/codex) (`codex` CLI)

## Install

```bash
# One-line install (standalone binary)
curl -fsSL https://raw.githubusercontent.com/loopsmark/corbit/main/install.sh | sh

# Or install as a global uv tool
uv tool install /path/to/corbit
```

## Quick Start

```bash
# Interactive setup
corbit config

# Run on a GitHub issue
corbit run --issue 42

# Run on a Linear issue
corbit run --issue ENG-123

# Multiple issues sequentially (default)
corbit run --issue 42,43,44

# Multiple issues in parallel
corbit run --issue 42,43,44 --parallel

# Single-pass (skip review loop)
corbit run --issue 42 --iteration-mode single-pass

# Step-by-step debug mode
corbit run --issue 42 --debug
```

## Commands

| Command | Description |
|---|---|
| `corbit run --issue <ID>` | Run pipeline for one or more issues |
| `corbit config` | Interactive project configuration |
| `corbit cleanup --issue <ID>` | Remove worktree for an issue |
| `corbit cleanup --all` | Remove all corbit worktrees |
| `corbit version` | Show version |

## Configuration

Run `corbit config` to generate a `.corbit.toml` in your repo root. CLI flags override the config file per-invocation.

| Setting | Default | Description |
|---|---|---|
| `coder_backend` | `claude-code` | Agent that implements issues (`claude-code` or `codex`) |
| `reviewer_backend` | `claude-code` | Agent that reviews PRs (`claude-code` or `codex`) |
| `max_review_rounds` | `4` | Review/fix cycles before giving up |
| `iteration_mode` | `full` | `full` (review loop) or `single-pass` (skip review) |
| `sequential` | `true` | Process issues one at a time (set to `false` for parallel mode) |
| `parallel_workers` | `2` | Worker count when running in parallel mode |
| `main_branch` | `main` | Base branch for worktrees and PRs |
| `agent_timeout` | `600` | Seconds per agent invocation |
| `linear_api_key` | — | Linear API key (prefer `LINEAR_API_KEY` env var to avoid committing secrets) |
| `linear_post_comment` | `true` | Post progress comments on Linear issues |
| `skip_permissions` | `true` | Pass `--dangerously-skip-permissions` to Claude Code (see security note below) |

> **Security:** When `skip_permissions = true` (the default), the Claude Code coder agent runs with `--dangerously-skip-permissions`, granting it unrestricted filesystem and shell access with no confirmation prompts. This is required for fully automated operation. Set `skip_permissions = false` if you prefer Claude Code to prompt before taking actions, at the cost of requiring manual approval during runs.

## Linear Integration

Set `LINEAR_API_KEY` in your environment (or `linear_api_key` in `.corbit.toml`), then pass a Linear ID:

```bash
export LINEAR_API_KEY=lin_api_...
corbit run --issue ENG-123
```

Corbit will post progress comments on the Linear issue as the pipeline runs.

## Sequential vs Parallel Mode

By default, corbit processes issues **sequentially** — one at a time, fully implemented and reviewed before the next starts. PRs are left open for you to merge manually. Issues branch independently from `main`, so this works well for unrelated work.

```bash
corbit run --issue 42,43,44          # sequential (default)
corbit run --issue 42,43,44 --parallel   # run all issues concurrently
corbit run --issue 42,43,44 --workers 4  # parallel with 4 workers
```

If your issues depend on each other (each one building on the previous), use `--wait-for-merge`:

```bash
corbit run --issue 42,43,44 --wait-for-merge
```

With `--wait-for-merge`, corbit pauses after each PR is approved, prints the PR URL, and waits for you to merge it on GitHub. Once merged, it pulls the updated `main` and branches the next issue from it.

## Epic Issues (GitHub)

Pass a single GitHub issue that has an `epic:` label or references child issues — Corbit auto-detects it and runs the child issues in dependency order, in parallel within each group.

```bash
corbit run --issue 254
corbit run --issue 254 --wait-for-merge   # pause between groups
```

---

For architecture details, agent configuration, and contributing guidelines, see [AGENTS.md](AGENTS.md).
