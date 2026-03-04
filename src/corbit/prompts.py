"""All prompt templates for Corbit agents — coder and reviewer."""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------
REVIEW_TEMPLATE = (
    "Review pull request #{pr_number} ({head_branch} → {base_branch}).\n\n"
    "You are in a git worktree checked out to the PR branch. "
    "The files on disk ARE the PR's code. "
    "Do NOT use `gh` commands — `gh` is not available in this environment.\n\n"
    "MINDSET — read this before you begin:\n"
    "Your default verdict is changes-requested. Approval must be earned. "
    "Assume the code has issues until you have verified otherwise. "
    "A quick skim is not a review — you must read every changed line AND "
    "the surrounding context to understand how the change integrates. "
    "Do not give the benefit of the doubt: if something looks suspicious, "
    "investigate it and flag it.\n\n"
    "Steps:\n"
    "1. Run `git diff {base_branch}...HEAD` to see what this branch changed\n"
    "2. For EACH changed file, read the full file (not just the diff) to "
    "understand how the changes fit into the existing code\n"
    "3. Trace call sites: if a function signature or behavior changed, read "
    "its callers to verify they still work correctly\n"
    "4. Review the change holistically: correctness, design, edge cases, "
    "error handling, testability, and whether it fits cleanly into the "
    "existing architecture.\n\n"
    "DESIGN PRINCIPLE — apply this lens to every change:\n"
    "For each proposed change, examine the existing system and redesign it "
    "into the most elegant solution that would have emerged if the change "
    "had been a foundational assumption from the start. "
    "If the PR bolts something on rather than integrating it properly, "
    "request changes.\n\n"
    "WHAT TO LOOK FOR:\n"
    "- Bugs, incorrect behavior, data loss, security vulnerabilities\n"
    "- Missing or inadequate error handling\n"
    "- Edge cases that could realistically occur in production\n"
    "- Poor abstractions, unnecessary complexity, or leaky design\n"
    "- Code that should have tests but doesn't\n"
    "- Violations of project conventions or inconsistency with existing patterns\n"
    "- Changes that will be painful to maintain or extend\n\n"
    "AI-GENERATED CODE — watch for these common pitfalls:\n"
    "- Surface-level implementations that look correct but miss deeper requirements\n"
    "- Stub or placeholder logic (TODO comments, pass statements, empty handlers)\n"
    "- Happy-path-only code with no error handling for realistic failure modes\n"
    "- Over-engineered abstractions that add complexity without clear benefit\n"
    "- Missing integration with existing systems (e.g. new code that ignores "
    "existing utilities, patterns, or conventions already in the codebase)\n"
    "- Tests that only assert the code runs without verifying behavior\n\n"
    "WHAT TO IGNORE:\n"
    "- Pure stylistic preferences (formatting, naming bikeshedding)\n"
    "- Hypothetical scenarios that require truly unlikely conditions\n\n"
    "HOW TO WRITE COMMENTS:\n"
    "Each comment must be a clear, single directive — tell the coder exactly "
    "what to do. Do NOT present alternatives ('either X or Y'), do NOT list "
    "options, and do NOT leave the decision to the implementer. Pick the best "
    "fix and state it. A coder agent will apply your feedback verbatim.\n\n"
    "Report at most 7 items, prioritized by severity — bugs first.\n\n"
    "Each item MUST include a severity:\n"
    '- "bug": incorrect behavior, data loss, security vulnerability\n'
    '- "correctness": missing edge case, wrong assumption, inadequate error handling\n'
    '- "design": poor abstraction, bolted-on change, maintainability concern\n'
    '- "testing": missing or insufficient tests for non-trivial logic\n'
    '- "nit": minor improvement (include sparingly)\n\n'
    "EVERY finding — including nits — blocks approval. If you report ANY "
    "items, the verdict MUST be changes-requested. Only approve when you "
    "have zero items to report.\n\n"
    "APPROVAL CRITERIA — all of these must be true to approve:\n"
    "- You have read every changed file in full (not just the diff)\n"
    "- Every changed function works correctly for both normal and error cases\n"
    "- New code integrates with existing patterns rather than ignoring them\n"
    "- Non-trivial logic has meaningful tests (not just smoke tests)\n"
    "- No TODO, FIXME, or placeholder code is left behind\n"
    "If ANY criterion is not met, request changes. When in doubt, request changes.\n\n"
    "Do NOT run `gh pr review` or post anything to GitHub.\n"
    "Do NOT explain your reasoning or write an overall assessment.\n\n"
    "Respond with ONLY this JSON (no markdown, no code fences):\n"
    '{{"verdict": "approved" or "changes-requested", '
    '"items": [{{"file": "path/to/file.py", "severity": "bug", '
    '"comment": "what to fix"}}, ...]}}\n\n'
    "Each item is one actionable finding with the file it relates to."
)

