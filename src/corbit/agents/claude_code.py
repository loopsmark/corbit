"""Claude Code CLI adapter."""

from __future__ import annotations

import json
from pathlib import Path

from corbit.agents.base import CoderAgent
from corbit.models import AgentResult
from corbit.prompts import build_feedback_prompt
from corbit.stream import run_streaming


class ClaudeCodeAgent(CoderAgent):
    """Coder agent backed by the Claude Code CLI."""

    def __init__(self, model: str = "", skip_permissions: bool = True) -> None:
        self._model = model
        self._skip_permissions = skip_permissions

    def _base_args(self) -> list[str]:
        args = ["claude", "-p", "--verbose", "--output-format", "stream-json"]
        if self._skip_permissions:
            args.append("--dangerously-skip-permissions")
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
        args = self._base_args()
        if session_id:
            args.extend(["--resume", session_id])
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
        args = self._base_args()
        if session_id:
            args.extend(["--resume", session_id])
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

        # stdout may contain multiple JSON lines (streaming events).
        # Find the final "result" event for session_id and output.
        sid: str | None = None
        output_text = ""
        found_result = False

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "result":
                    sid = data.get("session_id")
                    output_text = data.get("result", line)
                    found_result = True
            except (json.JSONDecodeError, TypeError):
                continue

        if not found_result:
            # Fallback: try parsing entire stdout as single JSON
            try:
                data = json.loads(result.stdout)
                sid = data.get("session_id")
                output_text = data.get("result", result.stdout)
            except (json.JSONDecodeError, TypeError):
                output_text = result.stdout

        if result.returncode != 0:
            error = result.stderr.strip()
            if not error:
                error = f"claude exited with code {result.returncode}"
                if output_text:
                    error += f" — last output: {output_text[:200]}"
            return AgentResult(
                success=False,
                output=output_text,
                error=error,
                session_id=sid,
            )

        return AgentResult(
            success=True,
            output=output_text,
            session_id=sid,
        )
