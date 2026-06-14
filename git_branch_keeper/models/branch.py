"""Branch model and related enums"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


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


@dataclass(frozen=True)
class BranchAnalysisProgress:
    """Progress update emitted while analyzing branches."""

    phase: str
    current: int = 0
    total: Optional[int] = None
    message: Optional[str] = None

    @property
    def percent(self) -> Optional[int]:
        """Return whole-number completion percentage when a total is known."""
        if self.total is None:
            return None
        if self.total <= 0:
            return 100
        return max(0, min(100, round((self.current / self.total) * 100)))


BranchAnalysisProgressCallback = Callable[[BranchAnalysisProgress], None]

# Generic aliases for any long-running GBK operation that wants to reuse the
# same CLI/TUI progress plumbing as branch analysis.
OperationProgress = BranchAnalysisProgress
OperationProgressCallback = Callable[[OperationProgress], None]


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
    pr_details: Optional[Dict[str, Any]] = None  # Structured PR metadata from provider APIs
    notes: Optional[str] = None  # Added notes field
    in_worktree: bool = False  # True if branch is checked out in a worktree
    is_worktree: bool = False  # True if this entry represents a worktree (not a branch)
    worktree_path: Optional[str] = None  # Path to the worktree directory if is_worktree=True
    worktree_is_orphaned: bool = False  # True if branch's worktree directory is missing
    merge_detection: Optional[Dict[str, Any]] = None  # Structured merge-detection details