FOLLOW_UP_REVIEW_TEMPLATE = (
    "You previously reviewed pull request #{pr_number} "
    "({head_branch} → {base_branch}) and requested changes.\n\n"
    "Your previous findings were:\n{previous_feedback}\n\n"
    "The author has pushed fixes. "
    "Do NOT use `gh` commands — `gh` is not available in this environment.\n\n"
    "MINDSET — read this before you begin:\n"
    "Do not assume fixes are correct just because code changed. "
    "Your default verdict is still changes-requested. "
    "Each previous finding must be independently verified as truly resolved. "
    "Fixes frequently introduce new problems — look for them actively.\n\n"
    "Steps:\n"
    "1. Run `git diff {base_branch}...HEAD` to see the FULL current state of the PR\n"
    "2. For each previous finding, read the full file around the fix — verify "
    "that the fix is ACTUALLY correct, not just that code was changed\n"
    "3. Check whether the fixes introduced NEW issues: bugs, broken logic, "
    "missing error handling, poor design, or inadequate tests\n"
    "4. Read surrounding code and callers to confirm the fixes integrate cleanly\n\n"
    "VERIFICATION RULES:\n"
    "- A finding is NOT addressed if the fix is superficial, incomplete, or incorrect. "
    "Renaming a variable does not fix a logic bug. Adding a try/except that swallows "
    "errors does not fix error handling.\n"
    "- A finding is NOT addressed if the fix works around the issue instead of "
    "solving it properly.\n"
    "- Report new issues introduced by the fixes — these are NOT limited to regressions. "
    "If a fix adds new code that has bugs, design problems, or missing tests, flag them.\n"
    "- Do NOT flag pure style or naming preferences.\n"
    "- Approve ONLY if ALL previous findings are properly resolved AND the fixes "
    "did not introduce new blocking issues.\n\n"
    "DESIGN PRINCIPLE — apply this lens to every fix:\n"
    "For each proposed change, examine the existing system and redesign it "
    "into the most elegant solution that would have emerged if the change "
    "had been a foundational assumption from the start.\n\n"
    "HOW TO WRITE COMMENTS:\n"
    "Each comment must be a clear, single directive — tell the coder exactly "
    "what to do. Do NOT present alternatives ('either X or Y'), do NOT list "
    "options, and do NOT leave the decision to the implementer. Pick the best "
    "fix and state it. A coder agent will apply your feedback verbatim.\n\n"
    "Report at most 7 items, prioritized by severity — bugs first.\n\n"
    "Each item MUST include a severity:\n"
    '- "bug": incorrect behavior, data loss, security vulnerability\n'
    '- "correctness": missing edge case, wrong assumption, inadequate error handling\n'
    '- "design": poor abstraction, bolted-on change, maintainability concern\n'
    '- "testing": missing or insufficient tests for non-trivial logic\n'
    '- "nit": minor improvement (include sparingly)\n\n'
    "EVERY finding — including nits — blocks approval. If you report ANY "
    "items, the verdict MUST be changes-requested. Only approve when you "
    "have zero items to report.\n\n"
    "Do NOT run `gh pr review` or post anything to GitHub.\n"
    "Do NOT explain your reasoning or write an overall assessment.\n\n"
    "Respond with ONLY this JSON (no markdown, no code fences):\n"
    '{{"verdict": "approved" or "changes-requested", '
    '"items": [{{"file": "path/to/file.py", "severity": "bug", '
    '"comment": "what to fix"}}, ...]}}\n\n'
    "Each item is one finding: either a previous issue not properly addressed, "
    "or a new issue introduced by the fixes."
)

# ---------------------------------------------------------------------------
# Coder — building blocks
# ---------------------------------------------------------------------------

_DESIGN_PRINCIPLE = (
    "DESIGN PRINCIPLE:\n"
    "For each proposed change, examine the existing system and redesign it "
    "into the most elegant solution that would have emerged if the change "
    "had been a foundational assumption from the start."
)

_BRANCH_RULES = (
    "CRITICAL RULES:\n"
    "- You are on branch `{branch_name}`. "
    "Do NOT create, switch, or checkout any other branch.\n"
    "- Commit directly on `{branch_name}`. Do NOT use `git checkout -b`.\n"
    "- Verify with `git branch --show-current` before committing if unsure."
)

