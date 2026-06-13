"""Branch model and related enums"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional


class BranchStatus(Enum):
    """Status of a branch."""

    ACTIVE = "active"
    STALE = "stale"
    MERGED = "merged"


class SyncStatus(Enum):
    """Sync status of a branch with remote."""

    SYNCED = "synced"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    LOCAL_ONLY = "local-only"
    MERGED_GIT = "merged-git"
    MERGED_PR = "merged-pr"
    CLOSED_UNMERGED = "closed-unmerged"  # New status for branches with closed but unmerged PRs


@dataclass
class BranchAnalysisResult:
    """Shared branch analysis output consumed by CLI and TUI views."""

    branches: List["BranchDetails"] = field(default_factory=list)
    local_branch_names: List[str] = field(default_factory=list)
    branches_to_process: List[str] = field(default_factory=list)
    deletable_branches: List["BranchDetails"] = field(default_factory=list)
    removable_worktrees: List["BranchDetails"] = field(default_factory=list)
    current_branch: Optional[str] = None
    github_base_url: Optional[str] = None
    cached_count: int = 0
    refreshed_count: int = 0
    is_complete: bool = True


@dataclass
class BranchDetails:
    """Detailed information about a branch."""

    name: str
    last_commit_date: str
    age_days: int
    status: BranchStatus
    modified_files: Optional[bool]  # None = couldn't check
    untracked_files: Optional[bool]  # None = couldn't check
    staged_files: Optional[bool]  # None = couldn't check
    has_remote: bool
    sync_status: str
    pr_status: Optional[str] = None
    notes: Optional[str] = None  # Added notes field
    in_worktree: bool = False  # True if branch is checked out in a worktree
    is_worktree: bool = False  # True if this entry represents a worktree (not a branch)
    worktree_path: Optional[str] = None  # Path to the worktree directory if is_worktree=True
    worktree_is_orphaned: bool = False  # True if branch's worktree directory is missing
