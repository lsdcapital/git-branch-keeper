"""Service for determining branch status and related operations"""

from typing import Optional, Dict, Union, TYPE_CHECKING
from fnmatch import fnmatch
from rich.console import Console
from git_branch_keeper.models.branch import BranchStatus
from git_branch_keeper.logging_config import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)


class BranchStatusService:
    """Service for determining branch status."""

    def __init__(
        self,
        repo_path: str,
        config: Union["Config", dict],
        git_service,
        github_service,
        verbose: bool = False,
    ):
        """Initialize the service."""
        self.repo_path = repo_path
        self.config = config
        self.git_service = git_service
        self.github_service = github_service
        self.verbose = verbose
        self.debug_mode = config.get("debug", False)
        self.protected_branches = config.get("protected_branches", ["main", "master"])
        self.ignore_patterns = config.get("ignore_patterns", [])
        self.min_stale_days = config.get("stale_days", 30)
        self.status_filter = config.get("status_filter", "all")
        self.main_branch = config.get("main_branch", "main")

    def get_branch_status(
        self, branch_name: str, main_branch: str, pr_data: Optional[Dict] = None
    ) -> BranchStatus:
        """Get the status of a branch."""
        logger.debug(f"Checking status for branch: {branch_name}")

        # Main branch is always active (cannot be merged into itself)
        if branch_name == main_branch:
            logger.debug(f"Branch {branch_name} is the main branch, marking as active")
            return BranchStatus.ACTIVE

        # Skip protected branches
        if branch_name in self.protected_branches:
            logger.debug(f"Branch {branch_name} is protected, marking as active")
            return BranchStatus.ACTIVE

        # Check PR data if available (only use this during bulk processing)
        if pr_data and branch_name in pr_data:
            pr_info = pr_data[branch_name]
            if pr_info.get("merged", False):
                logger.debug(f"Branch {branch_name} is merged (PR was merged)")
                return BranchStatus.MERGED
            if pr_info.get("closed", False):
                logger.debug(
                    f"Branch {branch_name} had PR closed without merging, marking as active"
                )
                return BranchStatus.ACTIVE
            if pr_info.get("count", 0) > 0:
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

        if age_days >= self.config.get("stale_days", 30):
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
