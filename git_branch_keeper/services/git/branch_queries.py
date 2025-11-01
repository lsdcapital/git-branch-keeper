"""Branch query service for git-branch-keeper."""

import git
import os
import re
from datetime import datetime, timezone
from typing import Optional, List, Union, TYPE_CHECKING

from git_branch_keeper.models.branch import SyncStatus
from git_branch_keeper.services.git.worktrees import WorktreeService
from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config
    from git_branch_keeper.services.git.merge_detector import MergeDetector

logger = get_logger(__name__)


class BranchQueries:
    """Service for querying branch information."""

    def __init__(
        self,
        repo_path: str,
        config: Union["Config", dict],
        merge_detector: "MergeDetector",
    ):
        """Initialize the branch queries service.

        Args:
            repo_path: Path to the git repository
            config: Configuration dictionary or Config object
            merge_detector: MergeDetector instance for merge checks (dependency injection)
        """
        self.repo_path = repo_path
        self.config = config
        self.merge_detector = merge_detector
        self.remote_name = "origin"
        self.worktree_service = WorktreeService(repo_path)
        self.in_git_operation = False

        logger.debug("Branch queries service initialized")

    def _get_repo(self):
        """Get a thread-safe git.Repo instance.

        Creates a new repo instance for each call to ensure thread safety.
        GitPython repos are lightweight - they don't clone, just open the existing repo.

        Returns:
            git.Repo: A fresh repository instance
        """
        return git.Repo(self.repo_path)

    def has_remote_branch(self, branch_name: str) -> bool:
        """Check if the branch has a remote tracking branch."""
        try:
            repo = self._get_repo()
            remote = repo.remote(self.remote_name)

            # First check if the remote ref exists
            remote_ref_name = f"origin/{branch_name}"
            if remote_ref_name not in [ref.name for ref in remote.refs]:
                return False

            # Then try to get the remote branch
            try:
                repo.refs[f"origin/{branch_name}"]
                return True
            except (IndexError, KeyError):
                return False
        except Exception as e:
            logger.debug(f"Error checking remote branch {branch_name}: {e}")
            return False

    def get_branch_age(self, branch_name: str) -> int:
        """Get age of branch in calendar days."""
        try:
            repo = self._get_repo()
            commit = repo.refs[branch_name].commit
            commit_time = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            now = datetime.now(timezone.utc)

            # Calculate age based on calendar dates for consistent results
            commit_date = commit_time.date()
            today = now.date()
            age = (today - commit_date).days

            return age
        except Exception as e:
            logger.debug(f"Error getting branch age for {branch_name}: {e}")
            return 0

    def get_branch_sync_status(self, branch_name: str, main_branch: str) -> str:
        """Get sync status of branch with remote."""
        try:
            repo = self._get_repo()

            # Main branch cannot be merged into itself - skip merge checks
            if branch_name == main_branch:
                if not self.has_remote_branch(branch_name):
                    return SyncStatus.LOCAL_ONLY.value

                # For main branch, just check sync status with remote
                ahead = list(repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
                behind = list(repo.iter_commits(f"{branch_name}..origin/{branch_name}"))

                if ahead and behind:
                    return SyncStatus.DIVERGED.value
                elif ahead:
                    return f"ahead {len(ahead)}"
                elif behind:
                    return f"behind {len(behind)}"
                else:
                    return SyncStatus.SYNCED.value

            # Skip merge checks for protected branches
            if branch_name in self.config.get("protected_branches", ["main", "master"]):
                if not self.has_remote_branch(branch_name):
                    return SyncStatus.LOCAL_ONLY.value

                # For protected branches, just check sync status
                ahead = list(repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
                behind = list(repo.iter_commits(f"{branch_name}..origin/{branch_name}"))

                if ahead and behind:
                    return SyncStatus.DIVERGED.value
                elif ahead:
                    return f"ahead {len(ahead)}"
                elif behind:
                    return f"behind {len(behind)}"
                else:
                    return SyncStatus.SYNCED.value

            # Check if branch is merged into main (via dependency injection)
            if self.merge_detector.is_branch_merged(branch_name, main_branch):
                return SyncStatus.MERGED_GIT.value

            # Check if branch exists on remote
            if not self.has_remote_branch(branch_name):
                return SyncStatus.LOCAL_ONLY.value

            # Check ahead/behind status
            ahead = list(repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
            behind = list(repo.iter_commits(f"{branch_name}..origin/{branch_name}"))

            if ahead and behind:
                return SyncStatus.DIVERGED.value
            elif ahead:
                return f"ahead {len(ahead)}"
            elif behind:
                return f"behind {len(behind)}"
            else:
                return SyncStatus.SYNCED.value
        except Exception as e:
            logger.debug(f"Error checking sync status for {branch_name}: {e}")
            return SyncStatus.LOCAL_ONLY.value  # Return local-only instead of unknown for better UX

    def get_last_commit_date(self, branch_name: str) -> str:
        """Get the date of the last commit on a branch."""
        try:
            repo = self._get_repo()
            commit = repo.refs[branch_name].commit
            dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception as e:
            logger.debug(f"Error getting last commit date for {branch_name}: {e}")
            return "unknown"

    def get_branch_status_details(self, branch_name: str) -> dict:
        """Get detailed status of a branch.

        Uses temporary git worktrees for safe, isolated branch checking.
        This prevents uncommitted changes from being carried between branches.

        Returns:
            dict with keys: modified, untracked, staged (all bool)
            On error: dict with key 'error' containing error message
        """
        self.in_git_operation = True
        try:
            repo = self._get_repo()

            # Check if this is the current branch
            try:
                current_branch = repo.active_branch.name
                is_current = branch_name == current_branch
            except TypeError:
                # Detached HEAD
                is_current = False

            # Check if branch is in a worktree (but not the current branch)
            worktree_infos = self.worktree_service.get_worktree_info()
            worktree_info = next(
                (wt for wt in worktree_infos if wt.branch_name == branch_name and not wt.is_main),
                None,
            )
            if worktree_info and not is_current:
                logger.debug(
                    f"Branch {branch_name} is in another worktree, using existing worktree"
                )
                return self.worktree_service.get_worktree_status_details(worktree_info.path)

            # For current branch, check status directly without checkout
            if is_current:
                logger.debug(f"Checking current branch {branch_name} directly")
                status = repo.git.status("--porcelain")

                # Parse porcelain format: XY filename
                # X = index status (first char), Y = working tree status (second char)
                has_modified = False
                has_untracked = False
                has_staged = False

                for line in status.split("\n"):
                    if not line:
                        continue
                    if len(line) < 2:
                        continue

                    index_status = line[0]  # Staged changes
                    worktree_status = line[1]  # Working tree changes

                    # Untracked files
                    if line.startswith("??"):
                        has_untracked = True
                        continue

                    # Check for staged changes (index status is not space)
                    if index_status != " ":
                        has_staged = True

                    # Check for working tree changes (worktree status is not space)
                    if worktree_status != " ":
                        has_modified = True

                return {
                    "modified": has_modified,
                    "untracked": has_untracked,
                    "staged": has_staged,
                }
            else:
                # Not current - use temporary worktree (safe, isolated)
                logger.debug(f"Checking branch {branch_name} using temporary worktree")
                with self.worktree_service.create_temporary_worktree(branch_name) as temp_dir:
                    # Get status from the worktree (without checking out current branch)
                    status = repo.git.execute(["git", "-C", temp_dir, "status", "--porcelain"])

                    # Parse porcelain format: XY filename
                    # X = index status (first char), Y = working tree status (second char)
                    has_modified = False
                    has_untracked = False
                    has_staged = False

                    for line in status.split("\n"):
                        if not line:
                            continue
                        if len(line) < 2:
                            continue

                        index_status = line[0]  # Staged changes
                        worktree_status = line[1]  # Working tree changes

                        # Untracked files
                        if line.startswith("??"):
                            has_untracked = True
                            continue

                        # Check for staged changes (index status is not space)
                        if index_status != " ":
                            has_staged = True

                        # Check for working tree changes (worktree status is not space)
                        if worktree_status != " ":
                            has_modified = True

                    return {
                        "modified": has_modified,
                        "untracked": has_untracked,
                        "staged": has_staged,
                    }

        except git.exc.GitCommandError as e:
            # Extract detailed error information from GitCommandError
            command = e.command if hasattr(e, "command") else "git"
            stderr = (e.stderr if hasattr(e, "stderr") else str(e)).strip()
            status = e.status if hasattr(e, "status") else "unknown"

            # Build informative error message
            if stderr:
                error_msg = f"'{command}' failed (exit {status}): {stderr}"
            else:
                error_msg = f"'{command}' failed with exit code {status}"

            logger.warning(f"Could not check branch status for {branch_name}: {error_msg}")
            # Return error info instead of raising
            return {"error": f"Git error: {error_msg}"}
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Could not check branch status for {branch_name}: {error_msg}")
            # Return error info instead of raising
            return {"error": f"Status check failed: {error_msg}"}
        finally:
            self.in_git_operation = False

    def get_file_status_detailed(
        self, branch_name: Optional[str] = None, worktree_path: Optional[str] = None
    ) -> dict:
        """Get detailed file status with actual file lists.

        Args:
            branch_name: Branch to check (if None, uses current branch)
            worktree_path: Worktree path to check (alternative to branch_name)

        Returns:
            Dict with 'modified', 'untracked', 'staged' lists of file paths
        """
        try:
            repo = self._get_repo()

            # Get status output
            if worktree_path:
                if not os.path.exists(worktree_path):
                    return {"modified": [], "untracked": [], "staged": []}
                status = repo.git.execute(["git", "-C", worktree_path, "status", "--porcelain"])
            elif branch_name:
                # Check if this is the current branch
                try:
                    current_branch = repo.active_branch.name
                    is_current = branch_name == current_branch
                except TypeError:
                    is_current = False

                if not is_current:
                    # Check if in worktree
                    worktree_branches = self.worktree_service.get_worktree_branches()
                    if branch_name in worktree_branches:
                        return {"modified": [], "untracked": [], "staged": []}

                    # Use temporary worktree for safe, isolated checking
                    with self.worktree_service.create_temporary_worktree(branch_name) as temp_dir:
                        status = repo.git.execute(["git", "-C", temp_dir, "status", "--porcelain"])
                else:
                    status = repo.git.status("--porcelain")
            else:
                status = repo.git.status("--porcelain")

            # Parse status into file lists
            modified = []
            untracked = []
            staged = []

            for line in status.split("\n"):
                if not line.strip():
                    continue

                # Parse git status --porcelain format
                # Format: XY filename (X=index, Y=worktree)
                # Examples:
                #  M file.txt (modified in worktree)
                # M  file.txt (staged)
                # MM file.txt (staged and modified)
                # ?? file.txt (untracked)
                if len(line) >= 3:
                    index_status = line[0]
                    worktree_status = line[1]
                    filename = line[3:]

                    # Staged files (index has changes)
                    if index_status in ["M", "A", "D", "R", "C"]:
                        staged.append(filename)

                    # Modified files (worktree has changes)
                    if worktree_status in ["M", "D"]:
                        modified.append(filename)

                    # Untracked files
                    if index_status == "?" and worktree_status == "?":
                        untracked.append(filename)

            return {"modified": modified, "untracked": untracked, "staged": staged}

        except Exception as e:
            logger.warning(f"Could not get detailed file status: {e}")
            return {"modified": [], "untracked": [], "staged": []}

    def get_diff(
        self,
        branch_name: Optional[str] = None,
        worktree_path: Optional[str] = None,
        staged: bool = False,
    ) -> str:
        """Get diff output for a branch or worktree.

        Args:
            branch_name: Branch to diff (if None, uses current branch)
            worktree_path: Worktree path to diff (alternative to branch_name)
            staged: If True, show staged changes; if False, show unstaged changes

        Returns:
            Git diff output as string
        """
        try:
            repo = self._get_repo()

            if worktree_path:
                if not os.path.exists(worktree_path):
                    return "Worktree directory not found (orphaned)"

                # Get diff from worktree
                if staged:
                    diff = repo.git.execute(["git", "-C", worktree_path, "diff", "--cached"])
                else:
                    diff = repo.git.execute(["git", "-C", worktree_path, "diff"])
                return diff or "No changes"

            elif branch_name:
                # Check if this is the current branch
                try:
                    current_branch = repo.active_branch.name
                    is_current = branch_name == current_branch
                except TypeError:
                    is_current = False

                if not is_current:
                    # Check if in worktree
                    worktree_branches = self.worktree_service.get_worktree_branches()
                    if branch_name in worktree_branches:
                        return "Branch is in another worktree"

                    # Use temporary worktree for safe, isolated diff
                    with self.worktree_service.create_temporary_worktree(branch_name) as temp_dir:
                        if staged:
                            diff = repo.git.execute(["git", "-C", temp_dir, "diff", "--cached"])
                        else:
                            diff = repo.git.execute(["git", "-C", temp_dir, "diff"])
                else:
                    if staged:
                        diff = repo.git.diff("--cached")
                    else:
                        diff = repo.git.diff()
            else:
                if staged:
                    diff = repo.git.diff("--cached")
                else:
                    diff = repo.git.diff()

            return diff or "No changes"

        except Exception as e:
            logger.warning(f"Could not get diff: {e}")
            return f"Error getting diff: {e}"

    def get_branch_commits(self, branch_name: str, main_branch: str, limit: int = 20) -> List[dict]:
        """Get list of commits unique to a branch.

        Args:
            branch_name: Branch to get commits from
            main_branch: Main branch to compare against
            limit: Maximum number of commits to return

        Returns:
            List of commit dicts with sha, message, author, date
        """
        try:
            repo = self._get_repo()

            # Get commits on branch not on main
            commits = []
            for commit in repo.iter_commits(f"{main_branch}..{branch_name}", max_count=limit):
                commits.append(
                    {
                        "sha": commit.hexsha[:7],
                        "message": commit.message.strip().split("\n")[0],  # First line only
                        "author": commit.author.name,
                        "date": datetime.fromtimestamp(
                            commit.committed_date, tz=timezone.utc
                        ).strftime("%Y-%m-%d %H:%M"),
                    }
                )

            return commits

        except Exception as e:
            logger.warning(f"Could not get branch commits: {e}")
            return []

    def get_merge_details(self, branch_name: str, main_branch: str) -> dict:
        """Get detailed information about how/when a branch was merged.

        Args:
            branch_name: Branch to check
            main_branch: Main branch to check against

        Returns:
            Dict with merge information
        """
        try:
            repo = self._get_repo()

            # Find merge commit in main branch
            merge_patterns = [
                f"Merge branch '{branch_name}'",
                f"Merge pull request .* from .*/{branch_name}",
                f"Merge pull request .* from .*:{branch_name}",
            ]

            for commit in repo.iter_commits(main_branch, max_count=200):
                message = (
                    commit.message
                    if isinstance(commit.message, str)
                    else commit.message.decode("utf-8", errors="ignore")
                )

                for pattern in merge_patterns:
                    if re.search(pattern, message):
                        return {
                            "found": True,
                            "merge_sha": commit.hexsha[:7],
                            "merge_message": message.strip().split("\n")[0],
                            "merge_author": commit.author.name,
                            "merge_date": datetime.fromtimestamp(
                                commit.committed_date, tz=timezone.utc
                            ).strftime("%Y-%m-%d %H:%M"),
                        }

            # If no merge commit found, might be fast-forward or squash
            return {
                "found": False,
                "message": "Merged via fast-forward or squash (no explicit merge commit found)",
            }

        except Exception as e:
            logger.warning(f"Could not get merge details: {e}")
            return {"found": False, "message": f"Error: {e}"}

    def get_divergence_info(self, branch_name: str, main_branch: str) -> dict:
        """Get ahead/behind information for a branch vs main.

        Args:
            branch_name: Branch to check
            main_branch: Main branch to compare against

        Returns:
            Dict with ahead/behind counts and commit lists
        """
        try:
            repo = self._get_repo()

            # Get commits ahead (on branch but not on main)
            ahead_commits = []
            for commit in repo.iter_commits(f"{main_branch}..{branch_name}", max_count=10):
                ahead_commits.append(
                    {
                        "sha": commit.hexsha[:7],
                        "message": commit.message.strip().split("\n")[0],
                        "date": datetime.fromtimestamp(
                            commit.committed_date, tz=timezone.utc
                        ).strftime("%Y-%m-%d"),
                    }
                )

            # Get commits behind (on main but not on branch)
            behind_commits = []
            for commit in repo.iter_commits(f"{branch_name}..{main_branch}", max_count=10):
                behind_commits.append(
                    {
                        "sha": commit.hexsha[:7],
                        "message": commit.message.strip().split("\n")[0],
                        "date": datetime.fromtimestamp(
                            commit.committed_date, tz=timezone.utc
                        ).strftime("%Y-%m-%d"),
                    }
                )

            return {
                "ahead": len(ahead_commits),
                "behind": len(behind_commits),
                "ahead_commits": ahead_commits,
                "behind_commits": behind_commits,
            }

        except Exception as e:
            logger.warning(f"Could not get divergence info: {e}")
            return {"ahead": 0, "behind": 0, "ahead_commits": [], "behind_commits": []}
