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
from git_branch_keeper.services.github_service import GitHubService
from git_branch_keeper.services.git_service import GitService
from git_branch_keeper.services.display_service import DisplayService
from git_branch_keeper.services.branch_status_service import BranchStatusService
from git_branch_keeper.services.cache_service import CacheService
from git_branch_keeper.threading_utils import get_optimal_worker_count
from git_branch_keeper.logging_config import get_logger
from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)

# Module-level reference to the active BranchKeeper instance for signal handling
_active_keeper: Optional['BranchKeeper'] = None

def _signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    if signum == signal.SIGINT:
        print()  # New line after ^C
        if _active_keeper and _active_keeper.git_service.in_git_operation:
            console.print("\n[yellow]Interrupted! Waiting for current Git operation to complete...[/yellow]")
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
        self.verbose = config.get('verbose', False)
        self.debug_mode = config.get('debug', False)
        
        # Initialize repo first
        try:
            self.repo = git.Repo(self.repo_path)
        except Exception as e:
            raise Exception(f"Error initializing repository: {e}")

        # Get configuration values
        self.min_stale_days = config.get('stale_days', 30)
        self.protected_branches = config.get('protected_branches', ['main', 'master'])
        self.ignore_patterns = config.get('ignore_patterns', [])
        self.status_filter = config.get('status_filter', 'all')
        self.interactive = config.get('interactive', True)
        self.dry_run = config.get('dry_run', True)
        self.force_mode = config.get('force', False)
        self.main_branch = config.get('main_branch', 'main')

        # Initialize services
        self.github_service = GitHubService(self.repo_path, self.config)
        self.git_service = GitService(self.repo_path, self.config)
        
        # Setup GitHub integration
        try:
            remote_url = self.repo.remotes.origin.url
            logger.debug(f"Setting up GitHub API with remote: {remote_url}")
            self.github_service.setup_github_api(remote_url)

            # If GitHub is available but no token, show a helpful message
            if not self.github_service.github_enabled and 'github.com' in remote_url:
                self._console_print("[yellow]💡 Tip: Set up a GitHub token for better merge detection and PR status[/yellow]")
                self._console_print("[yellow]   See: git-branch-keeper --help or check the README for setup instructions[/yellow]")
                self._console_print("")
        except Exception as e:
            logger.debug(f"Failed to setup GitHub API: {e}")
        
        self.branch_status_service = BranchStatusService(
            self.repo_path,
            self.config,
            self.git_service,
            self.github_service,
            self.verbose
        )
        self.display_service = DisplayService(
            verbose=self.verbose,
            debug=self.debug_mode
        )
        self.cache_service = CacheService(self.repo_path)

        # Initialize statistics
        self.stats = {
            "deleted": 0,
            "skipped_pr": 0,
            "skipped_protected": 0,
            "skipped_pattern": 0
        }

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

    def delete_branch(self, branch_name: str, reason: str) -> bool:
        """Delete a branch or show what would be deleted in dry-run mode."""
        try:
            # Check for open PRs first
            if self.github_service.has_open_pr(branch_name):
                self._console_print(f"[yellow]Skipping {branch_name} - Has open PR[/yellow]")
                self.stats["skipped_pr"] += 1
                return False

            # Cannot delete current branch - check BEFORE dry_run
            try:
                current_branch = self.repo.active_branch.name
                if branch_name == current_branch:
                    self._console_print(f"[yellow]Cannot delete current branch {branch_name}[/yellow]")
                    return False
            except TypeError:
                # Detached HEAD state - no active branch, so we can delete any branch
                pass

            # Check for local changes
            status_details = self.git_service.get_branch_status_details(branch_name)
            if status_details['modified'] or status_details['untracked'] or status_details['staged']:
                warning = []
                if status_details['modified']:
                    warning.append("modified files")
                if status_details['untracked']:
                    warning.append("untracked files")
                if status_details['staged']:
                    warning.append("staged files")

                # Show a cleaner warning
                change_indicators = []
                if status_details['modified']:
                    change_indicators.append("M")
                if status_details['untracked']:
                    change_indicators.append("U")
                if status_details['staged']:
                    change_indicators.append("S")

                self._console_print(f"[yellow]⚠️  {branch_name} has uncommitted changes when checked out: {'/'.join(change_indicators)}[/yellow]")
                self._console_print("[dim]   This might indicate files that are ignored differently between branches[/dim]")

                if self.interactive:
                    response = input(f"   Still want to delete branch {branch_name}? [y/N] ")
                    if response.lower() != 'y':
                        return False
                else:
                    self._console_print("   Skipping due to uncommitted changes")
                    return False

            remote_exists = self.git_service.has_remote_branch(branch_name)
            if self.dry_run:
                if remote_exists:
                    self._console_print(f"Would delete local and remote branch {branch_name} ({reason})")
                else:
                    self._console_print(f"Would delete local branch {branch_name} ({reason})")
                return True

            return self.git_service.delete_branch(branch_name, self.dry_run)

        except Exception as e:
            self._console_print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
            return False

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
            use_cache = not self.config.get('refresh', False)

            # Get cached branches (only immutable ones)
            cached_branches = {}
            if use_cache:
                cached_branches = self.cache_service.get_cached_branches(branches, self.main_branch)
                logger.debug(f"Using {len(cached_branches)} cached branches")

            # Separate branches into cached vs needs processing
            branches_to_process = [b for b in branches if b not in cached_branches]

            if self.verbose or self.debug_mode:
                if cached_branches:
                    self._console_print(f"[dim]Using {len(cached_branches)} cached branches, checking {len(branches_to_process)} branches[/dim]")

            # Process non-cached branches
            branch_details = self._collect_branch_details(branches_to_process, show_progress=True)

            # Add cached branches to results
            branch_details.extend(cached_branches.values())

            # Sort all branches (both cached and newly processed)
            branch_details = self._sort_branches(branch_details)

            # Save cache with new data
            if use_cache:
                self.cache_service.save_cache(branch_details, self.main_branch)

            # Display and optionally cleanup
            self._display_and_cleanup(branch_details, cleanup_enabled)

        except Exception as e:
            self._console_print(f"[red]Error processing branches: {e}[/red]")

    def get_cached_branches_fast(self) -> tuple[list, list]:
        """Quickly load cached branch data without processing.

        This method is optimized for fast initial loading in TUI mode.
        It returns cached branches immediately without any processing.

        Returns:
            Tuple of (cached_branch_details, branches_to_process):
            - cached_branch_details: List of BranchDetails objects from cache
            - branches_to_process: List of branch names that need processing
        """
        try:
            # Check if we should use cache
            use_cache = not self.config.get('refresh', False)
            if not use_cache:
                # If refresh is requested, return empty cache and all branches
                branches = self._get_filtered_branches()
                return [], branches

            # Get filtered branches
            branches = self._get_filtered_branches()
            if not branches:
                return [], []

            # Get cached branches (only immutable ones)
            cached_branches = self.cache_service.get_cached_branches(branches, self.main_branch)
            logger.debug(f"Fast-loaded {len(cached_branches)} cached branches")

            # Separate branches into cached vs needs processing
            branches_to_process = [b for b in branches if b not in cached_branches]

            # Convert cached branches dict to list and sort
            cached_branch_details = list(cached_branches.values())
            cached_branch_details = self._sort_branches(cached_branch_details)

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
            use_cache = not self.config.get('refresh', False)

            # Get cached branches (only immutable ones)
            cached_branches = {}
            if use_cache:
                cached_branches = self.cache_service.get_cached_branches(branches, self.main_branch)
                logger.debug(f"Using {len(cached_branches)} cached branches")

            # Separate branches into cached vs needs processing
            branches_to_process = [b for b in branches if b not in cached_branches]

            if self.verbose or self.debug_mode:
                if cached_branches:
                    self._console_print(f"[dim]Using {len(cached_branches)} cached branches, checking {len(branches_to_process)} branches[/dim]")

            # Process non-cached branches
            branch_details = self._collect_branch_details(branches_to_process, show_progress=show_progress)

            # Add cached branches to results
            branch_details.extend(cached_branches.values())

            # Sort all branches (both cached and newly processed)
            branch_details = self._sort_branches(branch_details)

            # Save cache with new data
            if use_cache:
                self.cache_service.save_cache(branch_details, self.main_branch)

            return branch_details

        except Exception as e:
            self._console_print(f"[red]Error getting branch details: {e}[/red]")
            return []

    def _check_main_branch_status(self) -> bool:
        """Check if main branch is up to date. Returns False if behind."""
        main_sync_status = self.git_service.get_branch_sync_status(self.main_branch, self.main_branch)
        if "behind" in main_sync_status:
            self._console_print(f"[yellow]Warning: Your {self.main_branch} branch is {main_sync_status}[/yellow]")
            self._console_print(f"[yellow]Please update your {self.main_branch} branch first:[/yellow]")
            self._console_print(f"  git checkout {self.main_branch}")
            self._console_print(f"  git pull origin {self.main_branch}")
            self._console_print("")
        return True

    def _get_filtered_branches(self) -> list:
        """Get all branches excluding tags, stash, remote refs, and ignored patterns."""
        branches = [
            ref.name for ref in self.repo.refs
            if not ref.name.startswith('origin/')
            and not ref.name.startswith('refs/stash')
            and not self.git_service.is_tag(ref.name)
        ]

        # Only filter out ignored branches, keep protected ones
        return [b for b in branches if not self.branch_status_service.should_ignore_branch(b)]

    def _sort_branches(self, branch_details: list) -> list:
        """Sort branches according to configuration with protected branches always first."""
        sort_by = self.config.get('sort_by', 'age')
        sort_order = self.config.get('sort_order', 'asc')
        reverse = (sort_order == 'desc')

        def date_to_int(date_str: str) -> int:
            """Convert date string to integer for sorting. Returns 0 for invalid dates."""
            try:
                return int(date_str.replace('-', ''))
            except (ValueError, AttributeError):
                return 0  # Invalid dates sort to beginning

        if sort_by == 'name':
            # Sort: protected first, then alphabetically by branch name
            branch_details.sort(key=lambda b: (
                0 if b.name in self.protected_branches else 1,
                b.name.lower() if not reverse else chr(255) + b.name.lower()
            ), reverse=reverse if reverse else False)
        elif sort_by == 'age':
            # Sort: protected first, then by age, then newest first within same age
            branch_details.sort(key=lambda b: (
                0 if b.name in self.protected_branches else 1,
                b.age_days if not reverse else -b.age_days,
                -date_to_int(b.last_commit_date)  # Negative for newest-first
            ))
        elif sort_by == 'date':
            # Sort: protected first, then by date, then alphabetically within same date
            branch_details.sort(key=lambda b: (
                0 if b.name in self.protected_branches else 1,
                b.last_commit_date if not reverse else chr(255) + b.last_commit_date,
                b.name.lower()
            ), reverse=reverse if reverse else False)
        elif sort_by == 'status':
            # Sort: protected first, then by status, then by age, then newest first
            status_order = {BranchStatus.ACTIVE: 0, BranchStatus.STALE: 1, BranchStatus.MERGED: 2}
            branch_details.sort(key=lambda b: (
                0 if b.name in self.protected_branches else 1,
                status_order.get(b.status, 99) if not reverse else -status_order.get(b.status, 99),
                b.age_days if not reverse else -b.age_days,
                -date_to_int(b.last_commit_date)
            ))

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
        status_filter = self.config.get('status_filter', 'all')
        sequential = self.config.get('sequential', False)
        pr_data: Dict[str, Dict] = {}

        # Verbose mode: show simple output
        if self.verbose or self.debug_mode:
            self._console_print("Processing branches...")

            # Fetch PR data
            pr_data = self._fetch_pr_data_with_feedback(branches)

            # Process branches sequentially in verbose mode for readable logs
            for branch_name in branches:
                details = self._process_single_branch(branch_name, status_filter, pr_data, None)
                if details:
                    branch_details.append(details)
        else:
            # Non-verbose mode: show spinner for PR fetch, then progress bar for processing

            # Phase 1: Fetch PR data with optional spinner (only if there are branches to check)
            if self.github_service.github_enabled and branches:
                status_context = console.status("[bold blue]Fetching PR data from GitHub...", spinner="dots") if show_progress else nullcontext()
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
                    max_workers = get_optimal_worker_count(self.config.get('workers'))
                    task_desc = f"Processing branches ({max_workers} workers)..."

                # Only create task if we have a real Progress object
                task = progress.add_task(task_desc, total=len(branches)) if progress is not None else None

                if sequential:
                    # Sequential processing
                    branch_details = self._process_branches_sequential(
                        branches, status_filter, pr_data, progress if show_progress else None, task
                    )
                else:
                    # Parallel processing
                    branch_details = self._process_branches_parallel(
                        branches, status_filter, pr_data, progress if show_progress else None, task
                    )

        # Sort branches according to configuration
        branch_details = self._sort_branches(branch_details)

        return branch_details

    def _fetch_pr_data_with_feedback(self, branches: list) -> Dict[str, Dict]:
        """Fetch PR data with user feedback."""
        pr_data: Dict[str, Dict] = {}
        if self.github_service.github_enabled:
            try:
                self._console_print("[dim]Fetching PR data from GitHub...[/dim]")
                logger.debug(f"Fetching PR data for {len(branches)} branches")
                pr_data = self.github_service.get_bulk_pr_data(branches)
            except Exception as e:
                logger.debug(f"Failed to fetch PR data: {e}")
        return pr_data

    def _process_branches_sequential(
        self, branches: list, status_filter: str, pr_data: Dict, progress, task
    ) -> list:
        """Process branches sequentially."""
        branch_details = []
        for branch_name in branches:
            details = self._process_single_branch(branch_name, status_filter, pr_data, None)
            if details:
                branch_details.append(details)
            if progress:  # Only update if progress bar exists
                progress.update(task, advance=1)
        return branch_details

    def _process_branches_parallel(
        self, branches: list, status_filter: str, pr_data: Dict, progress, task
    ) -> list:
        """Process branches in parallel using ThreadPoolExecutor."""
        branch_details = []

        # Get optimal worker count (already shown in progress bar)
        max_workers = get_optimal_worker_count(
            self.config.get('workers')
        )
        logger.debug(f"Using {max_workers} workers for parallel processing")

        # Submit all branch processing tasks
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_branch = {
                executor.submit(
                    self._process_single_branch,
                    branch, status_filter, pr_data, None
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
            if 'github.com' not in remote_url:
                return None

            if remote_url.startswith('git@'):
                org_repo = remote_url.split(':')[1].replace('.git', '')
                return f"https://github.com/{org_repo}"
            else:
                return remote_url.replace('.git', '')
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
            show_summary=self.verbose
        )

        # Handle cleanup if enabled
        if cleanup_enabled:
            self._perform_cleanup(branch_details)

    def _perform_cleanup(self, branch_details: list) -> None:
        """Delete stale and merged branches after confirmation."""
        branches_to_delete = [
            branch for branch in branch_details
            if branch.status in [BranchStatus.STALE, BranchStatus.MERGED]
            and branch.name not in self.protected_branches
        ]

        if not branches_to_delete:
            self._console_print("\n[green]No branches to clean up![/green]")
            return

        self._console_print(f"\n[yellow]Found {len(branches_to_delete)} branches to clean up[/yellow]")

        # Get confirmation if not in force mode
        if not self.force_mode:
            if not self._confirm_deletion(branches_to_delete):
                self._console_print("[yellow]Cleanup cancelled[/yellow]")
                return

        # Delete the branches
        self._console_print("")
        deleted_count = self._delete_branches(branches_to_delete)
        self._console_print(f"\n[green]Successfully deleted {deleted_count} branches[/green]")

    def _confirm_deletion(self, branches_to_delete: list) -> bool:
        """Show branches to delete and ask for confirmation."""
        self._console_print("\nThe following branches will be deleted:")
        for branch in branches_to_delete:
            reason = "stale" if branch.status == BranchStatus.STALE else "merged"
            remote_info = "local and remote" if branch.has_remote else "local only"
            self._console_print(f"  • {branch.name} ({reason}, {remote_info})")

        response = console.input("\nProceed with deletion? [y/N] ")
        return response.lower() == 'y'

    def _delete_branches(self, branches_to_delete: list) -> int:
        """Delete a list of branches and return count of deleted branches."""
        deleted_count = 0
        for branch in branches_to_delete:
            reason = "stale" if branch.status == BranchStatus.STALE else "merged"
            if self.delete_branch(branch.name, reason):
                deleted_count += 1
        return deleted_count

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
            if pr_info['count'] > 0:
                status = BranchStatus.ACTIVE
                # Format PR status display
                if branch == self.main_branch:
                    pr_status = f"target:{pr_info['count']}"
                else:
                    pr_status = str(pr_info['count'])

            # If branch was merged via PR
            elif pr_info['merged']:
                # Don't mark main branch as merged - PRs are merged INTO main
                if branch != self.main_branch:
                    status = BranchStatus.MERGED

            # If branch had PR that was closed without merging
            elif pr_info['closed']:
                notes = "PR closed without merging"
                # Still need to determine if it's stale or active
                status = self.branch_status_service.get_branch_status(branch, self.main_branch, pr_data)

        # If status not determined by PR data, use git analysis
        if status is None:
            status = self.branch_status_service.get_branch_status(branch, self.main_branch, pr_data)

        # Step 2: Get sync status
        sync_status = self.git_service.get_branch_sync_status(branch, self.main_branch)

        # Step 3: Ensure sync_status reflects how merge was detected
        if status == BranchStatus.MERGED:
            if sync_status not in [SyncStatus.MERGED_GIT.value, SyncStatus.MERGED_PR.value]:
                # Branch was detected as merged but sync_status doesn't reflect it
                if pr_data and branch in pr_data and pr_data[branch].get('merged'):
                    sync_status = SyncStatus.MERGED_PR.value
                else:
                    sync_status = SyncStatus.MERGED_GIT.value

        return status, sync_status, pr_status, notes

    def _process_single_branch(self, branch: str, status_filter: str, pr_data: dict, progress=None) -> Optional[BranchDetails]:
        """Process a single branch and return its details if it matches the filter."""
        # Use consolidated method to determine status
        status, sync_status, pr_status_str, notes = self._determine_branch_status(branch, pr_data)

        # Skip if doesn't match filter
        if status_filter != 'all' and status.value != status_filter:
            logger.debug(f"Skipping {branch} - status {status.value} doesn't match filter {status_filter}")
            return None

        # Check for local changes (uncommitted work)
        has_local_changes = False
        try:
            status_details = self.git_service.get_branch_status_details(branch)
            has_local_changes = any([
                status_details['modified'],
                status_details['untracked'],
                status_details['staged']
            ])
        except Exception as e:
            logger.debug(f"Could not check local changes for {branch}: {e}")
            # If we can't check, assume no local changes
            has_local_changes = False

        details = BranchDetails(
            name=branch,
            last_commit_date=self.git_service.get_last_commit_date(branch),
            age_days=self.git_service.get_branch_age(branch),
            status=status,
            has_local_changes=has_local_changes,
            has_remote=self.git_service.has_remote_branch(branch),
            sync_status=sync_status,
            pr_status=pr_status_str,
            notes=notes
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
