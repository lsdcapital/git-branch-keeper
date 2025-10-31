"""Git operations service"""

import git
import os
from datetime import datetime, timezone
from contextlib import contextmanager
from rich.console import Console
from typing import Union, TYPE_CHECKING, Dict, Optional
import re
from threading import Lock

from git_branch_keeper.models.branch import SyncStatus
from git_branch_keeper.services.git.worktrees import WorktreeService
from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)


class GitOperations:
    """Service for Git operations."""

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
        self._merge_status_cache: Dict[str, bool] = {}  # Cache for merge status checks
        self._cache_lock = Lock()  # Thread safety for cache access
        # Add counters for merge detection methods
        self.merge_detection_stats = {
            "method0": 0,  # Squash merge detection
            "method1": 0,  # Fast rev-list
            "method2": 0,  # Ancestor check
            "method3": 0,  # Commit message search
            "method4": 0,  # All commits exist
        }
        self._stats_lock = Lock()  # Thread safety for stats access

        # Initialize worktree service
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

    def _get_from_cache(self, key: str):
        """Thread-safe cache read."""
        with self._cache_lock:
            return self._merge_status_cache.get(key)

    def _set_in_cache(self, key: str, value: bool):
        """Thread-safe cache write."""
        with self._cache_lock:
            self._merge_status_cache[key] = value

    def _check_cache(self, key: str) -> tuple[bool, bool]:
        """Thread-safe cache check. Returns (found, value)."""
        with self._cache_lock:
            if key in self._merge_status_cache:
                return (True, self._merge_status_cache[key])
            return (False, False)

    def _increment_stat(self, method: str):
        """Thread-safe stats increment."""
        with self._stats_lock:
            self.merge_detection_stats[method] += 1

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

            # Check if branch is merged into main
            if self.is_branch_merged(branch_name, main_branch):
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
        with self._git_operation():
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
                    (
                        wt
                        for wt in worktree_infos
                        if wt.branch_name == branch_name and not wt.is_main
                    ),
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

                    return {
                        "modified": bool(
                            [line for line in status.split("\n") if line.startswith(" M")]
                        ),
                        "untracked": bool(
                            [line for line in status.split("\n") if line.startswith("??")]
                        ),
                        "staged": bool(
                            [line for line in status.split("\n") if line.startswith("M ")]
                        ),
                    }
                else:
                    # Not current - use temporary worktree (safe, isolated)
                    logger.debug(f"Checking branch {branch_name} using temporary worktree")
                    with self.worktree_service.create_temporary_worktree(branch_name) as temp_dir:
                        # Get status from the worktree (without checking out current branch)
                        status = repo.git.execute(["git", "-C", temp_dir, "status", "--porcelain"])

                        return {
                            "modified": bool(
                                [line for line in status.split("\n") if line.startswith(" M")]
                            ),
                            "untracked": bool(
                                [line for line in status.split("\n") if line.startswith("??")]
                            ),
                            "staged": bool(
                                [line for line in status.split("\n") if line.startswith("M ")]
                            ),
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

    def get_branch_commits(self, branch_name: str, main_branch: str, limit: int = 20) -> list[dict]:
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

    def is_tag(self, ref_name: str) -> bool:
        """Check if a reference is a tag."""
        try:
            repo = self._get_repo()
            # Strip refs/tags/ prefix if present
            tag_name = ref_name.replace("refs/tags/", "")
            return tag_name in [tag.name for tag in repo.tags]
        except Exception as e:
            logger.debug(f"Error checking if {ref_name} is a tag: {e}")
            return False

    def get_merge_stats(self) -> str:
        """Get a summary of which methods detected merges."""
        total = sum(self.merge_detection_stats.values())
        if total == 0:
            return "No merges detected"

        stats = []
        method_names = {
            "method0": "Squash merge",
            "method1": "Fast rev-list",
            "method2": "Tip reachable",
            "method3": "Merge commit",
            "method4": "Ancestor check",
        }

        for method, count in self.merge_detection_stats.items():
            if count > 0:
                stats.append(f"{method_names[method]}: {count}")

        return f"Merges detected by: {', '.join(stats)}"

    def is_branch_merged(self, branch_name: str, main_branch: str) -> bool:
        """Check if a branch is merged using multiple methods, ordered by speed."""
        # A branch cannot be merged into itself
        if branch_name == main_branch:
            logger.debug(f"Skipping merge check: {branch_name} is the main branch")
            return False

        # Check cache first (thread-safe)
        cache_key = f"{branch_name}:{main_branch}"
        found, value = self._check_cache(cache_key)
        if found:
            return value

        try:
            # Skip if it's a tag
            if self.is_tag(branch_name):
                logger.debug(f"Skipping tag: {branch_name}")
                self._set_in_cache(cache_key, False)
                return False

            # Try each detection method in order (fastest first)
            methods = [
                self._check_squash_merge,
                self._check_remote_deletion,
                self._check_fast_revlist,
                self._check_ancestor,
                self._check_merge_commit_message,
                self._check_full_commit_history,
            ]

            for method in methods:
                result = method(branch_name, main_branch)
                if result:
                    self._set_in_cache(cache_key, True)
                    return True

            self._set_in_cache(cache_key, False)
            return False
        except Exception as e:
            logger.debug(f"Error checking if branch is merged: {e}")
            self._set_in_cache(cache_key, False)
            return False

    def _check_squash_merge(self, branch_name: str, main_branch: str) -> bool:
        """Method 0: Check for squash merge by comparing diffs."""
        logger.debug("[Method 0] Checking for squash merge...")
        try:
            repo = self._get_repo()
            # Get all commits on the branch that aren't on main
            branch_commits = list(repo.iter_commits(f"{main_branch}..{branch_name}"))
            if not branch_commits:
                return False

            # Get the combined diff of all branch commits
            branch_diff = repo.git.diff(f"{main_branch}...{branch_name}", "--no-color")

            if not branch_diff:
                return False

            # Search recent commits in main for similar changes
            for commit in repo.iter_commits(main_branch, max_count=100):
                try:
                    commit_diff = repo.git.show(commit.hexsha, "--no-color", "--format=")

                    # If the branch diff is contained in the commit diff, likely a squash merge
                    if len(branch_diff) > 50 and branch_diff in commit_diff:
                        logger.debug(f"[Method 0] Found squash merge in commit {commit.hexsha}")
                        self._increment_stat("method0")
                        return True
                except Exception as e:
                    logger.debug(f"[Method 0] Error processing commit {commit.hexsha}: {e}")
                    continue
        except git.exc.GitCommandError as e:
            logger.debug(f"[Method 0] Error checking squash merge: {e}")

        return False

    def _check_remote_deletion(self, branch_name: str, main_branch: str) -> bool:
        """Method 0.5: Check if branch was deleted on remote (hint only)."""
        logger.debug("[Method 0.5] Checking if branch was deleted on remote...")
        try:
            repo = self._get_repo()
            if not self.has_remote_branch(branch_name):
                # Check if it ever existed on remote
                try:
                    tracking = repo.git.config("--get", f"branch.{branch_name}.merge")
                    if tracking:
                        logger.debug(
                            f"[Method 0.5] Branch {branch_name} was tracking remote but remote is gone"
                        )
                        # This is a hint, not definitive - continue to other methods
                except Exception as e:
                    logger.debug(f"[Method 0.5] Error getting tracking info: {e}")
        except Exception as e:
            logger.debug(f"[Method 0.5] Error checking remote branch: {e}")

        return False  # Not a definitive check

    def _check_fast_revlist(self, branch_name: str, main_branch: str) -> bool:
        """Method 1: Fast check using rev-list."""
        logger.debug("[Method 1] Using fast rev-list check...")
        try:
            repo = self._get_repo()
            result = repo.git.rev_list("--count", f"{main_branch}..{branch_name}")
            if result == "0":
                logger.debug(f"[Method 1] Branch {branch_name} is merged (fast rev-list)")
                self._increment_stat("method1")
                return True
        except git.exc.GitCommandError:
            pass

        return False

    def _check_ancestor(self, branch_name: str, main_branch: str) -> bool:
        """Method 2: Check if branch tip is ancestor of main."""
        logger.debug("[Method 2] Checking if branch tip is ancestor...")
        try:
            repo = self._get_repo()
            branch_tip = repo.refs[branch_name].commit
            is_ancestor = repo.is_ancestor(branch_tip, repo.refs[main_branch].commit)
            if is_ancestor:
                logger.debug(f"[Method 2] Branch {branch_name} is merged (tip is ancestor)")
                self._increment_stat("method2")
                return True
        except Exception as e:
            logger.debug(f"[Method 2] Error checking ancestor: {e}")

        return False

    def _check_merge_commit_message(self, branch_name: str, main_branch: str) -> bool:
        """Method 3: Check merge commit messages."""
        logger.debug("[Method 3] Checking merge commit messages...")
        merge_patterns = [
            f"Merge branch '{branch_name}'",
            f"Merge pull request .* from .*/{branch_name}",
            f"Merge pull request .* from .*:{branch_name}",
        ]

        repo = self._get_repo()
        for commit in repo.iter_commits(main_branch, max_count=100):
            # Ensure message is a string (GitPython can return bytes)
            message = (
                commit.message
                if isinstance(commit.message, str)
                else commit.message.decode("utf-8", errors="ignore")
            )
            for pattern in merge_patterns:
                if re.search(pattern, message):
                    logger.debug(f"[Method 3] Found merge commit: {message.splitlines()[0]}")
                    self._increment_stat("method3")
                    return True

        return False

    def _check_full_commit_history(self, branch_name: str, main_branch: str) -> bool:
        """Method 4: Full commit history check (slowest)."""
        logger.debug("[Method 4] Checking full commit history...")
        try:
            repo = self._get_repo()
            repo = self._get_repo()
            branch_commit_set = set(repo.git.rev_list(branch_name).split())
            main_commit_set = set(repo.git.rev_list(main_branch).split())
            if branch_commit_set.issubset(main_commit_set):
                logger.debug(f"[Method 4] Branch {branch_name} is merged (all commits in main)")
                self._increment_stat("method4")
                return True
        except Exception as e:
            logger.debug(f"[Method 4] Error checking commit history: {e}")

        return False

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
