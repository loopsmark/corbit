"""Backend name → agent class mapping."""

from __future__ import annotations

from corbit.agents.base import CoderAgent
from corbit.agents.claude_code import ClaudeCodeAgent
from corbit.agents.codex import CodexAgent
from corbit.models import AgentBackend

_REGISTRY: dict[AgentBackend, type[CoderAgent]] = {
    AgentBackend.CLAUDE_CODE: ClaudeCodeAgent,
    AgentBackend.CODEX: CodexAgent,
}


def get_agent(backend: AgentBackend, model: str = "", skip_permissions: bool = True) -> CoderAgent:
    """Create a coder agent instance for the given backend."""
    agent_cls = _REGISTRY.get(backend)
    if agent_cls is None:
        raise ValueError(f"Unknown agent backend: {backend}")
    if backend == AgentBackend.CLAUDE_CODE:
        return agent_cls(model=model, skip_permissions=skip_permissions)
    return agent_cls(model=model)
