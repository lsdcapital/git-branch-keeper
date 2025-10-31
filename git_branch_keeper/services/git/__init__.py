"""Git-related services for git-branch-keeper."""

from .operations import GitOperations
from .worktrees import WorktreeService
from .github import GitHubService
from .merge_detector import MergeDetector
from .branch_queries import BranchQueries

__all__ = [
    "GitOperations",
    "WorktreeService",
    "GitHubService",
    "MergeDetector",
    "BranchQueries",
]
