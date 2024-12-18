"""Service for determining branch status and related operations"""
from typing import List, Optional
from fnmatch import fnmatch
from git_branch_keeper.models.branch import BranchStatus, SyncStatus

class BranchStatusService:
    def __init__(self, git_service, github_service, config: dict):
        self.git_service = git_service
        self.github_service = github_service
        self.config = config
        self.protected_branches = config.get('protected_branches', ["main", "master"])
        self.ignore_patterns = config.get('ignore_patterns', [])
        self.min_stale_days = config.get('stale_days', 30)
        self.status_filter = config.get('status_filter', "all")

    def get_branch_status(self, branch_name: str, main_branch: str) -> BranchStatus:
        """Get comprehensive status of a branch."""
        try:
            # Quick checks first
            if branch_name == main_branch or self.is_protected_branch(branch_name):
                return BranchStatus.ACTIVE
            
            if self.should_ignore_branch(branch_name):
                return BranchStatus.IGNORED
            
            # Only check merge status if needed
            if self.status_filter in ["merged", "all"]:
                if self.git_service.is_merged(branch_name, main_branch):
                    return BranchStatus.MERGED
            
            # Only check staleness if needed
            if self.status_filter in ["stale", "all"]:
                if self.min_stale_days > 0:
                    age_days = self.git_service.get_branch_age(branch_name)
                    if age_days >= self.min_stale_days:
                        return BranchStatus.STALE
            
            return BranchStatus.ACTIVE
        except Exception as e:
            print(f"Error getting branch status: {e}")
            return BranchStatus.UNKNOWN

    def is_protected_branch(self, branch_name: str) -> bool:
        """Check if a branch is protected."""
        return branch_name in self.protected_branches

    def should_ignore_branch(self, branch_name: str) -> bool:
        """Check if a branch should be ignored based on ignore patterns."""
        return any(fnmatch(branch_name, pattern) for pattern in self.ignore_patterns)

    def should_process_branch(self, branch_name: str, status: BranchStatus) -> bool:
        """Determine if a branch should be processed based on status filter."""
        if self.status_filter == "all":
            return status in [BranchStatus.MERGED, BranchStatus.STALE]
        return status.value == self.status_filter 