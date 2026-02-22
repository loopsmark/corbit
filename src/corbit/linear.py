"""Linear API client (GraphQL via httpx)."""

from __future__ import annotations

import os

import httpx

from corbit.models import IssueComment, LinearEpicPlan, LinearIssue

_GRAPHQL_URL = "https://api.linear.app/graphql"


def _get_api_key(api_key: str | None) -> str:
    if api_key:
        return api_key
    key = os.environ.get("LINEAR_API_KEY", "")
    if not key:
        raise RuntimeError(
            "LINEAR_API_KEY is not set. "
            "Set it via the LINEAR_API_KEY environment variable or linear_api_key in .corbit.toml."
        )
    return key


async def _graphql(query: str, variables: dict, api_key: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL error: {data['errors']}")
        return data["data"]  # type: ignore[return-value]


async def fetch_issue(identifier: str, api_key: str | None = None) -> LinearIssue:
    """Fetch a Linear issue by its identifier (e.g. 'ENG-123')."""
    key = _get_api_key(api_key)
    query = """
    query FetchIssue($identifier: String!) {
      issue(id: $identifier) {
        id
        identifier
        title
        description
        url
        state { name }
        team { key }
        labels { nodes { name } }
        comments { nodes { user { name } body } }
      }
    }
    """
    data = await _graphql(query, {"identifier": identifier}, key)
    issue_data = data.get("issue")
    if issue_data is None:
        raise RuntimeError(f"Linear issue not found: {identifier}")

    comments = [
        IssueComment(
            author=c["user"]["name"] if c.get("user") else "unknown",
            body=c["body"],
        )
        for c in issue_data["comments"]["nodes"]
    ]
    label_names = [lb["name"] for lb in issue_data["labels"]["nodes"]]

    return LinearIssue(
        identifier=issue_data["identifier"],
        title=issue_data["title"],
        body=issue_data.get("description") or "",
        url=issue_data.get("url") or "",
        state=issue_data["state"]["name"] if issue_data.get("state") else "",
        team_key=issue_data["team"]["key"] if issue_data.get("team") else "",
        labels=label_names,
        comments=comments,
    )


async def fetch_epic_plan(identifier: str, api_key: str | None = None) -> LinearEpicPlan:
    """Fetch child issues and their blocking relations, return a dependency-ordered plan."""
    key = _get_api_key(api_key)
    query = """
    query FetchEpicPlan($identifier: String!) {
      issue(id: $identifier) {
        children {
          nodes {
            identifier
            relations {
              nodes {
                type
                relatedIssue {
                  identifier
                }
              }
            }
          }
        }
      }
    }
    """
    data = await _graphql(query, {"identifier": identifier}, key)
    issue_data = data.get("issue")
    if issue_data is None:
        raise RuntimeError(f"Linear issue not found: {identifier}")

    children_nodes = issue_data["children"]["nodes"]
    child_set = {node["identifier"] for node in children_nodes}

    # Build dependency graph: deps[child] = list of children it depends on (i.e. that block it)
    deps: dict[str, list[str]] = {node["identifier"]: [] for node in children_nodes}
    for node in children_nodes:
        ident = node["identifier"]
        for rel in node["relations"]["nodes"]:
            if rel["type"] == "BLOCKS":
                blocked = rel["relatedIssue"]["identifier"]
                # ident blocks blocked → blocked depends on ident
                if blocked in child_set:
                    deps[blocked].append(ident)

    groups = _topological_groups(deps)
    return LinearEpicPlan(parent_identifier=identifier, groups=groups)


def _topological_groups(deps: dict[str, list[str]]) -> list[list[str]]:
    """Group issues into sequential batches via Kahn's topological sort."""
    all_issues = set(deps.keys())
    in_degree = {
        n: sum(1 for d in deps[n] if d in all_issues)
        for n in all_issues
    }

    groups: list[list[str]] = []
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
            in_degree[m] -= sum(1 for d in deps[m] if d in ready)

    return groups


async def post_comment(identifier: str, body: str, api_key: str | None = None) -> None:
    """Post a comment on a Linear issue."""
    key = _get_api_key(api_key)

    # Fetch internal UUID for the issue
    id_query = """
    query GetIssueId($identifier: String!) {
      issue(id: $identifier) {
        id
      }
    }
    """
    id_data = await _graphql(id_query, {"identifier": identifier}, key)
    issue_id = id_data["issue"]["id"]

    mutation = """
    mutation CreateComment($issueId: String!, $body: String!) {
      commentCreate(input: { issueId: $issueId, body: $body }) {
        success
      }
    }
    """
    await _graphql(mutation, {"issueId": issue_id, "body": body}, key)
