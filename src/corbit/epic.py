"""Epic issue detection and child issue extraction."""

from __future__ import annotations

import re

from corbit.models import EpicPlan, GitHubIssue


def is_epic(issue: GitHubIssue) -> bool:
    """Return True if this issue is an epic containing child issues."""
    if any(label.startswith("epic:") for label in issue.labels):
        return True
    return len(_extract_all_issue_refs(issue.body)) > 1


def extract_epic_plan(issue: GitHubIssue) -> EpicPlan:
    """Extract an ordered execution plan from an epic issue body.

    Tries three parsing strategies in order:
    1. 'Suggested Implementation Order' numbered list
    2. Markdown dependency table with a 'Depends on' column
    3. Fall back: all #N references as individual sequential steps
    """
    body = issue.body

    groups = _parse_implementation_order(body)
    if not groups:
        groups = _parse_dependency_table(body)
    if not groups:
        refs = list(dict.fromkeys(_extract_all_issue_refs(body)))
        groups = [[ref] for ref in refs]

    return EpicPlan(parent_issue=issue.number, groups=groups)


def _extract_all_issue_refs(text: str) -> list[int]:
    return [int(m) for m in re.findall(r'#(\d+)', text)]


def _parse_implementation_order(body: str) -> list[list[int]] | None:
    """Parse a 'Suggested Implementation Order' numbered list into groups.

    Each numbered line becomes one group. Multiple issues on a single line
    (separated by '+', ',', or whitespace) are treated as parallel.
    """
    match = re.search(
        r'###?\s+Suggested Implementation Order\s*\n(.*?)(?=\n##|\Z)',
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None

    groups: list[list[int]] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not re.match(r'^\d+[.)]\s+', line):
            continue
        # Only parse issue refs from the "header" portion before the — separator
        # so we don't capture refs mentioned in prose descriptions.
        header = re.split(r'\s+[—–-]{1,2}\s+', line, maxsplit=1)[0]
        refs = [int(m) for m in re.findall(r'#(\d+)', header)]
        if refs:
            groups.append(refs)

    return groups or None


def _parse_dependency_table(body: str) -> list[list[int]] | None:
    """Parse a markdown table with a 'Depends on' column into topological groups."""
    lines = body.splitlines()
    header_idx: int | None = None
    dep_col_idx: int | None = None
    issue_col_idx: int | None = None

    for i, line in enumerate(lines):
        if '|' not in line or 'depends on' not in line.lower():
            continue
        cols = [c.strip().lower() for c in line.split('|')]
        dep_col_idx = next((j for j, c in enumerate(cols) if 'depends on' in c), None)
        issue_col_idx = next((j for j, c in enumerate(cols) if c in ('#', 'issue', '#issue', 'issue #')), None)
        if dep_col_idx is not None:
            header_idx = i
            if issue_col_idx is None:
                issue_col_idx = 1  # default: first data column
            break

    if header_idx is None or dep_col_idx is None:
        return None

    dependencies: dict[int, list[int]] = {}
    for line in lines[header_idx + 2:]:
        if not line.strip().startswith('|'):
            break
        cols = [c.strip() for c in line.split('|')]
        if len(cols) <= max(issue_col_idx, dep_col_idx):
            continue

        issue_refs = [int(m) for m in re.findall(r'#(\d+)', cols[issue_col_idx])]
        if not issue_refs:
            continue
        issue_num = issue_refs[0]

        dep_cell = cols[dep_col_idx]
        if dep_cell in ('—', '-', ''):
            dependencies[issue_num] = []
        else:
            dependencies[issue_num] = [int(m) for m in re.findall(r'#(\d+)', dep_cell)]

    if not dependencies:
        return None

    return _topological_groups(dependencies)


def _topological_groups(dependencies: dict[int, list[int]]) -> list[list[int]]:
    """Group issues into sequential batches via Kahn's topological sort."""
    all_issues = set(dependencies.keys())
    in_degree = {
        n: sum(1 for d in dependencies[n] if d in all_issues)
        for n in all_issues
    }

    groups: list[list[int]] = []
    remaining = set(all_issues)

    while remaining:
        ready = sorted(n for n in remaining if in_degree[n] == 0)
        if not ready:
            # Cycle — dump the rest as one group
            groups.append(sorted(remaining))
            break
        groups.append(ready)
        for n in ready:
            remaining.remove(n)
        for m in remaining:
            if any(d in [n for n in ready] for d in dependencies[m]):
                in_degree[m] -= sum(
                    1 for d in dependencies[m] if d in ready
                )

    return groups
