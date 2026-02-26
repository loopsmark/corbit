"""Code reviewer — supports multiple agent backends with structured JSON output."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from rich.console import Console

from corbit.github import post_pr_review
from corbit.models import AgentBackend, PullRequestInfo, ReviewItem, ReviewResult, ReviewSeverity, ReviewVerdict
from corbit.prompts import build_review_prompt
from corbit.stream import run_streaming

_console = Console()


def _build_no_gh_env() -> dict[str, str]:
    """Build an environment where ``gh`` is shadowed by a no-op stub.

    This prevents the reviewer agent from posting comments to GitHub
    directly — all GitHub interaction is handled by corbit itself.
    A stub script is placed in a temporary directory that is prepended to
    PATH, shadowing only ``gh`` while leaving all other binaries accessible.
    """
    env = os.environ.copy()
    if not shutil.which("gh"):
        return env

    stub_dir = Path(tempfile.gettempdir()) / "corbit-no-gh"
    stub_dir.mkdir(exist_ok=True)
    stub_gh = stub_dir / "gh"
    stub_gh.write_text("#!/bin/sh\necho 'gh: disabled by corbit' >&2\nexit 127\n")
    stub_gh.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
    return env


_SEVERITY_ORDER: list[ReviewSeverity] = [
    ReviewSeverity.BUG,
    ReviewSeverity.CORRECTNESS,
    ReviewSeverity.DESIGN,
    ReviewSeverity.TESTING,
    ReviewSeverity.NIT,
]

_SEVERITY_HEADERS: dict[ReviewSeverity, str] = {
    ReviewSeverity.BUG: "Bugs",
    ReviewSeverity.CORRECTNESS: "Correctness",
    ReviewSeverity.DESIGN: "Design",
    ReviewSeverity.TESTING: "Testing",
    ReviewSeverity.NIT: "Nits (informational)",
}


def _format_review_body(items: list[ReviewItem]) -> str:
    """Format review items grouped by severity for posting to GitHub."""
    grouped: dict[ReviewSeverity, list[ReviewItem]] = {}
    for item in items:
        grouped.setdefault(item.severity, []).append(item)

    sections: list[str] = []
    for severity in _SEVERITY_ORDER:
        group = grouped.get(severity)
        if not group:
            continue
        header = _SEVERITY_HEADERS[severity]
        lines = [f"### {header}"]
        for item in group:
            lines.append(f"- **`{item.file}`**: {item.comment}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


class Reviewer:
    """Reviews pull requests using a configurable agent backend."""

    def __init__(
        self,
        backend: AgentBackend = AgentBackend.CLAUDE_CODE,
        model: str = "",
        skip_permissions: bool = True,
    ) -> None:
        self._backend = backend
        self._model = model
        self._skip_permissions = skip_permissions
        self._session_id: str | None = None

    def _build_args(self, prompt: str) -> list[str]:
        if self._backend == AgentBackend.CLAUDE_CODE:
            args = ["claude", "-p", "--verbose", "--output-format", "stream-json"]
            if self._skip_permissions:
                args.append("--dangerously-skip-permissions")
            if self._model:
                args.extend(["--model", self._model])
            if self._session_id:
                args.extend(["--resume", self._session_id])
            args.append(prompt)
            return args

        if self._backend == AgentBackend.CODEX:
            args = ["codex", "exec", "--full-auto", "--json"]
            if self._model:
                args.extend(["--model", self._model])
            args.append(prompt)
            return args

        raise ValueError(f"Unsupported reviewer backend: {self._backend}")

    async def review(
        self,
        pr: PullRequestInfo,
        worktree_path: Path,
        timeout: int = 600,
        label: str = "",
        round_number: int = 1,
        previous_feedback: str = "",
    ) -> ReviewResult:
        """Run a code review on the given PR."""
        prompt = build_review_prompt(
            pr_number=pr.number,
            head_branch=pr.head_branch,
            base_branch=pr.base_branch,
            round_number=round_number,
            previous_feedback=previous_feedback,
        )

        args = self._build_args(prompt)
        reviewer_label = label or f"#{pr.number} [reviewer/{self._backend.value}]"

        result = await run_streaming(
            args, worktree_path, timeout, label=reviewer_label, env=_build_no_gh_env(),
        )

        if result.returncode == -1:
            return ReviewResult(
                verdict=ReviewVerdict.ERROR,
                comments="Reviewer timed out",
            )

        if result.returncode != 0:
            return ReviewResult(
                verdict=ReviewVerdict.ERROR,
                comments=f"Reviewer failed: {result.stderr.strip()}",
            )

        review = self._parse_review(result.stdout)

        # Track session for follow-up rounds (Claude Code only)
        if self._backend == AgentBackend.CLAUDE_CODE:
            sid = self._extract_session_id(result.stdout)
            if sid:
                self._session_id = sid

        # Post the review to GitHub from corbit (agent can't do it).
        # Failures here are non-fatal — the review result is still valid
        # and the pipeline should continue with the feedback.
        if review.verdict != ReviewVerdict.ERROR:
            if review.items:
                body = _format_review_body(review.items)
            else:
                body = review.comments or ("LGTM" if review.verdict == ReviewVerdict.APPROVED else "")
            try:
                await post_pr_review(pr.number, review.verdict.value, body)
            except RuntimeError as exc:
                _console.print(f"[yellow]Warning: failed to post review to GitHub: {exc}[/]")

        return review

    @staticmethod
    def _extract_session_id(raw: str) -> str | None:
        """Extract the session_id from Claude Code's JSONL stream."""
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "result" and event.get("session_id"):
                    return str(event["session_id"])
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    @staticmethod
    def _normalize_json_newlines(text: str) -> str:
        """Escape literal newlines/carriage-returns inside JSON string values.

        Claude sometimes emits JSON with real newline characters inside string
        values, which is invalid JSON. This scanner walks the text character by
        character, tracking whether we are inside a string, and replaces bare
        newlines with the \\n escape sequence.
        """
        result: list[str] = []
        in_string = False
        escaped = False
        for ch in text:
            if escaped:
                result.append(ch)
                escaped = False
            elif ch == "\\" and in_string:
                result.append(ch)
                escaped = True
            elif ch == '"':
                in_string = not in_string
                result.append(ch)
            elif ch == "\n" and in_string:
                result.append("\\n")
            elif ch == "\r" and in_string:
                result.append("\\r")
            else:
                result.append(ch)
        return "".join(result)

    @staticmethod
    def _try_json_loads(text: str) -> dict | None:
        """Attempt json.loads, falling back to newline-normalised parse."""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def _extract_json(self, text: str) -> dict | None:
        """Extract a JSON object from text that may contain surrounding prose."""
        # Direct parse (fast path)
        result = self._try_json_loads(text)
        if result is not None:
            return result

        # Normalize literal newlines inside strings and retry
        normalized = self._normalize_json_newlines(text)
        result = self._try_json_loads(normalized)
        if result is not None:
            return result

        # Try markdown code blocks (both raw and normalized)
        for candidate in (text, normalized):
            for marker in ("```json", "```"):
                if marker in candidate:
                    try:
                        start = candidate.index(marker) + len(marker)
                        end = candidate.index("```", start)
                        result = self._try_json_loads(candidate[start:end].strip())
                        if result is not None:
                            return result
                    except ValueError:
                        continue

        # Find first '{' and try progressively longer substrings (raw then normalized)
        for candidate in (text, normalized):
            idx = candidate.find("{")
            while idx != -1:
                result = self._try_json_loads(candidate[idx:])
                if result is not None:
                    return result
                idx = candidate.find("{", idx + 1)

        return None

    def _parse_review(self, raw: str) -> ReviewResult:
        """Parse the reviewer's JSON output into a ReviewResult."""
        # With stream-json, stdout contains JSONL events.
        # Collect text candidates: result event (highest priority) then
        # assistant text blocks (in order).  Trying multiple sources means
        # we still succeed when the result event is missing or empty — the
        # reviewer's JSON often lives only in the last assistant event.
        candidates: list[str] = []

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = event.get("type", "")
            if event_type == "result":
                result_field = event.get("result")
                if isinstance(result_field, str) and result_field:
                    candidates.insert(0, result_field)  # highest priority
            elif event_type == "assistant":
                message = event.get("message", {})
                if isinstance(message, dict):
                    for block in message.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            block_text = block.get("text", "")
                            if isinstance(block_text, str) and block_text:
                                candidates.append(block_text)

        if not candidates:
            # No recognized JSONL events — Codex or plain JSON output
            try:
                outer = json.loads(raw)
                fallback = outer.get("result") or outer.get("output") or raw
                candidates = [fallback if isinstance(fallback, str) else raw]
            except (json.JSONDecodeError, TypeError):
                candidates = [raw]

        # Try each candidate in priority order
        data: dict | None = None
        for candidate in candidates:
            data = self._extract_json(candidate)
            if data is not None:
                break

        if data is None:
            debug_text = candidates[0] if candidates else raw
            return ReviewResult(
                verdict=ReviewVerdict.ERROR,
                comments=f"Could not parse reviewer output: {debug_text[:500]}",
            )

        verdict_str = data.get("verdict", "error")
        try:
            verdict = ReviewVerdict(verdict_str)
        except ValueError:
            verdict = ReviewVerdict.ERROR

        items: list[ReviewItem] = []
        for raw_item in data.get("items", []):
            if isinstance(raw_item, dict):
                try:
                    severity = ReviewSeverity(raw_item.get("severity", "correctness"))
                except ValueError:
                    severity = ReviewSeverity.CORRECTNESS
                items.append(ReviewItem(
                    file=raw_item.get("file", ""),
                    comment=raw_item.get("comment", ""),
                    severity=severity,
                ))

        # Build feedback for the coder from all items
        if items:
            comments = "\n".join(
                f"- [{item.severity.value}] {item.file}: {item.comment}"
                for item in items
            )
        else:
            comments = data.get("comments", "")

        # Guard against inconsistent LLM output: if the verdict is
        # "approved" but there are items, override to "changes-requested"
        # so we never approve and simultaneously request work.
        if verdict == ReviewVerdict.APPROVED and items:
            verdict = ReviewVerdict.CHANGES_REQUESTED

        return ReviewResult(
            verdict=verdict,
            comments=comments,
            items=items,
        )
