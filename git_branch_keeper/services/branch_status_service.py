"""Service for determining branch status and related operations"""
from typing import List, Optional
from fnmatch import fnmatch
from rich.console import Console
from git_branch_keeper.models.branch import BranchStatus, SyncStatus, BranchDetails

console = Console()

class BranchStatusService:
    """Service for determining branch status."""

    def __init__(self, repo, config: dict, git_service, github_service, verbose: bool = False):
        """Initialize the service."""
        self.repo = repo
        self.config = config
        self.git_service = git_service
        self.github_service = github_service
        self.verbose = verbose
        self.protected_branches = config.get('protected_branches', ["main", "master"])
        self.ignore_patterns = config.get('ignore_patterns', [])
        self.min_stale_days = config.get('stale_days', 30)
        self.status_filter = config.get('status_filter', "all")

    def debug(self, message: str):
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            console.print(f"[Branch Status] {message}")

    def get_branch_status(self, branch_name: str, main_branch: str) -> BranchStatus:
        """
        Determine the status of a branch.
        Returns: BranchStatus (ACTIVE, STALE, MERGED)
        """
        try:
            # Check if branch is protected
            if branch_name in self.config['protected_branches']:
                return BranchStatus.ACTIVE

            # First check GitHub PR status if enabled
            if self.github_service.github_enabled:
                open_prs, was_merged = self.github_service.get_pr_status(branch_name)
                if was_merged:
                    if self.verbose:
                        self.debug(f"Branch {branch_name} marked as merged (has merged PR)")
                    return BranchStatus.MERGED

            # Always check git merge status, regardless of GitHub status
            try:
                is_merged = self.git_service.is_merged_to_main(branch_name, main_branch)
                if is_merged:
                    if self.verbose:
                        self.debug(f"Branch {branch_name} marked as merged (merged to main)")
                    return BranchStatus.MERGED
            except Exception as e:
                if self.verbose:
                    self.debug(f"Error checking merge status for {branch_name}: {e}")

            # Get branch age
            age_days = self.git_service.get_branch_age(branch_name)

            # If branch has open PRs, consider it active regardless of age
            if self.github_service.github_enabled and self.github_service.get_pr_count(branch_name) > 0:
                if self.verbose:
                    self.debug(f"Branch {branch_name} marked as active (has open PRs)")
                return BranchStatus.ACTIVE

            # Check if branch is stale based on age
            if age_days >= self.config['stale_days']:
                if self.verbose:
                    self.debug(f"Branch {branch_name} marked as stale ({age_days} days old)")
                return BranchStatus.STALE

            # Default to active
            return BranchStatus.ACTIVE

        except Exception as e:
            if self.verbose:
                self.debug(f"Error getting status for {branch_name}: {e}")
            return BranchStatus.ACTIVE

    def is_protected_branch(self, branch_name: str) -> bool:
        """Check if a branch is protected."""
        return branch_name in self.protected_branches

    def should_ignore_branch(self, branch_name: str) -> bool:
        """Check if a branch should be ignored based on ignore patterns."""
        return any(fnmatch(branch_name, pattern) for pattern in self.ignore_patterns)

    def should_process_branch(self, branch_name: str, status: BranchStatus, main_branch: str) -> tuple[bool, str]:
        """
        Determine if a branch should be processed based on its status and conditions.
        Returns: (should_process: bool, reason: str)
        """
        if self.verbose:
            self.debug(f"Checking if {branch_name} should be processed:")
            self.debug(f"  Status: {status.value}")
            self.debug(f"  Filter: {self.config['status_filter']}")

        # Check main branch status first
        main_sync_status = self.git_service.get_branch_sync_status(main_branch, main_branch)
        if "behind" in main_sync_status:
            if self.verbose:
                self.debug(f"  Skipping: main branch is {main_sync_status}")
            return False, f"main branch is {main_sync_status} - please update main first"

        # Skip if branch has open PRs
        if self.github_service.has_open_pr(branch_name):
            if self.verbose:
                self.debug("  Skipping: has open PR")
            return False, "has open PR"

        # Skip protected branches
        if branch_name in self.config['protected_branches']:
            if self.verbose:
                self.debug("  Skipping: is protected branch")
            return False, "is protected branch"

        # Skip if branch matches ignore patterns
        for pattern in self.config['ignore_patterns']:
            if fnmatch(branch_name, pattern):
                if self.verbose:
                    self.debug(f"  Skipping: matches ignore pattern {pattern}")
                return False, f"matches ignore pattern: {pattern}"

        # Skip if branch is not in sync with remote
        sync_status = self.git_service.get_branch_sync_status(branch_name, main_branch)
        if self.verbose:
            self.debug(f"  Sync status: {sync_status}")
            self.debug(f"  Force mode: {self.config.get('force', False)}")

        if not self.config.get('force', False):  # Only check sync status if not force mode
            if sync_status == "local-only":
                if self.verbose:
                    self.debug("  Skipping: exists only locally")
                return False, "exists only locally (use --force to clean anyway)"
            elif sync_status != "synced" and sync_status not in ["merged-git", "merged-pr"]:
                if "ahead" in sync_status:
                    if self.verbose:
                        self.debug("  Skipping: has unpushed commits")
                    return False, "has unpushed commits"
                elif "behind" in sync_status:
                    if self.verbose:
                        self.debug("  Skipping: has unpulled commits")
                    return False, "has unpulled commits"
                elif "diverged" in sync_status:
                    if self.verbose:
                        self.debug("  Skipping: has diverged from remote")
                    return False, "has diverged from remote"

        # Process based on status filter
        status_filter = self.config['status_filter'].lower()
        if status_filter == 'merged':
            if status == BranchStatus.MERGED:
                if self.verbose:
                    self.debug("  Will process: is merged")
                return True, "is merged"
            if self.verbose:
                self.debug("  Skipping: not merged")
            return False, "not merged"
        elif status_filter == 'stale':
            if status == BranchStatus.STALE:
                if self.verbose:
                    self.debug(f"  Will process: is stale (>{self.config['stale_days']} days old)")
                return True, f"is stale (>{self.config['stale_days']} days old)"
            if self.verbose:
                self.debug("  Skipping: not stale")
            return False, "not stale"
        elif status_filter == 'all':
            if status in [BranchStatus.STALE, BranchStatus.MERGED]:
                if self.verbose:
                    self.debug(f"  Will process: is {status.value}")
                return True, f"is {status.value}"
            if self.verbose:
                self.debug(f"  Skipping: is {status.value}")
            return False, f"is {status.value}"

        if self.verbose:
            self.debug(f"  Skipping: unknown status filter: {status_filter}")
        return False, f"unknown status filter: {status_filter}"

    def get_branch_details(self, branch_name: str) -> BranchDetails:
        """Get detailed information about a branch."""
        try:
            # Get last commit date and age
            last_commit_date = self.git_service.get_last_commit_date(branch_name)
            age_days = self.git_service.get_branch_age(branch_name)

            # Get branch status
            status = self.get_branch_status(branch_name, self.git_service.repo.active_branch.name)

            # Get local changes status
            status_details = self.git_service.get_branch_status_details(branch_name)
            has_local_changes = any([
                status_details['modified'],
                status_details['untracked'],
                status_details['staged']
            ])

            # Get sync status
            sync_status = self.git_service.get_branch_sync_status(branch_name, self.git_service.repo.active_branch.name)

            # Get PR status if GitHub is enabled
            pr_status = None
            if self.github_service.github_enabled:
                pr_count = self.github_service.get_pr_count(branch_name)
                pr_status = str(pr_count) if pr_count > 0 else None

            return BranchDetails(
                name=branch_name,
                last_commit_date=last_commit_date,
                age_days=age_days,
                status=status,
                has_local_changes=has_local_changes,
                has_remote=self.git_service.has_remote_branch(branch_name),
                sync_status=sync_status,
                pr_status=pr_status
            )
        except Exception as e:
            if self.verbose:
                self.debug(f"Error getting branch details for {branch_name}: {e}")
            return BranchDetails(
                name=branch_name,
                last_commit_date="unknown",
                age_days=0,
                status=BranchStatus.ACTIVE,
                has_local_changes=False,
                has_remote=False,
                sync_status="unknown",
                pr_status=None
            )