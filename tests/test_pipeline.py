"""Tests for pipeline and config modules."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from corbit.config import load_config
from corbit.models import AgentBackend, CorbitConfig, IterationMode
from corbit.models import ReviewItem, ReviewSeverity, ReviewVerdict
from corbit.reviewer import Reviewer, _format_review_body


def test_load_config_defaults() -> None:
    with patch("corbit.config._find_config_file", return_value=None):
        config = load_config()
    assert config.coder_backend == AgentBackend.CLAUDE_CODE
    assert config.reviewer_backend == AgentBackend.CLAUDE_CODE
    assert config.max_review_rounds == 4


def test_load_config_cli_overrides() -> None:
    with patch("corbit.config._find_config_file", return_value=None):
        config = load_config(
            backend="codex",
            reviewer_backend="codex",
            max_rounds=5,
            iteration_mode="single-pass",
            workers=4,
        )
    assert config.coder_backend == AgentBackend.CODEX
    assert config.reviewer_backend == AgentBackend.CODEX
    assert config.max_review_rounds == 5
    assert config.iteration_mode == IterationMode.SINGLE_PASS
    assert config.parallel_workers == 4


def test_load_config_mixed_backends() -> None:
    with patch("corbit.config._find_config_file", return_value=None):
        config = load_config(backend="codex", reviewer_backend="claude-code")
    assert config.coder_backend == AgentBackend.CODEX
    assert config.reviewer_backend == AgentBackend.CLAUDE_CODE


def test_load_config_env_overrides() -> None:
    env = {
        "CORBIT_BACKEND": "codex",
        "CORBIT_MAX_ROUNDS": "7",
        "CORBIT_PARALLEL": "8",
    }
    with (
        patch("corbit.config._find_config_file", return_value=None),
        patch.dict(os.environ, env, clear=False),
    ):
        config = load_config()
    assert config.coder_backend == AgentBackend.CODEX
    assert config.max_review_rounds == 7
    assert config.parallel_workers == 8


def test_reviewer_parse_approved() -> None:
    reviewer = Reviewer()
    result = reviewer._parse_review('{"verdict": "approved", "comments": "LGTM"}')
    assert result.verdict.value == "approved"
    assert result.comments == "LGTM"


def test_reviewer_parse_changes_requested() -> None:
    reviewer = Reviewer()
    result = reviewer._parse_review(
        '{"verdict": "changes-requested", "comments": "Fix types"}'
    )
    assert result.verdict.value == "changes-requested"


def test_reviewer_parse_wrapped_json() -> None:
    reviewer = Reviewer()
    outer = '{"result": "{\\"verdict\\": \\"approved\\", \\"comments\\": \\"ok\\"}"}'
    result = reviewer._parse_review(outer)
    assert result.verdict.value == "approved"


def test_reviewer_parse_invalid() -> None:
    reviewer = Reviewer()
    result = reviewer._parse_review("not json at all")
    assert result.verdict.value == "error"


def test_reviewer_codex_backend_args() -> None:
    reviewer = Reviewer(backend=AgentBackend.CODEX)
    args = reviewer._build_args("test prompt")
    assert args[0] == "codex"
    assert "--full-auto" in args
    assert "test prompt" in args


def test_reviewer_claude_backend_args() -> None:
    reviewer = Reviewer(backend=AgentBackend.CLAUDE_CODE, model="opus")
    args = reviewer._build_args("test prompt")
    assert args[0] == "claude"
    assert "--model" in args
    assert "opus" in args


def test_reviewer_parse_severity() -> None:
    reviewer = Reviewer()
    raw = (
        '{"verdict": "changes-requested", "items": ['
        '{"file": "a.py", "severity": "bug", "comment": "crash"},'
        '{"file": "b.py", "severity": "nit", "comment": "rename var"}'
        ']}'
    )
    result = reviewer._parse_review(raw)
    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED
    assert len(result.items) == 2
    assert result.items[0].severity == ReviewSeverity.BUG
    assert result.items[1].severity == ReviewSeverity.NIT
    # Feedback to coder should only contain blocking items
    assert "crash" in result.comments
    assert "rename var" not in result.comments


def test_reviewer_parse_severity_fallback() -> None:
    """Items without severity default to correctness."""
    reviewer = Reviewer()
    raw = '{"verdict": "changes-requested", "items": [{"file": "a.py", "comment": "fix"}]}'
    result = reviewer._parse_review(raw)
    assert result.items[0].severity == ReviewSeverity.CORRECTNESS


def test_reviewer_nits_only_treated_as_approved() -> None:
    reviewer = Reviewer()
    raw = (
        '{"verdict": "changes-requested", "items": ['
        '{"file": "a.py", "severity": "nit", "comment": "rename"}'
        ']}'
    )
    result = reviewer._parse_review(raw)
    assert result.verdict == ReviewVerdict.APPROVED
    assert len(result.items) == 1


def test_reviewer_parse_design_severity_is_blocking() -> None:
    """Design items should block approval, not be treated as nits."""
    reviewer = Reviewer()
    raw = (
        '{"verdict": "changes-requested", "items": ['
        '{"file": "a.py", "severity": "design", "comment": "bolted-on pattern"}'
        ']}'
    )
    result = reviewer._parse_review(raw)
    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED
    assert "bolted-on pattern" in result.comments


def test_reviewer_parse_testing_severity_is_blocking() -> None:
    """Testing items should block approval, not be treated as nits."""
    reviewer = Reviewer()
    raw = (
        '{"verdict": "changes-requested", "items": ['
        '{"file": "a.py", "severity": "testing", "comment": "missing unit tests"}'
        ']}'
    )
    result = reviewer._parse_review(raw)
    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED
    assert "missing unit tests" in result.comments


def test_reviewer_parse_json_in_assistant_event() -> None:
    """Reviewer JSON embedded in a JSONL assistant event must be parsed correctly.

    This covers the real-world failure where the result event's ``result``
    field is empty/missing and the reviewer's JSON only appears inside the
    ``assistant`` event's text block.
    """
    reviewer_json = '{"verdict": "changes-requested", "items": [{"file": "a.py", "severity": "correctness", "comment": "fix me"}]}'
    assistant_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": reviewer_json}],
        },
    })
    result_event = json.dumps({"type": "result", "result": "", "session_id": "s1"})
    raw = "\n".join([assistant_event, result_event]) + "\n"

    reviewer = Reviewer()
    result = reviewer._parse_review(raw)
    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED
    assert len(result.items) == 1
    assert result.items[0].file == "a.py"


def test_reviewer_parse_json_with_embedded_newlines() -> None:
    """Comments with literal newlines must survive normalization and be parsed."""
    # Reviewer outputs JSON whose comment strings contain real newlines
    reviewer_json = (
        '{"verdict": "changes-requested", "items": ['
        '{"file": "repo.py", "severity": "correctness",'
        ' "comment": "line one\nline two\nline three"}]}'
    )
    assistant_event = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": reviewer_json}]},
    })
    result_event = json.dumps({"type": "result", "result": reviewer_json, "session_id": "s2"})
    raw = "\n".join([assistant_event, result_event]) + "\n"

    reviewer = Reviewer()
    result = reviewer._parse_review(raw)
    assert result.verdict == ReviewVerdict.CHANGES_REQUESTED
    assert "line one" in result.items[0].comment


def test_format_review_body_grouped() -> None:
    items = [
        ReviewItem(file="a.py", comment="crash", severity=ReviewSeverity.BUG),
        ReviewItem(file="b.py", comment="edge case", severity=ReviewSeverity.CORRECTNESS),
        ReviewItem(file="d.py", comment="bolted-on", severity=ReviewSeverity.DESIGN),
        ReviewItem(file="e.py", comment="no tests", severity=ReviewSeverity.TESTING),
        ReviewItem(file="c.py", comment="rename", severity=ReviewSeverity.NIT),
    ]
    body = _format_review_body(items)
    # Sections are ordered by severity
    assert body.index("### Bugs") < body.index("### Correctness")
    assert body.index("### Correctness") < body.index("### Design")
    assert body.index("### Design") < body.index("### Testing")
    assert body.index("### Testing") < body.index("### Nits")
    assert "**`a.py`**" in body
    assert "**`c.py`**" in body
    assert "**`d.py`**" in body
    assert "**`e.py`**" in body