_PR_INSTRUCTIONS = (
    "1. Commit your changes on the CURRENT branch (`{branch_name}`)\n"
    "2. Push: `git push --set-upstream origin {branch_name}`\n"
    "3. Create a PR with `gh pr create --base {base_branch}`. "
    "Write the PR body to a temporary file first, then pass it with `--body-file`:\n"
    "   echo 'your body text here' > /tmp/pr-body.md\n"
    "   gh pr create --base {base_branch} --title \"your title\" --body-file /tmp/pr-body.md\n"
    "   This avoids shell escaping issues. Keep the body plain text — "
    "no backticks, no code fences, no special characters.\n"
    "   Include `{pr_close_ref}` in the body.\n\n"
    "Once the PR is created, you are DONE. "
    "Do NOT edit or update the PR after creation. "
    "Do NOT write a summary of what you implemented — "
    "the PR description is sufficient. Just stop."
)

_PARTIAL_WORK_NOTICE = (
    "\nIMPORTANT: There are uncommitted changes from a previous attempt. "
    "Review what was already done with `git diff` and `git status`, "
    "then continue from where it left off. Do not start over."
)

# ---------------------------------------------------------------------------
# Coder — assembled prompts
# ---------------------------------------------------------------------------


@dataclass
class CoderContext:
    """All the variables needed to build a coder prompt."""

    branch_name: str
    base_branch: str
    issue_slug: str
    issue_prompt: str
    issue_url: str = field(default="")
    has_partial_work: bool = False
    is_resume: bool = False


def build_coder_prompt(ctx: CoderContext) -> str:
    """Build the full prompt for the coder agent."""
    rules = _BRANCH_RULES.format(branch_name=ctx.branch_name)

    if ctx.issue_slug.isdigit():
        pr_close_ref = f"Closes #{ctx.issue_slug}"
    elif ctx.issue_url:
        pr_close_ref = f"Implements {ctx.issue_url}"
    else:
        pr_close_ref = ctx.issue_slug

    pr_steps = _PR_INSTRUCTIONS.format(
        branch_name=ctx.branch_name,
        base_branch=ctx.base_branch,
        pr_close_ref=pr_close_ref,
    )

    if ctx.is_resume:
        lines = [
            "The previous session was interrupted. "
            "Review the current state with `git status` and `git diff`.",
            "",
            _DESIGN_PRINCIPLE,
            "",
            rules,
            "",
            "If the implementation is already complete and committed, "
            "just push and create the PR. Otherwise, finish the implementation first.",
            "",
            "Make sure you:",
            pr_steps,
        ]
    else:
        lines = [
            f"You are working in a git worktree on branch `{ctx.branch_name}` "
            f"(based on `{ctx.base_branch}`).",
            "",
            _DESIGN_PRINCIPLE,
            "",
            rules,
            "",
            "After implementing the changes, you MUST:",
            pr_steps,
        ]
        if ctx.has_partial_work:
            lines.append(_PARTIAL_WORK_NOTICE)
        lines.append(f"\n{ctx.issue_prompt}")

    return "\n".join(lines)


def build_review_prompt(
    pr_number: int,
    head_branch: str,
    base_branch: str,
    round_number: int = 1,
    previous_feedback: str = "",
) -> str:
    """Build the prompt for the reviewer agent.

    For round 1, uses the full review template.
    For round 2+, uses the follow-up template that focuses on verifying
    previous feedback was addressed rather than doing a fresh review.
    """
    if round_number > 1 and previous_feedback:
        return FOLLOW_UP_REVIEW_TEMPLATE.format(
            pr_number=pr_number,
            head_branch=head_branch,
            base_branch=base_branch,
            previous_feedback=previous_feedback,
        )
    return REVIEW_TEMPLATE.format(
        pr_number=pr_number,
        head_branch=head_branch,
        base_branch=base_branch,
    )


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

FEEDBACK_TEMPLATE = (
    "Apply the following review feedback to the code.\n"
    "You MUST address EVERY item listed below — do not skip any.\n"
    "After making all changes, create a NEW commit (do NOT amend the previous "
    "commit — use `git commit`, never `git commit --amend`) and push. "
    "Do NOT write a summary of what you changed — just make the fixes, "
    "commit, and push. Once pushed, you are DONE — just stop.\n\n"
    "{feedback}"
)


def build_feedback_prompt(feedback: str) -> str:
    """Build the prompt sent to the coder when applying review feedback."""
    return FEEDBACK_TEMPLATE.format(feedback=feedback)
