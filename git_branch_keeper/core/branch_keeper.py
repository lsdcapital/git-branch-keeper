"""Core functionality for git-branch-keeper"""

import signal
import sys
from contextlib import nullcontext
from typing import Dict, Optional, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

import git
from rich.console import Console
from rich.progress import Progress

from git_branch_keeper.models.branch import BranchStatus, SyncStatus, BranchDetails
from git_branch_keeper.services.git import GitHubService, GitOperations
from git_branch_keeper.services.display_service import DisplayService
from git_branch_keeper.services.branch_status_service import BranchStatusService
from git_branch_keeper.services.cache_service import CacheService
from git_branch_keeper.services.branch_validation_service import BranchValidationService
from git_branch_keeper.utils.threading import get_optimal_worker_count
from git_branch_keeper.utils.logging import get_logger
from git_branch_keeper.config import Config
from git_branch_keeper.formatters import format_deletion_confirmation_items, format_deletion_reason

console = Console()
logger = get_logger(__name__)

# Module-level reference to the active BranchKeeper instance for signal handling
_active_keeper: Optional["BranchKeeper"] = None


def _signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    if signum == signal.SIGINT:
        print()  # New line after ^C
        if _active_keeper and _active_keeper.git_service.in_git_operation:
            console.print(
                "\n[yellow]Interrupted! Waiting for current Git operation to complete...[/yellow]"
            )
        else:
            console.print("\n[yellow]Interrupted! Cleaning up...[/yellow]")
        sys.exit(1)


# Set up signal handlers
signal.signal(signal.SIGINT, _signal_handler)


