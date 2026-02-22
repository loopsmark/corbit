"""Abstract base class for coder agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from corbit.models import AgentResult


class CoderAgent(ABC):
    """Base class for all coder agent backends."""

    @abstractmethod
    async def implement(
        self,
        prompt: str,
        worktree_path: Path,
        session_id: str | None = None,
        timeout: int = 600,
        label: str = "",
    ) -> AgentResult:
        """Run the initial implementation from a prompt."""

    @abstractmethod
    async def apply_feedback(
        self,
        feedback: str,
        worktree_path: Path,
        session_id: str | None = None,
        timeout: int = 600,
        label: str = "",
    ) -> AgentResult:
        """Apply review feedback to the existing implementation."""
