"""Shared subprocess streaming — pipes agent output to the terminal in real-time."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class StreamResult:
    """Result of a streamed subprocess execution."""

    returncode: int
    stdout: str
    stderr: str


def _timestamp() -> str:
    """Return current timestamp in Y/M/D HH:MM format."""
    return datetime.now().strftime("%Y/%m/%d %H:%M")


def _format_prefix(label: str) -> str:
    """Build a styled prefix like '  [2026/02/14 10:30] #63 [codex] │ '.

    Includes a fresh timestamp on every call.
    """
    ts = _timestamp()
    if label:
        return f"  [{ts}] {label} │ "
    return f"  [{ts}] "


def _tool_detail(name: str, tool_input: object) -> str:
    """Extract a short summary from tool input for display."""
    if not isinstance(tool_input, dict):
        return ""
    if name == "Bash":
        cmd = tool_input.get("command", "")
        if cmd:
            # Show first line, truncated
            first_line = str(cmd).split("\n")[0][:80]
            return f": {first_line}"
    elif name == "Read":
        path = tool_input.get("file_path", "")
        if path:
            return f": {path}"
    elif name == "Write":
        path = tool_input.get("file_path", "")
        if path:
            return f": {path}"
    elif name == "Edit":
        path = tool_input.get("file_path", "")
        if path:
            return f": {path}"
    elif name == "Glob":
        pattern = tool_input.get("pattern", "")
        if pattern:
            return f": {pattern}"
    elif name == "Grep":
        pattern = tool_input.get("pattern", "")
        if pattern:
            return f": {pattern}"
    elif name == "Task":
        desc = tool_input.get("description", "")
        if desc:
            return f": {desc}"
    return ""


def _print_event(event: dict[str, object], label: str) -> None:
    """Print a JSONL streaming event to stderr for live feedback.

    Handles both Claude Code and Codex event formats.
    """
    prefix = _format_prefix(label)
    event_type = str(event.get("type", ""))

    # --- Claude Code events ---
    if event_type == "result":
        return
    if event_type == "assistant" and "message" in event:
        message = event["message"]
        if isinstance(message, dict):
            for block in message.get("content", []):  # type: ignore[union-attr]
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = str(block.get("text", "")).strip()
                        if text:
                            # Skip raw JSON verdict — corbit displays it
                            clean = text.strip().strip("`").strip()
                            if clean.startswith("json"):
                                clean = clean[4:].strip()
                            try:
                                parsed = json.loads(clean)
                                if isinstance(parsed, dict) and "verdict" in parsed:
                                    continue
                            except (json.JSONDecodeError, TypeError):
                                pass
                            for line in text.splitlines():
                                stripped_line = line.strip()
                                if stripped_line:
                                    try:
                                        line_parsed = json.loads(stripped_line)
                                        if isinstance(line_parsed, dict) and "verdict" in line_parsed:
                                            continue
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                                sys.stderr.write(f"{prefix}{line}\n")
                            sys.stderr.flush()
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                        detail = _tool_detail(tool_name, tool_input)
                        sys.stderr.write(f"{prefix}▶ {tool_name}{detail}\n")
                        sys.stderr.flush()
        return

    # --- Codex events ---
    if event_type == "item.completed":
        item = event.get("item")
        if isinstance(item, dict):
            item_type = item.get("type", "")
            text = item.get("text", "")
            if item_type == "agent_message" and text:
                for line in str(text).splitlines():
                    sys.stderr.write(f"{prefix}{line}\n")
                sys.stderr.flush()
            elif item_type == "tool_call":
                tool_name = str(item.get("name", ""))
                detail = _tool_detail(tool_name, item.get("input", {}))
                sys.stderr.write(f"{prefix}▶ {tool_name}{detail}\n")
                sys.stderr.flush()
            elif item_type == "reasoning" and text:
                lines = str(text).splitlines()
                for i, line in enumerate(lines):
                    if i == 0:
                        sys.stderr.write(f"{prefix}💭 {line}\n")
                    else:
                        sys.stderr.write(f"{prefix}{line}\n")
                sys.stderr.flush()
        return
    if event_type == "turn.completed":
        usage = event.get("usage")
        if isinstance(usage, dict):
            tokens = usage.get("output_tokens", "?")
            sys.stderr.write(f"{prefix}✓ turn complete ({tokens} tokens)\n")
            sys.stderr.flush()
        return


async def run_streaming(
    args: list[str],
    cwd: Path,
    timeout: int,
    label: str = "",
    env: dict[str, str] | None = None,
) -> StreamResult:
    """Run a subprocess while streaming progress to the terminal in real-time.

    Streams JSON streaming events from stdout (type != 'result') to stderr
    for live feedback. The final JSON result is captured for parsing.
    stderr from the process is also streamed.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )

    stdout_lines: list[str] = []
    stderr_buf = bytearray()

    # Wire Ctrl+C to kill the child process group and cancel our tasks
    loop = asyncio.get_running_loop()
    original_handler = signal.getsignal(signal.SIGINT)
    _sigint_received = False

    def _handle_sigint() -> None:
        nonlocal _sigint_received
        _sigint_received = True
        if proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        # Cancel all running tasks in the event loop so we don't hang
        for task in asyncio.all_tasks(loop):
            if task is not asyncio.current_task():
                task.cancel()

    loop.add_signal_handler(signal.SIGINT, _handle_sigint)

    async def _read_stdout() -> None:
        assert proc.stdout is not None
        while True:
            try:
                line_bytes = await proc.stdout.readline()
            except ValueError:
                # Line exceeds asyncio buffer limit (~64 KB).
                # Drain chunks until we find the newline terminator.
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk or chunk.endswith(b"\n"):
                        break
                continue
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace")
            stdout_lines.append(line)

            # Stream progress events to terminal
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
                _print_event(event, label)
            except (json.JSONDecodeError, TypeError, KeyError):
                # Not JSON or unexpected shape — print raw
                sys.stderr.write(f"{_format_prefix(label)}{stripped}\n")
                sys.stderr.flush()

    async def _read_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_buf.extend(line)
            sys.stderr.write(line.decode(errors="replace"))
            sys.stderr.flush()

    try:
        await asyncio.wait_for(
            asyncio.gather(_read_stdout(), _read_stderr(), proc.wait()),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return StreamResult(
            returncode=-1,
            stdout="".join(stdout_lines),
            stderr="Agent timed out",
        )
    except asyncio.CancelledError:
        # Ctrl+C handler cancelled our tasks — kill the child if still alive
        if proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        raise KeyboardInterrupt("Aborted by user")
    finally:
        # Restore original signal handler
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (ValueError, RuntimeError):
            pass
        signal.signal(signal.SIGINT, original_handler)

    # Check if process was killed by our signal handler or user pressed Ctrl+C
    if _sigint_received or (proc.returncode and proc.returncode < 0):
        raise KeyboardInterrupt("Aborted by user")

    return StreamResult(
        returncode=proc.returncode or 0,
        stdout="".join(stdout_lines),
        stderr=stderr_buf.decode(errors="replace"),
    )