class BranchKeeper:
    """Main class for managing Git branches."""

    def __init__(self, repo_path: str, config: Union[Config, dict], tui_mode: bool = False):
        """Initialize BranchKeeper.

        Args:
            repo_path: Path to git repository
            config: Configuration dict or Config object
            tui_mode: If True, suppresses Rich console output (for TUI mode)
        """
        self.repo_path = repo_path
        self.tui_mode = tui_mode
        # Convert dict to Config if needed (backward compatibility)
        if isinstance(config, dict):
            self.config = Config.from_dict(config)
        else:
            self.config = config
        self.verbose = config.get("verbose", False)
        self.debug_mode = config.get("debug", False)

        # Initialize repo first
        try:
            self.repo = git.Repo(self.repo_path)
        except Exception as e:
            raise Exception(f"Error initializing repository: {e}")

        # Get configuration values
        self.min_stale_days = config.get("stale_days", 30)
        self.protected_branches = config.get("protected_branches", ["main", "master"])
        self.ignore_patterns = config.get("ignore_patterns", [])
        self.status_filter = config.get("status_filter", "all")
        self.interactive = config.get("interactive", True)
        self.dry_run = config.get("dry_run", True)
        self.force_mode = config.get("force", False)
        self.main_branch = config.get("main_branch", "main")

        # Validate GitHub token requirement BEFORE initializing services
        # Check for GitHub integration (optional)
        remote_url = None
        is_github_repo = False
        has_github_token = False

        try:
            remote_url = self.repo.remotes.origin.url
            is_github_repo = "github.com" in remote_url

            if is_github_repo:
                # Check if token exists
                import os

                github_token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
                has_github_token = bool(github_token)

                if not has_github_token:
                    # GitHub repo without token - inform user about limited functionality
                    logger.info(
                        "GitHub token not found. Running in local-only mode.\n"
                        "  • Branch analysis will work normally\n"
                        "  • PR detection and protection: DISABLED\n"
                        "  • To enable: Set GITHUB_TOKEN environment variable or add to config\n"
                        "  • Get token at: https://github.com/settings/tokens"
                    )
                    if not self.tui_mode:
                        self._console_print(
                            "[yellow]ℹ GitHub token not found - PR detection disabled[/yellow]"
                        )
            else:
                # Non-GitHub repo (GitLab, Bitbucket, local, etc.)
                logger.info(
                    f"Non-GitHub repository detected ({remote_url}). PR detection disabled."
                )
                if not self.tui_mode:
                    self._console_print(
                        "[blue]ℹ Non-GitHub repository - PR detection disabled[/blue]"
                    )

        except AttributeError:
            # No remote named 'origin' - local-only repo
            logger.info("No origin remote found. Running in local-only mode.")
            if not self.tui_mode:
                self._console_print("[blue]ℹ Local repository - no remote tracking[/blue]")

        # Initialize services
        self.github_service = GitHubService(self.repo_path, self.config)
        self.git_service = GitOperations(self.repo_path, self.config)

        # Setup GitHub integration (only if available)
        if is_github_repo and has_github_token and remote_url:
            try:
                logger.debug(f"Setting up GitHub API with remote: {remote_url}")
                self.github_service.setup_github_api(remote_url)
                logger.info("[GitHub] Integration enabled - PR detection active")
            except Exception as e:
                logger.debug(f"Failed to setup GitHub API: {e}")
                logger.warning("[GitHub] Setup failed - PR detection disabled")
        else:
            logger.debug("[GitHub] Integration disabled (no token or non-GitHub repo)")

        self.branch_status_service = BranchStatusService(
            self.repo_path, self.config, self.git_service, self.github_service, self.verbose
        )
        self.display_service = DisplayService(verbose=self.verbose, debug=self.debug_mode)
        self.cache_service = CacheService(self.repo_path)

        # Initialize statistics
        self.stats = {"deleted": 0, "skipped_pr": 0, "skipped_protected": 0, "skipped_pattern": 0}

        # Set as active keeper for signal handling
        global _active_keeper
        _active_keeper = self

    def _console_print(self, *args, **kwargs):
        """Print to console only when not in TUI mode.

        Args:
            *args: Arguments to pass to console.print()
            **kwargs: Keyword arguments to pass to console.print()
        """
        if not self.tui_mode:
            console.print(*args, **kwargs)

    def delete_branch(
        self, branch_name: str, reason: str, force_mode: bool = False
    ) -> tuple[bool, Optional[str]]:
        """Delete a branch or show what would be deleted in dry-run mode.

        Args:
            branch_name: Name of the branch to delete
            reason: Reason for deletion (merged/stale)
            force_mode: If True, skip uncommitted changes check

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        try:
            # Check for open PRs first
            if self.github_service.has_open_pr(branch_name):
                error_msg = "Has open pull request"
                self._console_print(f"[yellow]Skipping {branch_name} - {error_msg}[/yellow]")
                self.stats["skipped_pr"] += 1
                return False, error_msg

            # Cannot delete current branch - check BEFORE dry_run
            try:
                current_branch = self.repo.active_branch.name
                if branch_name == current_branch:
                    error_msg = "Cannot delete current branch"
                    self._console_print(f"[yellow]{error_msg}: {branch_name}[/yellow]")
                    return False, error_msg
            except TypeError:
                # Detached HEAD state - no active branch, so we can delete any branch
                pass

            # Check for local changes
            status_details = self.git_service.get_branch_status_details(branch_name)

            # Check if branch is in a worktree (can't delete while in worktree - even with force)
            if status_details.get("in_worktree"):
                error_msg = "Branch is checked out in a worktree"
                self._console_print(f"[yellow]Cannot delete {branch_name} - {error_msg}[/yellow]")
                return False, error_msg

            # Check if there was an error checking status
            error = status_details.get("error")
            if error:
                error_msg = str(error)
                self._console_print(
                    f"[yellow]Cannot verify {branch_name} status: {error_msg}[/yellow]"
                )
                return False, error_msg

            # Check for uncommitted changes (skip if force_mode is enabled)
            if not force_mode and (
                status_details.get("modified")
                or status_details.get("untracked")
                or status_details.get("staged")
            ):
                warning = []
                if status_details.get("modified"):
                    warning.append("modified files")
                if status_details.get("untracked"):
                    warning.append("untracked files")
                if status_details.get("staged"):
                    warning.append("staged files")

                # Show a cleaner warning
                change_indicators = []
                if status_details.get("modified"):
                    change_indicators.append("M")
                if status_details.get("untracked"):
                    change_indicators.append("U")
                if status_details.get("staged"):
                    change_indicators.append("S")

                error_msg = f"Has uncommitted changes ({'/'.join(change_indicators)})"
                self._console_print(
                    f"[yellow]⚠️  {branch_name} has uncommitted changes when checked out: {'/'.join(change_indicators)}[/yellow]"
                )
                self._console_print(
                    "[dim]   This might indicate files that are ignored differently between branches[/dim]"
                )

                if self.interactive and not self.tui_mode:
                    response = input(f"   Still want to delete branch {branch_name}? [y/N] ")
                    if response.lower() != "y":
                        return False, error_msg
                else:
                    self._console_print("   Skipping due to uncommitted changes")
                    return False, error_msg

            remote_exists = self.git_service.has_remote_branch(branch_name)
            if self.dry_run:
                if remote_exists:
                    self._console_print(
                        f"Would delete local and remote branch {branch_name} ({reason})"
                    )
                else:
                    self._console_print(f"Would delete local branch {branch_name} ({reason})")
                return True, None

            # Delete the branch
            success = self.git_service.delete_branch(branch_name, self.dry_run)

            # If deletion was successful, remove from cache
            if success:
                self.cache_service.remove_branch_from_cache(branch_name)
                logger.debug(f"Removed {branch_name} from cache after deletion")
                return True, None
            else:
                return False, "Git deletion failed (may be protected remotely)"

        except Exception as e:
            error_msg = str(e)
            self._console_print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
            return False, error_msg

    def _create_worktree_entry(self, worktree_info, parent_branch: BranchDetails) -> BranchDetails:
        """Create a BranchDetails entry representing a worktree.

        Args:
            worktree_info: WorktreeInfo object with worktree data
            parent_branch: The BranchDetails of the branch this worktree is based on

        Returns:
            BranchDetails object representing the worktree
        """
        # Check worktree file status (same as branches)
        status_details = self.git_service.worktree_service.get_worktree_status_details(
            worktree_info.path
        )

        # If empty dict (orphaned or error), set to None
        modified_files = status_details.get("modified") if status_details else None
        untracked_files = status_details.get("untracked") if status_details else None
        staged_files = status_details.get("staged") if status_details else None

        # Reuse parent branch data but mark as worktree
        return BranchDetails(
            name=parent_branch.name,
            last_commit_date=parent_branch.last_commit_date,
            age_days=parent_branch.age_days,
            status=parent_branch.status,
            modified_files=modified_files,
            untracked_files=untracked_files,
            staged_files=staged_files,
            has_remote=parent_branch.has_remote,
            sync_status=parent_branch.sync_status,
            pr_status=parent_branch.pr_status,
            notes=f"{'[ORPHANED] ' if worktree_info.is_orphaned else ''}{worktree_info.path}",
            in_worktree=False,  # This IS the worktree, not "in" a worktree
            is_worktree=True,
            worktree_path=worktree_info.path,
        )

    def _insert_worktree_entries(self, branch_details: list) -> list:
        """Insert worktree entries after their parent branches.

        Args:
            branch_details: List of BranchDetails objects

        Returns:
            New list with worktree entries inserted
        """
        # Get all worktrees
        worktree_infos = self.git_service.worktree_service.get_worktree_info()

        # Skip main worktree
        worktree_infos = [wt for wt in worktree_infos if not wt.is_main]

        if not worktree_infos:
            return branch_details

        # Build new list with worktrees inserted
        result = []
        for branch in branch_details:
            # Find worktrees for this branch
            branch_worktrees = [wt for wt in worktree_infos if wt.branch_name == branch.name]

            # Check if any worktree is orphaned and update parent branch
            if any(wt.is_orphaned for wt in branch_worktrees):
                branch.worktree_is_orphaned = True

            result.append(branch)

            # Add worktree entries after this branch
            for wt in branch_worktrees:
                worktree_entry = self._create_worktree_entry(wt, branch)
                result.append(worktree_entry)

        return result

    def process_branches(self, cleanup_enabled: bool = False) -> None:
        """Process all branches according to configuration."""
        try:
            # Check main branch status first
            if not self._check_main_branch_status():
                return

            # Get and filter branches
            branches = self._get_filtered_branches()
            if not branches:
                self._console_print("No branches to process")
                return

            # Check if we should use cache
            use_cache = not self.config.get("refresh", False)

            # Get cached branches and determine which need refresh
            cached_branches = {}
            branches_to_process = branches

            if use_cache:
                # Load all cached branches
                cached_branches = self.cache_service.get_cached_branches(branches)
                logger.debug(f"Loaded {len(cached_branches)} cached branches")

                # Determine which branches need refresh (unstable or not cached)
                branches_to_process = self.cache_service.get_stale_branches(
                    branches, self.main_branch
                )

                stable_count = len(branches) - len(branches_to_process)
                if self.verbose or self.debug_mode:
                    if stable_count > 0:
                        self._console_print(
                            f"[dim]Using {stable_count} stable cached branches, refreshing {len(branches_to_process)} branches[/dim]"
                        )
            else:
                # With --refresh, process all branches
                if self.verbose or self.debug_mode:
                    self._console_print(
                        f"[dim]Refreshing all {len(branches)} branches (--refresh mode)[/dim]"
                    )

            # Process branches that need refresh
            refreshed_details = self._collect_branch_details(
                branches_to_process, show_progress=True
            )

            # Merge cached stable branches with refreshed data
            branch_details = []
            refreshed_names = {b.name for b in refreshed_details}

            # Add refreshed branches
            branch_details.extend(refreshed_details)

            # Add stable cached branches that weren't refreshed
            for branch_name, cached_branch in cached_branches.items():
                if branch_name not in refreshed_names:
                    branch_details.append(cached_branch)

            # Sort all branches (both cached and newly processed)
            branch_details = self.sort_branches(branch_details)

            # Insert worktree entries after their parent branches
            branch_details = self._insert_worktree_entries(branch_details)

            # Save cache with new data (always save, regardless of whether we used cache to load)
            self.cache_service.save_cache(branch_details, self.main_branch)

            # Display and optionally cleanup
            self._display_and_cleanup(branch_details, cleanup_enabled)

        except Exception as e:
            self._console_print(f"[red]Error processing branches: {e}[/red]")

    def get_cached_branches_fast(self) -> tuple[list, list]:
        """Quickly load cached branch data without processing.

        This method is optimized for fast initial loading in TUI mode.
        It returns all cached branches immediately and identifies which need refresh.

        Returns:
            Tuple of (cached_branch_details, branches_to_process):
            - cached_branch_details: List of all BranchDetails objects from cache
            - branches_to_process: List of branch names that need refresh (unstable/not cached)
        """
        try:
            # Check if we should use cache
            use_cache = not self.config.get("refresh", False)
            if not use_cache:
                # If refresh is requested, return empty cache and all branches
                branches = self._get_filtered_branches()
                return [], branches

            # Get filtered branches
            branches = self._get_filtered_branches()
            if not branches:
                return [], []

            # Get all cached branches
            cached_branches = self.cache_service.get_cached_branches(branches)
            logger.debug(f"Fast-loaded {len(cached_branches)} cached branches")

            # Determine which branches need refresh
            branches_to_process = self.cache_service.get_stale_branches(branches, self.main_branch)

            # Convert cached branches dict to list and sort
            cached_branch_details = list(cached_branches.values())
            cached_branch_details = self.sort_branches(cached_branch_details)

            return cached_branch_details, branches_to_process

        except Exception as e:
            logger.debug(f"Error fast-loading cached branches: {e}")
            # On error, return empty cache and all branches for processing
            try:
                branches = self._get_filtered_branches()
                return [], branches
            except Exception:
                return [], []

    def get_branch_details(self, show_progress: bool = True) -> list:
        """Get branch details for interactive TUI mode.

        Args:
            show_progress: Whether to show Rich Progress bars (default True for CLI, False for TUI)

        Returns:
            List of BranchDetails objects
        """
        try:
            # Check main branch status first
            if not self._check_main_branch_status():
                return []

            # Get and filter branches
            branches = self._get_filtered_branches()
            if not branches:
                return []

            # Check if we should use cache
            use_cache = not self.config.get("refresh", False)

            # Get cached branches (only immutable ones)
            cached_branches = {}
            if use_cache:
                cached_branches = self.cache_service.get_cached_branches(branches)
                logger.debug(f"Using {len(cached_branches)} cached branches")

            # Separate branches into cached vs needs processing
            branches_to_process = [b for b in branches if b not in cached_branches]

            if self.verbose or self.debug_mode:
                if cached_branches:
                    self._console_print(
                        f"[dim]Using {len(cached_branches)} cached branches, checking {len(branches_to_process)} branches[/dim]"
                    )

            # Process non-cached branches
            branch_details = self._collect_branch_details(
                branches_to_process, show_progress=show_progress
            )

            # Add cached branches to results
            branch_details.extend(cached_branches.values())

            # Update in_worktree status for ALL branches (including cached)
            # Worktree status is dynamic and not cached
            worktree_branches = self.git_service.worktree_service.get_worktree_branches()
            logger.debug(f"Worktree branches detected: {worktree_branches}")

            try:
                current_branch = self.repo.active_branch.name
            except TypeError:
                current_branch = None

            for branch in branch_details:
                # Check if this is the current branch
                is_current = (branch.name == current_branch) if current_branch else False

                # Set in_worktree flag (but not for current branch)
                if branch.name in worktree_branches and not is_current:
                    branch.in_worktree = True
                    logger.debug(f"Setting in_worktree=True for {branch.name}")
                else:
                    branch.in_worktree = False

            # Sort all branches (both cached and newly processed)
            branch_details = self.sort_branches(branch_details)

            # Insert worktree entries after their parent branches
            branch_details = self._insert_worktree_entries(branch_details)

            # Save cache with new data (always save, regardless of whether we used cache to load)
            self.cache_service.save_cache(branch_details, self.main_branch)

            return branch_details

        except Exception as e:
            self._console_print(f"[red]Error getting branch details: {e}[/red]")
            return []

    def _check_main_branch_status(self) -> bool:
        """Check if main branch is up to date. Returns False if behind."""
        main_sync_status = self.git_service.get_branch_sync_status(
            self.main_branch, self.main_branch
        )
        if "behind" in main_sync_status:
            self._console_print(
                f"[yellow]Warning: Your {self.main_branch} branch is {main_sync_status}[/yellow]"
            )
            self._console_print(
                f"[yellow]Please update your {self.main_branch} branch first:[/yellow]"
            )
            self._console_print(f"  git checkout {self.main_branch}")
            self._console_print(f"  git pull origin {self.main_branch}")
            self._console_print("")
        return True

    def _get_filtered_branches(self) -> list:
        """Get all branches excluding tags, stash, remote refs, and ignored patterns."""
        branches = [
            ref.name
            for ref in self.repo.refs
            if not ref.name.startswith("origin/")
            and not ref.name.startswith("refs/stash")
            and not self.git_service.is_tag(ref.name)
        ]

        # Only filter out ignored branches, keep protected ones
        return [b for b in branches if not self.branch_status_service.should_ignore_branch(b)]

    def sort_branches(self, branch_details: list) -> list:
        """Sort branches according to configuration with protected branches always first."""
        sort_by = self.config.get("sort_by", "age")
        sort_order = self.config.get("sort_order", "asc")
        reverse = sort_order == "desc"

        def date_to_int(date_str: str) -> int:
            """Convert date string to integer for sorting. Returns 0 for invalid dates."""
            try:
                return int(date_str.replace("-", ""))
            except (ValueError, AttributeError):
                return 0  # Invalid dates sort to beginning

        if sort_by == "name":
            # Sort: protected first, then alphabetically by branch name
            branch_details.sort(
                key=lambda b: (
                    0 if b.name in self.protected_branches else 1,
                    b.name.lower() if not reverse else chr(255) + b.name.lower(),
                ),
                reverse=reverse if reverse else False,
            )
        elif sort_by == "age":
            # Sort: protected first, then by age, then newest first within same age
            branch_details.sort(
                key=lambda b: (
                    0 if b.name in self.protected_branches else 1,
                    b.age_days if not reverse else -b.age_days,
                    -date_to_int(b.last_commit_date),  # Negative for newest-first
                )
            )
        elif sort_by == "date":
            # Sort: protected first, then by date, then alphabetically within same date
            branch_details.sort(
                key=lambda b: (
                    0 if b.name in self.protected_branches else 1,
                    b.last_commit_date if not reverse else chr(255) + b.last_commit_date,
                    b.name.lower(),
                ),
                reverse=reverse if reverse else False,
            )
        elif sort_by == "status":
            # Sort: protected first, then by status, then by age, then newest first
            status_order = {BranchStatus.ACTIVE: 0, BranchStatus.STALE: 1, BranchStatus.MERGED: 2}
            branch_details.sort(
                key=lambda b: (
                    0 if b.name in self.protected_branches else 1,
                    (
                        status_order.get(b.status, 99)
                        if not reverse
                        else -status_order.get(b.status, 99)
                    ),
                    b.age_days if not reverse else -b.age_days,
                    -date_to_int(b.last_commit_date),
                )
            )

        return branch_details

    def _collect_branch_details(self, branches: list, show_progress: bool = True) -> list:
        """Process branches and collect their details with unified progress tracking.

        Args:
            branches: List of branch names to process
            show_progress: Whether to show Rich Progress bars (default True for CLI, False for TUI)
        """
        if not branches:
            return []

        branch_details = []
        status_filter = self.config.get("status_filter", "all")
        sequential = self.config.get("sequential", False)
        pr_data: Dict[str, Dict] = {}

        # Capture current branch file status BEFORE stashing
        # (stashing will hide these changes from git status checks)
        current_branch_status = None
        try:
            current_branch = self.repo.active_branch.name
            logger.debug(
                f"Capturing file status for current branch {current_branch} before stashing"
            )
            current_branch_status = self.git_service.get_branch_status_details(current_branch)
            logger.debug(
                f"Current branch status: modified={current_branch_status.get('modified')}, "
                f"untracked={current_branch_status.get('untracked')}, "
                f"staged={current_branch_status.get('staged')}"
            )
        except (TypeError, AttributeError):
            # Detached HEAD or other error
            current_branch = None
            logger.debug("No current branch (detached HEAD?), skipping pre-stash status check")

        # Stash uncommitted changes before checking branch status
        was_stashed = False
        try:
            was_stashed = self.git_service.stash_changes()
            if was_stashed:
                logger.debug("Stashed uncommitted changes to check branch status")
        except Exception as e:
            logger.warning(f"Could not stash changes: {e}")
            # Continue anyway - we'll handle errors during branch status checks

        try:
            # Verbose mode: show simple output
            if self.verbose or self.debug_mode:
                self._console_print("Processing branches...")

                # Fetch PR data
                pr_data = self._fetch_pr_data_with_feedback(branches)

                # Process branches sequentially in verbose mode for readable logs
                for branch_name in branches:
                    details = self._process_single_branch(
                        branch_name,
                        status_filter,
                        pr_data,
                        None,
                        current_branch,
                        current_branch_status,
                    )
                    if details:
                        branch_details.append(details)
            else:
                # Non-verbose mode: show spinner for PR fetch, then progress bar for processing

                # Phase 1: Fetch PR data with optional spinner (only if there are branches to check)
                if branches:
                    status_context = (
                        console.status("[bold blue]Fetching PR data from GitHub...", spinner="dots")
                        if show_progress
                        else nullcontext()
                    )
                    with status_context:
                        try:
                            logger.debug(f"Fetching PR data for {len(branches)} branches")
                            pr_data = self.github_service.get_bulk_pr_data(branches)
                        except Exception as e:
                            logger.debug(f"Failed to fetch PR data: {e}")

                # Phase 2: Process branches with optional progress bar
                # Use Progress context if show_progress=True, otherwise use nullcontext
                progress_context = Progress() if show_progress else nullcontext()

                with progress_context as progress:
                    # Determine worker count for progress message
                    if sequential:
                        task_desc = "Processing branches..."
                    else:
                        max_workers = get_optimal_worker_count(self.config.get("workers"))
                        task_desc = f"Processing branches ({max_workers} workers)..."

                    # Only create task if we have a real Progress object
                    task = (
                        progress.add_task(task_desc, total=len(branches))
                        if progress is not None
                        else None
                    )

                    if sequential:
                        # Sequential processing
                        branch_details = self._process_branches_sequential(
                            branches,
                            status_filter,
                            pr_data,
                            progress if show_progress else None,
                            task,
                            current_branch,
                            current_branch_status,
                        )
                    else:
                        # Parallel processing
                        branch_details = self._process_branches_parallel(
                            branches,
                            status_filter,
                            pr_data,
                            progress if show_progress else None,
                            task,
                            current_branch,
                            current_branch_status,
                        )

            # Sort branches according to configuration
            branch_details = self.sort_branches(branch_details)

            return branch_details
        finally:
            # Always restore stashed changes
            try:
                self.git_service.restore_stashed_changes(was_stashed)
                if was_stashed:
                    logger.debug("Restored stashed changes")
            except Exception as e:
                logger.error(f"Failed to restore stashed changes: {e}")
                self._console_print(
                    "[red]Warning: Could not restore stashed changes. Run 'git stash pop' manually.[/red]"
                )

    def _fetch_pr_data_with_feedback(self, branches: list) -> Dict[str, Dict]:
        """Fetch PR data with user feedback."""
        pr_data: Dict[str, Dict] = {}
        try:
            self._console_print("[dim]Fetching PR data from GitHub...[/dim]")
            logger.debug(f"Fetching PR data for {len(branches)} branches")
            pr_data = self.github_service.get_bulk_pr_data(branches)
        except Exception as e:
            logger.debug(f"Failed to fetch PR data: {e}")
        return pr_data

    def _process_branches_sequential(
        self,
        branches: list,
        status_filter: str,
        pr_data: Dict,
        progress,
        task,
        current_branch_name: Optional[str] = None,
        current_branch_status: Optional[dict] = None,
    ) -> list:
        """Process branches sequentially."""
        branch_details = []
        for branch_name in branches:
            details = self._process_single_branch(
                branch_name,
                status_filter,
                pr_data,
                None,
                current_branch_name,
                current_branch_status,
            )
            if details:
                branch_details.append(details)
            if progress:  # Only update if progress bar exists
                progress.update(task, advance=1)
        return branch_details

    def _process_branches_parallel(
        self,
        branches: list,
        status_filter: str,
        pr_data: Dict,
        progress,
        task,
        current_branch_name: Optional[str] = None,
        current_branch_status: Optional[dict] = None,
    ) -> list:
        """Process branches in parallel using ThreadPoolExecutor."""
        branch_details = []

        # Get optimal worker count (already shown in progress bar)
        max_workers = get_optimal_worker_count(self.config.get("workers"))
        logger.debug(f"Using {max_workers} workers for parallel processing")

        # Submit all branch processing tasks
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_branch = {
                executor.submit(
                    self._process_single_branch,
                    branch,
                    status_filter,
                    pr_data,
                    None,
                    current_branch_name,
                    current_branch_status,
                ): branch
                for branch in branches
            }

            # Collect results as they complete
            for future in as_completed(future_to_branch):
                branch_name = future_to_branch[future]
                try:
                    details = future.result()
                    if details:
                        branch_details.append(details)
                except Exception as e:
                    logger.error(f"Error processing branch {branch_name}: {e}")
                    if self.debug_mode and not self.tui_mode:
                        console.print_exception()
                finally:
                    if progress:  # Only update if progress bar exists
                        progress.update(task, advance=1)

        return branch_details

    def _get_github_base_url(self) -> Optional[str]:
        """Extract GitHub base URL from remote URL."""
        try:
            remote_url = self.repo.remotes.origin.url
            if "github.com" not in remote_url:
                return None

            if remote_url.startswith("git@"):
                org_repo = remote_url.split(":")[1].replace(".git", "")
                return f"https://github.com/{org_repo}"
            else:
                return remote_url.replace(".git", "")
        except Exception:
            return None

    def _display_and_cleanup(self, branch_details: list, cleanup_enabled: bool) -> None:
        """Display branch details and optionally perform cleanup."""
        if not branch_details:
            self._console_print("No branches match the filter criteria")
            return

        # Display results
        github_base_url = self._get_github_base_url()
        self.display_service.display_branch_table(
            branch_details,
            self.repo,
            github_base_url,
            self.branch_status_service,
            self.protected_branches,
            show_summary=self.verbose,
        )

        # Handle cleanup if enabled
        if cleanup_enabled:
            self._perform_cleanup(branch_details)

    def get_deletable_branches(self, branches: list, force_mode: bool = False) -> list:
        """Filter branches to get only those that can be deleted.

        Args:
            branches: List of BranchDetails objects
            force_mode: If True, include branches with uncommitted changes

        Returns:
            List of deletable BranchDetails (excludes worktree entries)
        """
        deletable = []
        for branch in branches:
            # Skip worktree entries
            if branch.is_worktree:
                continue

            # Check if deletable using validation service
            if force_mode:
                # In force mode, allow branches with uncommitted changes
                # but still respect protected branches and worktree checks
                is_force_deletable = (
                    branch.status in [BranchStatus.STALE, BranchStatus.MERGED]
                    and branch.name not in self.protected_branches
                    and not branch.in_worktree
                )
                if is_force_deletable:
                    deletable.append(branch)
            else:
                # Normal mode - use standard validation
                if BranchValidationService.is_deletable(branch, self.protected_branches):
                    deletable.append(branch)

        return deletable

    def get_removable_worktrees(self, branches: list) -> list:
        """Filter branches to get only worktree entries that can be removed.

        Args:
            branches: List of BranchDetails objects

        Returns:
            List of removable worktree entries
        """
        return [
            branch
            for branch in branches
            if branch.is_worktree and BranchValidationService.is_worktree_removable(branch)
        ]

    def perform_deletion(
        self, branches_to_delete: list, worktrees_to_remove: list, force_mode: bool = False
    ) -> tuple:
        """Perform deletion of branches and removal of worktrees.

        This is the shared deletion logic used by both CLI and TUI.
        Worktrees are removed first, then branches are deleted.

        Args:
            branches_to_delete: List of BranchDetails to delete
            worktrees_to_remove: List of BranchDetails (worktree entries) to remove
            force_mode: If True, skip uncommitted changes checks (but not PR or worktree checks)

        Returns:
            Tuple of (deleted_branches, failed_branches, removed_worktrees, failed_worktrees)
            where failed items are tuples of (branch_name, error_message)
        """
        deleted_branches = []
        failed_branches = []
        removed_worktrees = []
        failed_worktrees = []

        # Remove worktrees first
        for wt in worktrees_to_remove:
            is_orphaned = wt.notes and "[ORPHANED]" in wt.notes
            force = is_orphaned or force_mode

            success, error_message = self.git_service.worktree_service.remove_worktree(
                wt.worktree_path, force=force
            )

            if success:
                removed_worktrees.append(wt.worktree_path)
            else:
                failed_worktrees.append((wt.worktree_path, error_message or "Unknown error"))

        # Prune worktree metadata to update Git's internal state
        if worktrees_to_remove:
            self.git_service.worktree_service.prune_worktrees()

        # Delete branches
        for branch in branches_to_delete:
            reason = format_deletion_reason(branch.status)
            success, error_message = self.delete_branch(branch.name, reason, force_mode=force_mode)

            if success:
                deleted_branches.append(branch.name)
            else:
                failed_branches.append((branch.name, error_message or "Unknown error"))

        return (deleted_branches, failed_branches, removed_worktrees, failed_worktrees)

    def _perform_cleanup(self, branch_details: list) -> None:
        """Delete stale and merged branches and remove worktrees after confirmation."""
        # Use shared methods to filter deletable items
        branches_to_delete = self.get_deletable_branches(branch_details, force_mode=self.force_mode)
        worktrees_to_remove = self.get_removable_worktrees(branch_details)

        if not branches_to_delete and not worktrees_to_remove:
            self._console_print("\n[green]No branches or worktrees to clean up![/green]")
            return

        self._console_print(
            f"\n[yellow]Found {len(branches_to_delete)} branches and {len(worktrees_to_remove)} worktrees to clean up[/yellow]"
        )

        # Get confirmation if not in force mode
        if not self.force_mode:
            if not self._confirm_deletion_with_worktrees(branches_to_delete, worktrees_to_remove):
                self._console_print("[yellow]Cleanup cancelled[/yellow]")
                return

        # Perform the deletion using shared method
        self._console_print("")
        deleted_branches, failed_branches, removed_worktrees, failed_worktrees = (
            self.perform_deletion(
                branches_to_delete, worktrees_to_remove, force_mode=self.force_mode
            )
        )

        # Display results
        for wt_path in removed_worktrees:
            self._console_print(f"[green]✓ Removed worktree at {wt_path}[/green]")

        for wt_path, error in failed_worktrees:
            self._console_print(f"[red]✗ Failed to remove worktree at {wt_path}: {error}[/red]")

        total_deleted = len(deleted_branches)
        total_removed = len(removed_worktrees)
        self._console_print(
            f"\n[green]Successfully removed {total_removed} worktrees and deleted {total_deleted} branches[/green]"
        )

        if failed_branches:
            self._console_print(f"\n[red]Failed to delete {len(failed_branches)} branches:[/red]")
            for branch_name, error in failed_branches:
                self._console_print(f"[red]  • {branch_name}: {error}[/red]")

    def _confirm_deletion(self, branches_to_delete: list) -> bool:
        """Show branches to delete and ask for confirmation."""
        self._console_print("\nThe following branches will be deleted:")
        self._console_print(format_deletion_confirmation_items(branches_to_delete))

        response = console.input("\nProceed with deletion? [y/N] ")
        return response.lower() == "y"

    def _confirm_deletion_with_worktrees(
        self, branches_to_delete: list, worktrees_to_remove: list
    ) -> bool:
        """Show branches and worktrees to delete/remove and ask for confirmation."""
        if branches_to_delete:
            self._console_print("\nThe following branches will be deleted:")
            self._console_print(format_deletion_confirmation_items(branches_to_delete))

        if worktrees_to_remove:
            self._console_print("\nThe following worktrees will be removed:")
            for wt in worktrees_to_remove:
                status = (
                    "[ORPHANED]"
                    if wt.notes and "[ORPHANED]" in wt.notes
                    else format_deletion_reason(wt.status)
                )
                self._console_print(f"  • {wt.worktree_path} (branch: {wt.name}, {status})")

        response = console.input("\nProceed with cleanup? [y/N] ")
        return response.lower() == "y"

    def _determine_branch_status(self, branch: str, pr_data: Optional[Dict] = None) -> tuple:
        """
        Consolidated method to determine branch status, sync_status, pr_status, and notes.

        Args:
            branch: Branch name to analyze
            pr_data: Optional PR data dictionary

        Returns:
            Tuple of (status, sync_status, pr_status, notes)
        """
        status = None
        pr_status = None
        notes = None

        # Step 1: Determine status based on PR data (if available) or git analysis
        if pr_data and branch in pr_data:
            pr_info = pr_data[branch]

            # If branch has open PRs, it's always ACTIVE
            if pr_info["count"] > 0:
                status = BranchStatus.ACTIVE
                # Format PR status display
                if branch == self.main_branch:
                    pr_status = f"target:{pr_info['count']}"
                else:
                    pr_status = str(pr_info["count"])

            # If branch was merged via PR
            elif pr_info["merged"]:
                # Don't mark main branch as merged - PRs are merged INTO main
                if branch != self.main_branch:
                    status = BranchStatus.MERGED

            # If branch had PR that was closed without merging
            elif pr_info["closed"]:
                notes = "PR closed without merging"
                # Still need to determine if it's stale or active
                status = self.branch_status_service.get_branch_status(
                    branch, self.main_branch, pr_data
                )

        # If status not determined by PR data, use git analysis
        if status is None:
            status = self.branch_status_service.get_branch_status(branch, self.main_branch, pr_data)

        # Step 2: Get sync status
        sync_status = self.git_service.get_branch_sync_status(branch, self.main_branch)

        # Step 3: Ensure sync_status reflects how merge was detected
        if status == BranchStatus.MERGED:
            # Determine merge method from PR data
            if pr_data and branch in pr_data and pr_data[branch].get("merged"):
                sync_status = SyncStatus.MERGED_PR.value
            else:
                sync_status = SyncStatus.MERGED_GIT.value

        return status, sync_status, pr_status, notes

    def _process_single_branch(
        self,
        branch: str,
        status_filter: str,
        pr_data: dict,
        progress=None,
        current_branch_name: Optional[str] = None,
        current_branch_status: Optional[dict] = None,
    ) -> Optional[BranchDetails]:
        """Process a single branch and return its details if it matches the filter.

        Args:
            branch: Branch name to process
            status_filter: Filter to apply (all/merged/stale)
            pr_data: Pull request data
            progress: Optional progress tracker
            current_branch_name: Name of the currently checked out branch
            current_branch_status: Pre-captured file status for current branch (before stashing)
        """
        # Use consolidated method to determine status
        status, sync_status, pr_status_str, notes = self._determine_branch_status(branch, pr_data)

        # Skip if doesn't match filter
        if status_filter != "all" and status.value != status_filter:
            logger.debug(
                f"Skipping {branch} - status {status.value} doesn't match filter {status_filter}"
            )
            return None

        # Check for local changes (uncommitted work) - store detailed breakdown
        modified_files = None
        untracked_files = None
        staged_files = None
        in_worktree = False
        worktree_path_for_details = None  # Store worktree path for BranchDetails
        status_error = None
        try:
            # Use pre-captured status for current branch (captured before stashing)
            # to avoid stash hiding the uncommitted changes
            if branch == current_branch_name and current_branch_status is not None:
                logger.debug(f"Using pre-captured status for current branch {branch}")
                status_details = current_branch_status
            else:
                status_details = self.git_service.get_branch_status_details(branch)
            if status_details.get("in_worktree"):
                in_worktree = True
                worktree_path = status_details.get("worktree_path")
                worktree_path_for_details = worktree_path  # Save for BranchDetails
                logger.debug(f"[CORE] Branch {branch} in_worktree set to TRUE at {worktree_path}")

                # If we have the worktree path, check the status of the worktree
                if worktree_path:
                    worktree_status = self.git_service.worktree_service.get_worktree_status_details(
                        worktree_path
                    )
                    if worktree_status:
                        modified_files = worktree_status.get("modified", False)
                        untracked_files = worktree_status.get("untracked", False)
                        staged_files = worktree_status.get("staged", False)
                        logger.debug(
                            f"[CORE] Got worktree status for {branch}: M={modified_files} U={untracked_files} S={staged_files}"
                        )
                    else:
                        # Empty dict means orphaned or error - set to None to show as unknown
                        modified_files = None
                        untracked_files = None
                        staged_files = None
                        logger.debug(
                            f"[CORE] Worktree appears orphaned or error checking status for {branch}"
                        )
            elif status_details.get("error"):
                # Capture error information
                status_error = status_details.get("error")
                logger.warning(f"[CORE] Status check error for {branch}: {status_error}")
                # Leave as None to indicate status couldn't be determined
                modified_files = None
                untracked_files = None
                staged_files = None
            else:
                modified_files = status_details["modified"]
                untracked_files = status_details["untracked"]
                staged_files = status_details["staged"]
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Could not check branch status for {branch}: {error_msg}")
            status_error = f"Unexpected error: {error_msg}"
            # Leave as None to indicate status couldn't be determined
            modified_files = None
            untracked_files = None
            staged_files = None

        # Append status error to notes if it exists
        final_notes = notes
        if status_error:
            if final_notes:
                final_notes = f"{final_notes}\n[ERROR] {status_error}"
            else:
                final_notes = f"[ERROR] {status_error}"

        details = BranchDetails(
            name=branch,
            last_commit_date=self.git_service.get_last_commit_date(branch),
            age_days=self.git_service.get_branch_age(branch),
            status=status,
            modified_files=modified_files,
            untracked_files=untracked_files,
            staged_files=staged_files,
            has_remote=self.git_service.has_remote_branch(branch),
            sync_status=sync_status,
            pr_status=pr_status_str,
            notes=final_notes,
            in_worktree=in_worktree,
            worktree_path=worktree_path_for_details,  # Store worktree path for branches in worktrees
        )

        logger.debug(
            f"[CORE] Created BranchDetails for {branch}: status={status.value}, in_worktree={details.in_worktree}"
        )

        return details

    def cleanup(self):
        """Clean up branches."""
        self.process_branches(cleanup_enabled=True)

    def update_main(self):
        """Update the main branch from remote."""
        return self.git_service.update_main_branch(self.main_branch)

    def close(self) -> None:
        """Clean up resources and close connections."""
        logger.debug("Closing BranchKeeper resources")
        try:
            # Close GitHub API connection
            if self.github_service:
                self.github_service.close()
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
