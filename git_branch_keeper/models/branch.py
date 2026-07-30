"""Branch model and related enums"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BranchStatus(Enum):
    """Status of a branch."""

    ACTIVE = "active"
    STALE = "stale"
    MERGED = "merged"
    # Branch has no commits of its own relative to main. Reachability would call
    # this "merged" (a tip that is already in main is trivially an ancestor of it),
    # but nothing was ever merged - there was nothing to merge. Kept distinct so the
    # label is honest and so it stays out of every [STALE, MERGED] cleanup check.
    UNSTARTED = "unstarted"


class SyncStatus(Enum):
    """Sync status of a branch with remote."""

    SYNCED = "synced"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    LOCAL_ONLY = "local-only"
    # Exists on the remote with no local head. Positively merged rows are remote
    # cleanup candidates; stale/unmerged rows remain read-only.
    REMOTE_ONLY = "remote-only"
    MERGED_GIT = "merged-git"
    MERGED_PR = "merged-pr"
    NO_COMMITS = "no-commits"  # No commits unique to the branch (see BranchStatus.UNSTARTED)
    CLOSED_UNMERGED = "closed-unmerged"  # New status for branches with closed but unmerged PRs


@dataclass(frozen=True)
class BranchAnalysisProgress:
    """Progress update emitted while analyzing branches."""

    phase: str
    current: int = 0
    total: int | None = None
    message: str | None = None

    @property
    def percent(self) -> int | None:
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

    branches: list[BranchDetails] = field(default_factory=list)
    local_branch_names: list[str] = field(default_factory=list)
    branches_to_process: list[str] = field(default_factory=list)
    deletable_branches: list[BranchDetails] = field(default_factory=list)
    removable_worktrees: list[BranchDetails] = field(default_factory=list)
    current_branch: str | None = None
    github_base_url: str | None = None
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
    modified_files: bool | None  # None = couldn't check
    untracked_files: bool | None  # None = couldn't check
    staged_files: bool | None  # None = couldn't check
    has_remote: bool
    sync_status: str
    # Whether refs/heads/<name> exists. Defaults True because every branch GBK saw
    # before remote enumeration was local by construction. `has_remote and not
    # has_local` is the remote-only case; see BranchRefResolver.
    has_local: bool = True
    # Compact PR display value. Source branches store the selected PR number;
    # protected target branches use ``target:<open count>``.
    pr_status: str | None = None
    pr_details: dict[str, Any] | None = None  # Structured PR metadata from provider APIs
    notes: str | None = None  # Added notes field
    in_worktree: bool = False  # True if branch is checked out in a worktree
    is_worktree: bool = False  # True if this entry represents a worktree (not a branch)
    worktree_path: str | None = None  # Path to the worktree directory if is_worktree=True
    worktree_is_orphaned: bool = False  # True if branch's worktree directory is missing
    merge_detection: dict[str, Any] | None = None  # Structured merge-detection details
