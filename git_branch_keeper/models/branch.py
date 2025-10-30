"""Branch model and related enums"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional

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
    notes: Optional[str] = None # Added notes field
    in_worktree: bool = False  # True if branch is checked out in a worktree