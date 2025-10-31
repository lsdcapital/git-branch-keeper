"""Worktree data models."""

from dataclasses import dataclass


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: str
    branch_name: str
    commit_sha: str
    is_main: bool  # Is this the main working tree?
    is_orphaned: bool  # Directory missing?

    def __str__(self) -> str:
        """String representation of worktree."""
        status = "orphaned" if self.is_orphaned else "active"
        main_marker = " (main)" if self.is_main else ""
        return f"{self.branch_name} @ {self.path}{main_marker} [{status}]"
