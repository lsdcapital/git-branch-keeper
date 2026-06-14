"""Core functionality for git-branch-keeper"""

import signal
import sys
from contextlib import nullcontext
from typing import Dict, Optional, Union
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

import git
from rich.console import Console
from rich.progress import Progress

from git_branch_keeper.models.branch import (
    BranchStatus,
    SyncStatus,
    BranchDetails,
    BranchAnalysisProgress,
    BranchAnalysisProgressCallback,
    BranchAnalysisResult,
    OperationProgressCallback,
)
from git_branch_keeper.services.git import GitHubService, GitOperations
from git_branch_keeper.services.git.github import resolve_github_token
from git_branch_keeper.services.display_service import DisplayService
from git_branch_keeper.services.branch_status_service import BranchStatusService
from git_branch_keeper.services.cache_service import CacheService
from git_branch_keeper.services.branch_validation_service import BranchValidationService
from git_branch_keeper.utils.threading import get_optimal_worker_count
from git_branch_keeper.utils.logging import get_logger
from git_branch_keeper.utils.remotes import detect_remote_name, get_remote_url
from git_branch_keeper.config import Config
from git_branch_keeper.formatters import format_deletion_confirmation_items, format_deletion_reason

console = Console()
logger = get_logger(__name__)

# PyGithub/urllib3 defaults to a 10-connection pool for api.github.com. Keep
# GitHub-enabled branch processing under that so the unified progress loop does
# not flood the pool with concurrent PR lookups.
GITHUB_ENABLED_WORKER_CAP = 8

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

        # Detect which remote to use (prefers "origin"; adapts to a single non-origin remote)
        self.remote_name = detect_remote_name(self.repo)

        # Get configuration values
        self.min_stale_days = config.get("stale_days", 30)
        self.protected_branches = config.get("protected_branches", ["main", "master"])
        self.ignore_patterns = config.get("ignore_patterns", [])
        self.status_filter = config.get("status_filter", "all")
        self.interactive = config.get("interactive", True)
        self.dry_run = config.get("dry_run", True)
        self.force_mode = config.get("force", False)
        self.delete_remote = config.get("delete_remote", False)
        self.main_branch = config.get("main_branch", "main")

        # Resolve optional GitHub auth before initializing services.
        # Check for GitHub integration (optional)
        remote_url = None
        is_github_repo = False
        has_github_token = False

        remote_url = get_remote_url(self.repo, self.remote_name)
        if remote_url:
            is_github_repo = "github.com" in remote_url

            if is_github_repo:
                # Check if token exists in config/env or via authenticated gh CLI.
                github_token = resolve_github_token(self.config)
                if github_token and not self.config.get("github_token"):
                    self.config.github_token = github_token
                has_github_token = bool(github_token)

                if not has_github_token:
                    # GitHub repo without token - inform user about limited functionality
                    logger.info(
                        "GitHub auth not found. Running in local-only mode.\n"
                        "  • Branch analysis will work normally\n"
                        "  • PR detection and protection: DISABLED\n"
                        "  • To enable: Set GITHUB_TOKEN/GH_TOKEN, add github_token to config, "
                        "or run `gh auth login`\n"
                        "  • Get token at: https://github.com/settings/tokens"
                    )
                    if not self.tui_mode:
                        self._console_print(
                            "[yellow]ℹ GitHub auth not found - PR detection disabled[/yellow]"
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
        else:
            # No usable remote - local-only repo
            logger.info(f"No '{self.remote_name}' remote found. Running in local-only mode.")
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
        self,
        branch_name: str,
        reason: str,
        force_mode: bool = False,
        batch_id: Optional[str] = None,
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
                if remote_exists and self.delete_remote:
                    self._console_print(
                        f"Would delete local and remote branch {branch_name} ({reason})"
                    )
                elif remote_exists:
                    self._console_print(
                        f"Would delete local branch {branch_name} ({reason}) - "
                        "remote kept (use --remote to also delete it)"
                    )
                else:
                    self._console_print(f"Would delete local branch {branch_name} ({reason})")
                return True, None

            # Delete the branch
            success = self.git_service.delete_branch(
                branch_name,
                self.dry_run,
                delete_remote=self.delete_remote,
                batch_id=batch_id,
            )

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

        # If status is unavailable, leave flags as None and surface the reason in notes.
        modified_files = status_details.get("modified") if status_details else None
        untracked_files = status_details.get("untracked") if status_details else None
        staged_files = status_details.get("staged") if status_details else None
        status_error = status_details.get("error") if status_details else None
        notes = f"{'[ORPHANED] ' if worktree_info.is_orphaned else ''}{worktree_info.path}"
        if status_error:
            notes = f"{notes}\n[ERROR] {status_error}"

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
            pr_details=parent_branch.pr_details,
            notes=notes,
            in_worktree=False,  # This IS the worktree, not "in" a worktree
            is_worktree=True,
            worktree_path=worktree_info.path,
            merge_detection=parent_branch.merge_detection,
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
        """Analyze branches, render the CLI view, and optionally preview/perform cleanup."""
        try:
            analysis = self.analyze_branches(show_progress=True)
            if not analysis.local_branch_names:
                self._console_print("No branches to process")
                return

            self._display_and_cleanup(analysis, cleanup_enabled)

        except Exception as e:
            self._console_print(f"[red]Error processing branches: {e}[/red]")

    def _emit_operation_progress(
        self,
        progress_callback: Optional[OperationProgressCallback],
        phase: str,
        current: int = 0,
        total: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        """Emit a shared operation progress update if a callback was supplied."""
        if progress_callback is None:
            return

        try:
            progress_callback(
                BranchAnalysisProgress(
                    phase=phase,
                    current=current,
                    total=total,
                    message=message,
                )
            )
        except Exception:
            logger.debug("Operation progress callback failed", exc_info=True)

    def _emit_analysis_progress(
        self,
        progress_callback: Optional[BranchAnalysisProgressCallback],
        phase: str,
        current: int = 0,
        total: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        """Emit a branch-analysis progress update if a callback was supplied."""
        self._emit_operation_progress(progress_callback, phase, current, total, message)

    def analyze_branches(
        self,
        show_progress: bool = True,
        progress_callback: Optional[BranchAnalysisProgressCallback] = None,
    ) -> BranchAnalysisResult:
        """Analyze branch state using the shared CLI/TUI data path.

        This is the single source of truth for branch discovery, cache refresh
        decisions, status calculation, sorting, dynamic worktree state, and
        deletion eligibility. CLI and TUI code should consume this result rather
        than reimplementing branch-data assembly.

        Args:
            show_progress: Whether to show Rich progress while processing branches.
            progress_callback: Optional callback for UI-native progress updates.

        Returns:
            BranchAnalysisResult with display rows and shared metadata.
        """
        self._emit_analysis_progress(progress_callback, "Checking main branch")
        if not self._check_main_branch_status():
            self._emit_analysis_progress(progress_callback, "Complete", 0, 0)
            return BranchAnalysisResult(is_complete=False)

        self._emit_analysis_progress(progress_callback, "Discovering branches")
        branches = self._get_filtered_branches()
        if not branches:
            self._emit_analysis_progress(
                progress_callback,
                "Complete",
                0,
                0,
                "No branches found",
            )
            return BranchAnalysisResult(local_branch_names=[])

        use_cache = not self.config.get("refresh", False)
        cached_branches: Dict[str, BranchDetails] = {}
        branches_to_process = branches

        if use_cache:
            self._emit_analysis_progress(progress_callback, "Loading cache")
            cached_branches = self.cache_service.get_cached_branches(branches)
            logger.debug(f"Loaded {len(cached_branches)} cached branches")

            branches_to_process = self.cache_service.get_stale_branches(branches, self.main_branch)

            stable_count = len(branches) - len(branches_to_process)
            if (self.verbose or self.debug_mode) and stable_count > 0:
                self._console_print(
                    f"[dim]Using {stable_count} stable cached branches, refreshing {len(branches_to_process)} branches[/dim]"
                )
        else:
            self._emit_analysis_progress(
                progress_callback,
                "Preparing refresh",
                message=f"Refreshing all {len(branches)} branches",
            )
            if self.verbose or self.debug_mode:
                self._console_print(
                    f"[dim]Refreshing all {len(branches)} branches (--refresh mode)[/dim]"
                )

        refreshed_details = self._collect_branch_details(
            branches_to_process,
            show_progress=show_progress,
            progress_callback=progress_callback,
        )

        branch_rows = list(refreshed_details)
        scheduled_for_refresh = set(branches_to_process)

        # Only reuse cached branches that were deliberately skipped as stable.
        # If a branch was scheduled for refresh but now no longer matches the
        # filter, do not re-add its stale cached row.
        for branch_name, cached_branch in cached_branches.items():
            if branch_name in scheduled_for_refresh:
                continue
            if self._branch_matches_status_filter(cached_branch):
                branch_rows.append(cached_branch)

        self._emit_analysis_progress(progress_callback, "Finalizing", len(branches), len(branches))
        analysis = self._finalize_branch_analysis(
            branch_rows,
            local_branch_names=branches,
            branches_to_process=branches_to_process,
            cached_count=len(cached_branches),
            refreshed_count=len(refreshed_details),
            is_complete=True,
            save_cache=True,
        )
        self._emit_analysis_progress(
            progress_callback,
            "Complete",
            len(branches_to_process),
            len(branches_to_process),
            "Branch data ready",
        )
        return analysis

    def get_cached_analysis_fast(
        self, finalize_partial: bool = False, include_refresh_candidates: bool = False
    ) -> BranchAnalysisResult:
        """Load cached analysis rows without processing branches.

        This supports fast TUI startup. Complete cached snapshots are finalized
        using the same core logic as full analysis. Partial snapshots are cheap
        by default: callers get ``branches_to_process`` and can schedule the
        shared analyzer without paying worktree/deletion finalization cost for
        rows they will immediately replace.

        Args:
            finalize_partial: If True, also finalize cached rows when some
                branches still need refresh.
            include_refresh_candidates: If True, include cached rows even when
                they are scheduled for refresh. This is useful for a provisional
                cache-first UI that will update rows in the background.
        """
        try:
            branches = self._get_filtered_branches()
            if not branches:
                return BranchAnalysisResult(local_branch_names=[])

            use_cache = not self.config.get("refresh", False)
            if not use_cache:
                return BranchAnalysisResult(
                    local_branch_names=branches,
                    branches_to_process=branches,
                    is_complete=False,
                )

            cached_branches = self.cache_service.get_cached_branches(branches)
            logger.debug(f"Fast-loaded {len(cached_branches)} cached branches")

            branches_to_process = self.cache_service.get_stale_branches(branches, self.main_branch)

            if branches_to_process and not finalize_partial:
                return BranchAnalysisResult(
                    local_branch_names=branches,
                    branches_to_process=branches_to_process,
                    cached_count=len(cached_branches),
                    is_complete=False,
                )

            scheduled_for_refresh = set(branches_to_process)

            cached_branch_rows = []
            for branch_name, cached_branch in cached_branches.items():
                if not include_refresh_candidates and branch_name in scheduled_for_refresh:
                    continue
                if self._branch_matches_status_filter(cached_branch):
                    cached_branch_rows.append(cached_branch)

            return self._finalize_branch_analysis(
                cached_branch_rows,
                local_branch_names=branches,
                branches_to_process=branches_to_process,
                cached_count=len(cached_branches),
                refreshed_count=0,
                is_complete=not bool(branches_to_process),
                save_cache=False,
            )

        except Exception as e:
            logger.debug(f"Error fast-loading cached analysis: {e}")
            try:
                branches = self._get_filtered_branches()
                return BranchAnalysisResult(
                    local_branch_names=branches,
                    branches_to_process=branches,
                    is_complete=False,
                )
            except Exception:
                return BranchAnalysisResult(is_complete=False)

    def get_cached_branches_fast(self) -> tuple[list, list]:
        """Backward-compatible wrapper around get_cached_analysis_fast()."""
        analysis = self.get_cached_analysis_fast(
            finalize_partial=True, include_refresh_candidates=True
        )
        return analysis.branches, analysis.branches_to_process

    def get_branch_details(self, show_progress: bool = True) -> list:
        """Return analyzed branch display rows for callers that need only rows.

        Args:
            show_progress: Whether to show Rich Progress bars (default True for CLI, False for TUI)

        Returns:
            List of BranchDetails objects, including worktree rows.
        """
        try:
            return self.analyze_branches(show_progress=show_progress).branches
        except Exception as e:
            self._console_print(f"[red]Error getting branch details: {e}[/red]")
            return []

    def _branch_matches_status_filter(self, branch: BranchDetails) -> bool:
        """Return True if a branch row matches the active status filter."""
        status_filter = self.config.get("status_filter", "all")
        return status_filter == "all" or branch.status.value == status_filter

    def _current_branch_name(self) -> Optional[str]:
        """Return the current branch name, or None for detached HEAD."""
        try:
            return self.repo.active_branch.name
        except (TypeError, AttributeError):
            return None

    def _apply_dynamic_worktree_status(self, branch_rows: list) -> None:
        """Refresh dynamic worktree flags for local branch rows.

        Worktree membership is intentionally not trusted from cache; it can
        change independently of branch commits or status.
        """
        worktree_infos = self.git_service.worktree_service.get_worktree_info()
        worktree_by_branch = {
            wt.branch_name: wt for wt in worktree_infos if wt.branch_name and not wt.is_main
        }
        worktree_branches = set(worktree_by_branch)
        logger.debug(f"Worktree branches detected: {worktree_branches}")

        current_branch = self._current_branch_name()
        for branch in branch_rows:
            is_current = branch.name == current_branch if current_branch else False
            branch.in_worktree = branch.name in worktree_branches and not is_current
            if branch.in_worktree and branch.worktree_path is None:
                branch.worktree_path = worktree_by_branch[branch.name].path
            logger.debug(f"Setting in_worktree={branch.in_worktree} for {branch.name}")

    def _finalize_branch_analysis(
        self,
        branch_rows: list,
        local_branch_names: list,
        branches_to_process: list,
        cached_count: int,
        refreshed_count: int,
        is_complete: bool,
        save_cache: bool,
    ) -> BranchAnalysisResult:
        """Finalize shared branch analysis rows and metadata."""
        self._apply_dynamic_worktree_status(branch_rows)
        branch_rows = self.sort_branches(branch_rows)

        if save_cache:
            # Cache only local branch rows. Worktree rows are view-specific and
            # are re-derived from Git worktree metadata each analysis run.
            self.cache_service.save_cache(branch_rows, self.main_branch)

        display_rows = self._insert_worktree_entries(branch_rows)

        removable_worktrees = self.get_removable_worktrees(display_rows)
        deletable_branches = self.get_deletable_branches(display_rows, force_mode=self.force_mode)
        deletable_branches.extend(
            self.get_branches_unblocked_by_worktree_removal(
                display_rows,
                branches_to_delete=deletable_branches,
                worktrees_to_remove=removable_worktrees,
                force_mode=self.force_mode,
            )
        )

        return BranchAnalysisResult(
            branches=display_rows,
            local_branch_names=list(local_branch_names),
            branches_to_process=list(branches_to_process),
            deletable_branches=deletable_branches,
            removable_worktrees=removable_worktrees,
            current_branch=self._current_branch_name(),
            github_base_url=self._get_github_base_url(),
            cached_count=cached_count,
            refreshed_count=refreshed_count,
            is_complete=is_complete,
        )

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
            self._console_print(f"  git pull {self.remote_name} {self.main_branch}")
            self._console_print("")
        return True

    def _get_filtered_branches(self) -> list:
        """Get local branch heads excluding ignored patterns.

        Branch rows must come only from ``refs/heads/*``. ``repo.refs`` also contains tags,
        remote-tracking refs, stash refs, and arbitrary custom refs (for example
        ``refs/conductor-checkpoints/*``), none of which are deletable local branches.
        Worktrees are discovered separately from ``git worktree list --porcelain`` and then
        attached to these local branch rows.
        """
        branches = [head.name for head in self.repo.heads]

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

    def _collect_branch_details(
        self,
        branches: list,
        show_progress: bool = True,
        progress_callback: Optional[BranchAnalysisProgressCallback] = None,
    ) -> list:
        """Process branches and collect their details with unified progress tracking.

        Branch analysis must not modify the user's working tree or stash. The
        current branch is inspected in place with ``git status --porcelain``;
        non-current branches are inspected in temporary worktrees.

        Args:
            branches: List of branch names to process
            show_progress: Whether to show Rich Progress bars (default True for CLI, False for TUI)
            progress_callback: Optional callback for UI-native progress updates.
        """
        if not branches:
            self._emit_analysis_progress(progress_callback, "Processing branches", 0, 0)
            return []

        branch_details = []
        status_filter = self.config.get("status_filter", "all")
        sequential = self.config.get("sequential", False)

        # Capture current branch file status directly. This is read-only and
        # preserves uncommitted/staged/untracked indicators without stashing.
        current_branch_status = None
        try:
            current_branch = self.repo.active_branch.name
            logger.debug(f"Capturing file status for current branch {current_branch}")
            current_branch_status = self.git_service.get_branch_status_details(current_branch)
            logger.debug(
                f"Current branch status: modified={current_branch_status.get('modified')}, "
                f"untracked={current_branch_status.get('untracked')}, "
                f"staged={current_branch_status.get('staged')}"
            )
        except (TypeError, AttributeError):
            # Detached HEAD or other error
            current_branch = None
            logger.debug("No current branch (detached HEAD?), skipping current status check")

        # Verbose mode: show simple output. PR metadata is fetched inside each
        # branch work item so there is one processing path/progress state.
        if self.verbose or self.debug_mode:
            self._console_print("Processing branches...")
            self._emit_analysis_progress(
                progress_callback,
                "Processing branches",
                0,
                len(branches),
                "Processing branches sequentially",
            )

            # Process branches sequentially in verbose mode for readable logs
            completed = 0
            for branch_name in branches:
                details = self._process_single_branch(
                    branch_name,
                    status_filter,
                    None,
                    None,
                    current_branch,
                    current_branch_status,
                )
                if details:
                    branch_details.append(details)
                completed += 1
                self._emit_analysis_progress(
                    progress_callback,
                    "Processing branches",
                    completed,
                    len(branches),
                    "Processing branches sequentially",
                )
        else:
            # PR metadata fetches happen inside the branch workers and are covered
            # by this single progress bar.
            progress_context = Progress() if show_progress else nullcontext()

            with progress_context as progress:
                # Determine worker count for progress message
                if sequential:
                    task_desc = "Processing branches..."
                else:
                    max_workers = self._get_worker_count_for_branches(len(branches))
                    worker_label = "worker" if max_workers == 1 else "workers"
                    task_desc = f"Processing branches ({max_workers} {worker_label})..."

                self._emit_analysis_progress(
                    progress_callback,
                    "Processing branches",
                    0,
                    len(branches),
                    task_desc.rstrip("."),
                )

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
                        None,
                        progress if show_progress else None,
                        task,
                        current_branch,
                        current_branch_status,
                        progress_callback,
                    )
                else:
                    # Parallel processing
                    branch_details = self._process_branches_parallel(
                        branches,
                        status_filter,
                        None,
                        progress if show_progress else None,
                        task,
                        current_branch,
                        current_branch_status,
                        progress_callback,
                    )

        # Sort branches according to configuration
        branch_details = self.sort_branches(branch_details)

        return branch_details

    def _process_branches_sequential(
        self,
        branches: list,
        status_filter: str,
        pr_data: Optional[Dict[str, Dict]],
        progress,
        task,
        current_branch_name: Optional[str] = None,
        current_branch_status: Optional[dict] = None,
        progress_callback: Optional[BranchAnalysisProgressCallback] = None,
    ) -> list:
        """Process branches sequentially."""
        branch_details = []
        for completed, branch_name in enumerate(branches, start=1):
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
            self._emit_analysis_progress(
                progress_callback,
                "Processing branches",
                completed,
                len(branches),
            )
        return branch_details

    def _get_worker_count_for_branches(self, branch_count: int) -> int:
        """Return the effective worker count, capped by branch count and GitHub limits."""
        configured_workers = get_optimal_worker_count(self.config.get("workers"))
        if branch_count <= 0:
            return 1

        if self.github_service.is_enabled():
            configured_workers = min(configured_workers, GITHUB_ENABLED_WORKER_CAP)

        return max(1, min(configured_workers, branch_count))

    def _process_branches_parallel(
        self,
        branches: list,
        status_filter: str,
        pr_data: Optional[Dict[str, Dict]],
        progress,
        task,
        current_branch_name: Optional[str] = None,
        current_branch_status: Optional[dict] = None,
        progress_callback: Optional[BranchAnalysisProgressCallback] = None,
    ) -> list:
        """Process branches in parallel using ThreadPoolExecutor."""
        branch_details: list[BranchDetails] = []

        if not branches:
            return branch_details

        # Cap workers to queued branch jobs; extra branches are queued by the executor.
        max_workers = self._get_worker_count_for_branches(len(branches))
        logger.debug(
            f"Using {max_workers} workers for parallel processing of {len(branches)} branches"
        )

        # Submit all branch processing tasks. Manage shutdown explicitly so Ctrl-C
        # can cancel queued work instead of waiting for every pending future.
        executor = ThreadPoolExecutor(max_workers=max_workers)
        future_to_branch: Dict[Future[Optional[BranchDetails]], str] = {}
        try:
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
            completed = 0
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
                    completed += 1
                    self._emit_analysis_progress(
                        progress_callback,
                        "Processing branches",
                        completed,
                        len(branches),
                    )
        except (KeyboardInterrupt, SystemExit):
            for future in future_to_branch:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

        return branch_details

    def _get_github_base_url(self) -> Optional[str]:
        """Extract GitHub base URL from remote URL."""
        try:
            remote_url = get_remote_url(self.repo, self.remote_name)
            if not remote_url or "github.com" not in remote_url:
                return None

            if remote_url.startswith("git@"):
                org_repo = remote_url.split(":")[1].replace(".git", "")
                return f"https://github.com/{org_repo}"
            else:
                return remote_url.replace(".git", "")
        except Exception:
            return None

    def _display_and_cleanup(self, analysis: BranchAnalysisResult, cleanup_enabled: bool) -> None:
        """Display branch analysis and optionally perform cleanup."""
        if not analysis.branches:
            self._console_print("No branches match the filter criteria")
            return

        self.display_service.display_branch_table(
            analysis.branches,
            self.repo,
            analysis.github_base_url,
            self.branch_status_service,
            self.protected_branches,
            show_summary=self.verbose,
            delete_remote=self.delete_remote,
        )

        if cleanup_enabled:
            self._perform_cleanup(
                analysis.branches,
                branches_to_delete=analysis.deletable_branches,
                worktrees_to_remove=analysis.removable_worktrees,
            )

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

    def get_branches_unblocked_by_worktree_removal(
        self,
        branches: list,
        branches_to_delete: list,
        worktrees_to_remove: list,
        force_mode: bool = False,
    ) -> list:
        """Return parent branches that become deletable after removing worktrees.

        A branch checked out in a worktree cannot be deleted directly. If GBK is
        already going to remove that branch's worktree, include the clean
        merged/stale parent branch in the same cleanup plan so deletion happens
        immediately after the worktree is removed.
        """
        planned_branch_names = {
            branch.name for branch in branches_to_delete if not branch.is_worktree
        }
        removable_worktree_names = {wt.name for wt in worktrees_to_remove if wt.is_worktree}

        unblocked = []
        for branch in branches:
            if branch.is_worktree:
                continue
            if branch.name in planned_branch_names:
                continue
            if branch.name not in removable_worktree_names:
                continue
            if branch.name in self.protected_branches:
                continue
            if branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
                continue

            has_uncommitted = (
                branch.modified_files is True
                or branch.untracked_files is True
                or branch.staged_files is True
            )
            if has_uncommitted and not force_mode:
                continue

            unblocked.append(branch)
            planned_branch_names.add(branch.name)

        return unblocked

    def perform_deletion(
        self,
        branches_to_delete: list,
        worktrees_to_remove: list,
        force_mode: bool = False,
        batch_id: Optional[str] = None,
        progress_callback: Optional[OperationProgressCallback] = None,
        show_progress: bool = False,
    ) -> tuple:
        """Perform deletion of branches and removal of worktrees.

        This is the shared deletion logic used by both CLI and TUI.
        Worktrees are removed first, then branches are deleted.

        Args:
            branches_to_delete: List of BranchDetails to delete
            worktrees_to_remove: List of BranchDetails (worktree entries) to remove
            force_mode: If True, skip uncommitted changes checks (but not PR or worktree checks)
            progress_callback: Optional callback for UI-native progress updates.
            show_progress: Whether to show a Rich progress bar for CLI cleanup.

        Returns:
            Tuple of (deleted_branches, failed_branches, removed_worktrees, failed_worktrees)
            where failed items are tuples of (branch_name, error_message)
        """
        deleted_branches = []
        failed_branches = []
        removed_worktrees = []
        failed_worktrees = []

        total_steps = len(worktrees_to_remove) + len(branches_to_delete)
        completed = 0
        progress_context = Progress() if show_progress and total_steps else nullcontext()

        def update_progress(message: str) -> None:
            nonlocal completed
            completed += 1
            if progress is not None and task is not None:
                progress.update(task, advance=1, description=message)
            self._emit_operation_progress(
                progress_callback,
                "Cleaning up",
                completed,
                total_steps,
                message.rstrip("."),
            )

        self._emit_operation_progress(
            progress_callback,
            "Cleaning up",
            0,
            total_steps,
            "Starting cleanup",
        )

        if branches_to_delete and batch_id is None and not self.dry_run:
            batch_id = self.git_service.deletion_journal.new_batch_id()

        with progress_context as progress:
            task = (
                progress.add_task("Cleaning up...", total=total_steps)
                if progress is not None and total_steps
                else None
            )

            # Remove worktrees first. In dry-run mode, report what would happen
            # without touching worktree directories or Git metadata.
            for wt in worktrees_to_remove:
                message = "Removing worktree..."
                if self.dry_run:
                    self._console_print(f"Would remove worktree at {wt.worktree_path}")
                    removed_worktrees.append(wt.worktree_path)
                    update_progress("Would remove worktree")
                    continue

                is_orphaned = wt.notes and "[ORPHANED]" in wt.notes
                force = is_orphaned or force_mode

                success, error_message = self.git_service.worktree_service.remove_worktree(
                    wt.worktree_path, force=force
                )

                if success:
                    removed_worktrees.append(wt.worktree_path)
                    message = "Removed worktree"
                else:
                    failed_worktrees.append((wt.worktree_path, error_message or "Unknown error"))
                    message = "Failed to remove worktree"
                update_progress(message)

            # Prune worktree metadata to update Git's internal state
            if worktrees_to_remove and not self.dry_run:
                self.git_service.worktree_service.prune_worktrees()

            # Delete branches
            for branch in branches_to_delete:
                message = f"Deleting {branch.name}..."
                reason = format_deletion_reason(branch.status)
                success, error_message = self.delete_branch(
                    branch.name, reason, force_mode=force_mode, batch_id=batch_id
                )

                if success:
                    deleted_branches.append(branch.name)
                    message = f"Deleted {branch.name}"
                else:
                    failed_branches.append((branch.name, error_message or "Unknown error"))
                    message = f"Failed to delete {branch.name}"
                update_progress(message)

        self._emit_operation_progress(
            progress_callback,
            "Complete",
            total_steps,
            total_steps,
            "Cleanup complete",
        )

        return (deleted_branches, failed_branches, removed_worktrees, failed_worktrees)

    def _perform_cleanup(
        self,
        branch_details: list,
        branches_to_delete: Optional[list] = None,
        worktrees_to_remove: Optional[list] = None,
    ) -> None:
        """Delete stale and merged branches and remove worktrees after confirmation."""
        # Use precomputed analysis data when available; otherwise keep the
        # historical behavior for direct/internal callers.
        if branches_to_delete is None:
            branches_to_delete = self.get_deletable_branches(
                branch_details, force_mode=self.force_mode
            )
        if worktrees_to_remove is None:
            worktrees_to_remove = self.get_removable_worktrees(branch_details)

        branches_to_delete = list(branches_to_delete)
        worktrees_to_remove = list(worktrees_to_remove)
        branches_to_delete.extend(
            self.get_branches_unblocked_by_worktree_removal(
                branch_details,
                branches_to_delete=branches_to_delete,
                worktrees_to_remove=worktrees_to_remove,
                force_mode=self.force_mode,
            )
        )

        if not branches_to_delete and not worktrees_to_remove:
            self._console_print("\n[green]No branches or worktrees to clean up![/green]")
            return

        if self.dry_run:
            self._console_print(
                f"\n[yellow]Dry run: found {len(branches_to_delete)} branches and {len(worktrees_to_remove)} worktrees that would be cleaned up[/yellow]"
            )
        else:
            self._console_print(
                f"\n[yellow]Found {len(branches_to_delete)} branches and {len(worktrees_to_remove)} worktrees to clean up[/yellow]"
            )

        # Get confirmation for real deletion if not in force mode.
        # Dry-run is always read-only and should never prompt.
        if not self.force_mode and not self.dry_run:
            if not self._confirm_deletion_with_worktrees(branches_to_delete, worktrees_to_remove):
                self._console_print("[yellow]Cleanup cancelled[/yellow]")
                return

        # Perform the deletion using shared method
        self._console_print("")
        deleted_branches, failed_branches, removed_worktrees, failed_worktrees = (
            self.perform_deletion(
                branches_to_delete,
                worktrees_to_remove,
                force_mode=self.force_mode,
                show_progress=not self.dry_run and not self.verbose and not self.debug_mode,
            )
        )

        # Display results
        for wt_path in removed_worktrees:
            if not self.dry_run:
                self._console_print(f"[green]✓ Removed worktree at {wt_path}[/green]")

        for wt_path, error in failed_worktrees:
            self._console_print(f"[red]✗ Failed to remove worktree at {wt_path}: {error}[/red]")

        total_deleted = len(deleted_branches)
        total_removed = len(removed_worktrees)
        if self.dry_run:
            self._console_print(
                f"\n[green]Dry run complete: would remove {total_removed} worktrees and delete {total_deleted} branches[/green]"
            )
        else:
            self._console_print(
                f"\n[green]Successfully removed {total_removed} worktrees and deleted {total_deleted} branches[/green]"
            )
            if total_deleted > 0:
                self._console_print(
                    "[dim]Deleted branches can be restored with: git-branch-keeper undo[/dim]"
                )

        if failed_branches:
            self._console_print(f"\n[red]Failed to delete {len(failed_branches)} branches:[/red]")
            for branch_name, error in failed_branches:
                self._console_print(f"[red]  • {branch_name}: {error}[/red]")

    def _confirm_deletion(self, branches_to_delete: list) -> bool:
        """Show branches to delete and ask for confirmation."""
        self._console_print("\nThe following branches will be deleted:")
        self._console_print(
            format_deletion_confirmation_items(branches_to_delete, self.delete_remote)
        )

        response = console.input("\nProceed with deletion? [y/N] ")
        return response.lower() == "y"

    def _confirm_deletion_with_worktrees(
        self, branches_to_delete: list, worktrees_to_remove: list
    ) -> bool:
        """Show branches and worktrees to delete/remove and ask for confirmation."""
        if branches_to_delete:
            self._console_print("\nThe following branches will be deleted:")
            self._console_print(
                format_deletion_confirmation_items(branches_to_delete, self.delete_remote)
            )

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

    def _annotate_pr_head_match(self, branch: str, pr_info: Dict) -> None:
        """Add local-tip comparison fields to PR metadata when a PR head SHA is available."""
        head_sha = pr_info.get("head_sha")
        if not head_sha or pr_info.get("local_head_sha"):
            return

        local_head_sha = self.git_service.get_branch_tip_sha(branch)
        pr_info["local_head_sha"] = local_head_sha
        pr_info["head_matches_local"] = bool(local_head_sha and local_head_sha == head_sha)

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

        def append_note(note: str) -> None:
            nonlocal notes
            notes = f"{notes}; {note}" if notes else note

        # Step 1: Determine status based on PR data (if available) or git analysis
        if pr_data and branch in pr_data:
            pr_info = pr_data[branch]
            self._annotate_pr_head_match(branch, pr_info)

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
                    if pr_info.get("head_matches_local", True) is False:
                        number = pr_info.get("number")
                        pr_label = f"PR #{number}" if number else "merged PR"
                        append_note(f"{pr_label} merged but local tip differs from PR head")
                    else:
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

        merge_detection = self.git_service.get_merge_detection_info(branch)

        # Surface squash-detection details in the existing Notes column. Exact
        # patch-id matches may mark the branch merged; fuzzy matches stay advisory.
        if status == BranchStatus.MERGED and merge_detection.get("method") == "squash_patch_id":
            matched_commit = merge_detection.get("matched_commit")
            short_sha = str(matched_commit)[:7] if matched_commit else "unknown"
            append_note(f"squash-merged: exact patch-id match in {short_sha}")
        elif status != BranchStatus.MERGED and self.git_service.is_likely_squash_merged(branch):
            append_note("possible squash-merge - verify before deleting")

        if status != BranchStatus.MERGED and merge_detection.get("truncated"):
            scan_limit = merge_detection.get("scan_limit") or "configured limit"
            append_note(f"squash scan truncated at {scan_limit} commits")

        # Step 2: Get sync status
        sync_status = self.git_service.get_branch_sync_status(branch, self.main_branch)

        # Step 3: Ensure sync_status reflects how merge was detected
        if status == BranchStatus.MERGED:
            # Determine merge method from PR data. A merged PR is authoritative only
            # when the local branch still points at the PR head that GitHub merged.
            if (
                pr_data
                and branch in pr_data
                and pr_data[branch].get("merged")
                and pr_data[branch].get("head_matches_local", True) is not False
            ):
                sync_status = SyncStatus.MERGED_PR.value
            else:
                sync_status = SyncStatus.MERGED_GIT.value

        return status, sync_status, pr_status, notes

    def _process_single_branch(
        self,
        branch: str,
        status_filter: str,
        pr_data: Optional[Dict[str, Dict]],
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
        # Fetch PR metadata as part of this branch's work item so the single
        # processing progress bar covers both network and local Git analysis.
        branch_pr_data = pr_data
        if branch_pr_data is None:
            branch_pr_data = self.github_service.get_pr_data_for_branch(branch)

        # Use consolidated method to determine status
        status, sync_status, pr_status_str, notes = self._determine_branch_status(
            branch, branch_pr_data
        )
        merge_detection = self.git_service.get_merge_detection_info(branch)

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
            elif all(key in status_details for key in ("modified", "untracked", "staged")):
                modified_files = status_details["modified"]
                untracked_files = status_details["untracked"]
                staged_files = status_details["staged"]
            else:
                status_error = "Status check returned incomplete data"
                logger.debug(f"[CORE] Incomplete status data for {branch}: {status_details}")
                modified_files = None
                untracked_files = None
                staged_files = None
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
            pr_details=(
                branch_pr_data.get(branch) if branch_pr_data and branch in branch_pr_data else None
            ),
            notes=final_notes,
            in_worktree=in_worktree,
            worktree_path=worktree_path_for_details,  # Store worktree path for branches in worktrees
            merge_detection=merge_detection,
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
