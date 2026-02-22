"""OpenAI Codex CLI adapter."""

from __future__ import annotations

import json
from pathlib import Path

from corbit.agents.base import CoderAgent
from corbit.models import AgentResult
from corbit.prompts import build_feedback_prompt
from corbit.stream import run_streaming


class CodexAgent(CoderAgent):
    """Coder agent backed by the OpenAI Codex CLI."""

    def __init__(self, model: str = "") -> None:
        self._model = model

    def _base_args(self, worktree_path: Path) -> list[str]:
        args = ["codex", "exec", "--full-auto", "--json"]
        if self._model:
            args.extend(["--model", self._model])
        # Worktrees store git metadata in the main repo's .git/worktrees/ dir.
        # Grant codex write access so it can commit.
        git_file = worktree_path / ".git"
        if git_file.is_file():
            content = git_file.read_text().strip()
            if content.startswith("gitdir:"):
                git_dir = Path(content.split(":", 1)[1].strip())
                if not git_dir.is_absolute():
                    git_dir = (worktree_path / git_dir).resolve()
                main_git_dir = git_dir.parent.parent
                args.extend(["--add-dir", str(main_git_dir)])
        return args

    def _resume_args(self) -> list[str]:
        args = ["codex", "exec", "resume", "--full-auto", "--json"]
        if self._model:
            args.extend(["--model", self._model])
        return args

    async def implement(
        self,
        prompt: str,
        worktree_path: Path,
        session_id: str | None = None,
        timeout: int = 600,
        label: str = "",
    ) -> AgentResult:
        if session_id:
            # Resume previous session to keep full context
            args = self._resume_args()
            args.extend([session_id, prompt])
        else:
            args = self._base_args(worktree_path)
            args.append(prompt)
        return await self._run(args, worktree_path, timeout, label=label)

    async def apply_feedback(
        self,
        feedback: str,
        worktree_path: Path,
        session_id: str | None = None,
        timeout: int = 600,
        label: str = "",
    ) -> AgentResult:
        # Always use a fresh session with --add-dir so the sandbox can commit.
        # `codex exec resume` doesn't support --add-dir, and the feedback
        # prompt is self-contained (file paths + what to fix).
        args = self._base_args(worktree_path)
        args.append(build_feedback_prompt(feedback))
        return await self._run(args, worktree_path, timeout, label=label)

    async def _run(
        self,
        args: list[str],
        worktree_path: Path,
        timeout: int,
        label: str = "",
    ) -> AgentResult:
        result = await run_streaming(args, worktree_path, timeout, label=label)

        if result.returncode == -1:
            return AgentResult(success=False, error="Agent timed out")

        # Parse JSONL output — extract thread_id, last agent message, and errors
        thread_id: str | None = None
        last_message = ""
        error_message = ""

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type", "")
                if event_type == "thread.started":
                    thread_id = event.get("thread_id")
                elif event_type == "error":
                    error_message = event.get("message", "")
                elif event_type == "turn.failed":
                    err = event.get("error", {})
                    if isinstance(err, dict) and not error_message:
                        error_message = err.get("message", "")
                elif event_type == "item.completed":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "agent_message":
                        last_message = item.get("text", "")
            except (json.JSONDecodeError, TypeError):
                continue

        output_text = last_message or result.stdout

        if result.returncode != 0:
            error = error_message or result.stderr.strip()
            if not error:
                if last_message:
                    error = f"codex exited with code {result.returncode} — last message: {last_message[:200]}"
                else:
                    error = (
                        f"codex exited with code {result.returncode} with no output. "
                        "Check that `codex` is installed and OPENAI_API_KEY is set."
                    )
            return AgentResult(
                success=False,
                output=output_text,
                error=error,
                session_id=thread_id,
            )

        return AgentResult(
            success=True,
            output=output_text,
            session_id=thread_id,
        )
