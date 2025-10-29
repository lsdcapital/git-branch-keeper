"""Service for determining branch status and related operations"""
from typing import Optional, Dict, Union, TYPE_CHECKING
from fnmatch import fnmatch
from rich.console import Console
from git_branch_keeper.models.branch import BranchStatus, SyncStatus, BranchDetails
from git_branch_keeper.logging_config import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)

class BranchStatusService:
    """Service for determining branch status."""

    def __init__(self, repo_path: str, config: Union['Config', dict], git_service, github_service, verbose: bool = False):
        """Initialize the service."""
        self.repo_path = repo_path
        self.config = config
        self.git_service = git_service
        self.github_service = github_service
        self.verbose = verbose
        self.debug_mode = config.get('debug', False)
        self.protected_branches = config.get('protected_branches', ["main", "master"])
        self.ignore_patterns = config.get('ignore_patterns', [])
        self.min_stale_days = config.get('stale_days', 30)
        self.status_filter = config.get('status_filter', "all")
        self.main_branch = config.get('main_branch', 'main')

    def get_branch_status(self, branch_name: str, main_branch: str, pr_data: Optional[Dict] = None) -> BranchStatus:
        """Get the status of a branch."""
        logger.debug(f"Checking status for branch: {branch_name}")
        
        # Skip protected branches
        if branch_name in self.protected_branches:
            logger.debug(f"Branch {branch_name} is protected, marking as active")
            return BranchStatus.ACTIVE

        # Check PR data if available (only use this during bulk processing)
        if pr_data and branch_name in pr_data:
            pr_info = pr_data[branch_name]
            if pr_info.get('merged', False):
                logger.debug(f"Branch {branch_name} is merged (PR was merged)")
                return BranchStatus.MERGED
            if pr_info.get('closed', False):
                logger.debug(f"Branch {branch_name} had PR closed without merging, marking as active")
                return BranchStatus.ACTIVE
            if pr_info.get('count', 0) > 0:
                logger.debug(f"Branch {branch_name} marked as active (has open PRs)")
                return BranchStatus.ACTIVE

        # Then try to detect merge using Git methods (faster)
        logger.debug(f"Checking if {branch_name} is merged into {main_branch}...")
        if self.git_service.is_branch_merged(branch_name, main_branch):
            logger.debug(f"Branch {branch_name} is merged into {main_branch} (Git)")
            return BranchStatus.MERGED

        # Check branch age last (simplest check)
        age_days = self.git_service.get_branch_age(branch_name)
        logger.debug(f"Branch age: {age_days} days")
        
        if age_days >= self.config.get('stale_days', 30):
            logger.debug(f"Branch {branch_name} marked as stale (age: {age_days} days)")
            return BranchStatus.STALE

        logger.debug(f"Branch {branch_name} marked as active (default)")
        return BranchStatus.ACTIVE

    def is_protected_branch(self, branch_name: str) -> bool:
        """Check if a branch is protected."""
        return branch_name in self.protected_branches

    def should_ignore_branch(self, branch_name: str) -> bool:
        """Check if a branch should be ignored based on ignore patterns."""
        return any(fnmatch(branch_name, pattern) for pattern in self.ignore_patterns)

    def should_process_branch(self, branch_name: str, status: BranchStatus, main_branch: str, pr_data: Optional[Dict] = None) -> tuple[bool, str]:
        """
        Determine if a branch should be processed based on its status and conditions.
        Returns: (should_process: bool, reason: str)
        """
        logger.debug(f"Checking if {branch_name} should be processed:")
        logger.debug(f"  Status: {status.value}")
        logger.debug(f"  Filter: {self.config.get('status_filter', 'all')}")

        # Check main branch status first
        main_sync_status = self.git_service.get_branch_sync_status(main_branch, main_branch)
        if "behind" in main_sync_status:
            if self.verbose:
                logger.debug(f"  Skipping: main branch is {main_sync_status}")
            return False, f"main branch is {main_sync_status} - please update main first"

        # Skip if branch has open PRs
        if pr_data and branch_name in pr_data:
            if pr_data[branch_name]['count'] > 0:
                if self.verbose:
                    logger.debug("  Skipping: has open PR")
                return False, "has open PR"
        elif self.github_service.has_open_pr(branch_name):
            if self.verbose:
                logger.debug("  Skipping: has open PR")
            return False, "has open PR"

        # Skip protected branches
        if branch_name in self.config.get('protected_branches', []):
            if self.verbose:
                logger.debug("  Skipping: is protected branch")
            return False, "is protected branch"

        # Skip if branch matches ignore patterns
        for pattern in self.config.get('ignore_patterns', []):
            if fnmatch(branch_name, pattern):
                if self.verbose:
                    logger.debug(f"  Skipping: matches ignore pattern {pattern}")
                return False, f"matches ignore pattern: {pattern}"

        # Skip if branch is not in sync with remote
        sync_status = self.git_service.get_branch_sync_status(branch_name, main_branch)
        logger.debug(f"  Sync status: {sync_status}")
        logger.debug(f"  Force mode: {self.config.get('force', False)}")
        logger.debug(f"  Is merged: {status == BranchStatus.MERGED}")
        if sync_status == SyncStatus.LOCAL_ONLY.value:
            logger.debug(f"  Local-only branch with merge status: {status}")
            if status == BranchStatus.MERGED:
                logger.debug("  Branch is local-only but marked as merged - will process")

        if not self.config.get('force', False):  # Only check sync status if not force mode
            if sync_status == SyncStatus.LOCAL_ONLY.value and status != BranchStatus.MERGED:
                if self.verbose:
                    logger.debug("  Skipping: exists only locally and is not merged")
                return False, "exists only locally (use --force to clean anyway)"
            elif sync_status not in [SyncStatus.SYNCED.value, SyncStatus.MERGED_GIT.value, SyncStatus.MERGED_PR.value, SyncStatus.LOCAL_ONLY.value, SyncStatus.CLOSED_UNMERGED.value]:
                if "ahead" in sync_status:
                    if self.verbose:
                        logger.debug("  Skipping: has unpushed commits")
                    return False, "has unpushed commits"
                elif "behind" in sync_status:
                    if self.verbose:
                        logger.debug("  Skipping: has unpulled commits")
                    return False, "has unpulled commits"
                elif "diverged" in sync_status:
                    if self.verbose:
                        logger.debug("  Skipping: has diverged from remote")
                    return False, "has diverged from remote"

        # Process based on status filter
        status_filter = self.config.get('status_filter', 'all').lower()
        if status_filter == 'merged':
            if status == BranchStatus.MERGED:
                if self.verbose:
                    logger.debug("  Will process: is merged")
                return True, "is merged"
            if self.verbose:
                logger.debug("  Skipping: not merged")
            return False, "not merged"
        elif status_filter == 'stale':
            if status == BranchStatus.STALE:
                stale_days = self.config.get('stale_days', 30)
                if self.verbose:
                    logger.debug(f"  Will process: is stale (>{stale_days} days old)")
                return True, f"is stale (>{stale_days} days old)"
            if self.verbose:
                logger.debug("  Skipping: not stale")
            return False, "not stale"
        elif status_filter == 'all':
            if status in [BranchStatus.STALE, BranchStatus.MERGED]:
                if self.verbose:
                    logger.debug(f"  Will process: is {status.value}")
                return True, f"is {status.value}"
            if self.verbose:
                logger.debug(f"  Skipping: is {status.value}")
            return False, f"is {status.value}"

        logger.debug(f"  Skipping: unknown status filter: {status_filter}")
        return False, f"unknown status filter: {status_filter}"

    def get_branch_details(self, branch_name: str) -> BranchDetails:
        """Get detailed information about a branch."""
        try:
            # Get last commit date and age
            last_commit_date = self.git_service.get_last_commit_date(branch_name)
            age_days = self.git_service.get_branch_age(branch_name)

            # Get branch status
            status = self.get_branch_status(branch_name, self.main_branch)

            # Get local changes status
            status_details = self.git_service.get_branch_status_details(branch_name)
            has_local_changes = any([
                status_details['modified'],
                status_details['untracked'],
                status_details['staged']
            ])

            # Get sync status
            sync_status = self.git_service.get_branch_sync_status(branch_name, self.main_branch)

            # Initialize PR fields as None - they will be populated later during bulk fetch
            pr_status = None
            notes = None

            return BranchDetails(
                name=branch_name,
                last_commit_date=last_commit_date,
                age_days=age_days,
                status=status,
                has_local_changes=has_local_changes,
                has_remote=self.git_service.has_remote_branch(branch_name),
                sync_status=sync_status,
                pr_status=pr_status,
                notes=notes
            )
        except Exception as e:
            if self.verbose:
                logger.debug(f"Error getting branch details for {branch_name}: {e}")
            return BranchDetails(
                name=branch_name,
                last_commit_date="unknown",
                age_days=0,
                status=BranchStatus.ACTIVE,
                has_local_changes=False,
                has_remote=False,
                sync_status="unknown",
                pr_status=None,
                notes=None
            )