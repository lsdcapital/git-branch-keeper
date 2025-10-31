"""Git operations service - Facade for git operations."""

import git
from contextlib import contextmanager
from rich.console import Console
from typing import Union, TYPE_CHECKING, Optional, List

from git_branch_keeper.services.git.merge_detector import MergeDetector
from git_branch_keeper.services.git.branch_queries import BranchQueries
from git_branch_keeper.services.git.worktrees import WorktreeService
from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)


class GitOperations:
    """Facade for Git operations, composing specialized services."""

    def __init__(self, repo_path: str, config: Union["Config", dict]):
        """Initialize the service.

        Args:
            repo_path: Path to the git repository (string path, not repo object)
            config: Configuration dictionary or Config object
        """
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get("verbose", False)
        self.debug_mode = config.get("debug", False)
        self.remote_name = "origin"  # Store remote name, not object
        self.in_git_operation = False  # Track if operation is in progress

        # Compose specialized services (Dependency Injection pattern)
        self.merge_detector = MergeDetector(repo_path, config)
        self.branch_queries = BranchQueries(repo_path, config, self.merge_detector)
        self.worktree_service = WorktreeService(repo_path)

        logger.info("Git operations initialized")

    def _get_repo(self):
        """Get a thread-safe git.Repo instance.

        Creates a new repo instance for each call to ensure thread safety.
        GitPython repos are lightweight - they don't clone, just open the existing repo.

        Returns:
            git.Repo: A fresh repository instance
        """
        return git.Repo(self.repo_path)

    @contextmanager
    def _git_operation(self):
        """Context manager to track git operations."""
        self.in_git_operation = True
        try:
            yield
        finally:
            self.in_git_operation = False

    # ============================================================================
    # Delegation methods to MergeDetector
    # ============================================================================

    def is_branch_merged(self, branch_name: str, main_branch: str) -> bool:
        """Check if a branch is merged. Delegates to MergeDetector."""
        return self.merge_detector.is_branch_merged(branch_name, main_branch)

    def get_merge_stats(self) -> str:
        """Get merge detection statistics. Delegates to MergeDetector."""
        return self.merge_detector.get_merge_stats()

    # ============================================================================
    # Delegation methods to BranchQueries
    # ============================================================================

    def has_remote_branch(self, branch_name: str) -> bool:
        """Check if branch has a remote. Delegates to BranchQueries."""
        return self.branch_queries.has_remote_branch(branch_name)

    def get_branch_age(self, branch_name: str) -> int:
        """Get branch age. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_age(branch_name)

    def get_branch_sync_status(self, branch_name: str, main_branch: str) -> str:
        """Get branch sync status. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_sync_status(branch_name, main_branch)

    def get_last_commit_date(self, branch_name: str) -> str:
        """Get last commit date. Delegates to BranchQueries."""
        return self.branch_queries.get_last_commit_date(branch_name)

    def get_branch_status_details(self, branch_name: str) -> dict:
        """Get branch status details. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_status_details(branch_name)

    def get_file_status_detailed(
        self, branch_name: Optional[str] = None, worktree_path: Optional[str] = None
    ) -> dict:
        """Get detailed file status. Delegates to BranchQueries."""
        return self.branch_queries.get_file_status_detailed(branch_name, worktree_path)

    def get_diff(
        self,
        branch_name: Optional[str] = None,
        worktree_path: Optional[str] = None,
        staged: bool = False,
    ) -> str:
        """Get diff output. Delegates to BranchQueries."""
        return self.branch_queries.get_diff(branch_name, worktree_path, staged)

    def get_branch_commits(self, branch_name: str, main_branch: str, limit: int = 20) -> List[dict]:
        """Get branch commits. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_commits(branch_name, main_branch, limit)

    def get_merge_details(self, branch_name: str, main_branch: str) -> dict:
        """Get merge details. Delegates to BranchQueries."""
        return self.branch_queries.get_merge_details(branch_name, main_branch)

    def get_divergence_info(self, branch_name: str, main_branch: str) -> dict:
        """Get divergence info. Delegates to BranchQueries."""
        return self.branch_queries.get_divergence_info(branch_name, main_branch)

    def is_tag(self, ref_name: str) -> bool:
        """Check if ref is a tag. Delegates to MergeDetector."""
        return self.merge_detector.is_tag(ref_name)

    # ============================================================================
    # Core Git Operations (not delegated)
    # ============================================================================

    def stash_changes(self) -> bool:
        """Stash uncommitted changes temporarily.

        Returns:
            bool: True if changes were stashed, False if nothing to stash
        """
        try:
            repo = self._get_repo()
            # Check if there's anything to stash
            status = repo.git.status("--porcelain")
            if not status.strip():
                logger.debug("No uncommitted changes to stash")
                return False

            # Stash with untracked files
            repo.git.stash("push", "-u", "-m", "git-branch-keeper-temp")
            logger.debug("Stashed uncommitted changes")
            return True
        except Exception as e:
            logger.warning(f"Could not stash changes: {e}")
            raise

    def restore_stashed_changes(self, was_stashed: bool) -> None:
        """Restore previously stashed changes.

        Args:
            was_stashed: Whether changes were actually stashed (from stash_changes return value)
        """
        if not was_stashed:
            logger.debug("Nothing was stashed, skipping restore")
            return

        try:
            repo = self._get_repo()
            repo.git.stash("pop")
            logger.debug("Restored stashed changes")
        except Exception as e:
            logger.warning(f"Could not restore stashed changes: {e}")
            logger.warning("Your changes are still in the stash. Run 'git stash pop' manually.")
            raise

    def delete_branch(self, branch_name: str, dry_run: bool = False) -> bool:
        """Delete a branch locally and remotely if it exists."""
        with self._git_operation():
            try:
                repo = self._get_repo()

                # Check if remote branch exists before deletion
                has_remote = self.has_remote_branch(branch_name)

                # Delete local branch
                if not dry_run:
                    console.print(f"Deleting local branch {branch_name}...")
                    repo.delete_head(branch_name, force=True)

                # Delete remote branch if it exists
                if has_remote:
                    if not dry_run:
                        try:
                            # Only get remote when we need to push
                            remote = repo.remote(self.remote_name)
                            console.print(f"Deleting remote branch {branch_name}...")
                            remote.push(refspec=f":{branch_name}")
                            console.print(
                                f"[green]Deleted branch {branch_name} (local and remote)[/green]"
                            )
                        except git.exc.GitCommandError as e:
                            # Check if it's a protected branch error
                            if "protected" in str(e).lower() or "prohibited" in str(e).lower():
                                console.print(
                                    f"[yellow]Warning: Remote branch {branch_name} is protected and cannot be deleted remotely[/yellow]"
                                )
                                console.print(
                                    f"[green]Deleted local branch {branch_name} only[/green]"
                                )
                            else:
                                # Re-raise if it's a different error
                                raise
                    else:
                        console.print(
                            f"[yellow]Would delete branch {branch_name} (local and remote)[/yellow]"
                        )
                else:
                    if not dry_run:
                        console.print(f"[green]Deleted branch {branch_name} (local only)[/green]")
                    else:
                        console.print(
                            f"[yellow]Would delete branch {branch_name} (local only)[/yellow]"
                        )

                return True

            except Exception as e:
                console.print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
                return False
