"""Merge detection service for git-branch-keeper."""

import git
import re
from threading import Lock
from typing import Dict, Union, TYPE_CHECKING

from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

logger = get_logger(__name__)


class MergeDetector:
    """Service for detecting if branches have been merged."""

    def __init__(self, repo_path: str, config: Union["Config", dict]):
        """Initialize the merge detector.

        Args:
            repo_path: Path to the git repository
            config: Configuration dictionary or Config object
        """
        self.repo_path = repo_path
        self.config = config
        self.debug_mode = config.get("debug", False)
        self._merge_status_cache: Dict[str, bool] = {}  # Cache for merge status checks
        self._cache_lock = Lock()  # Thread safety for cache access
        self._main_branch_sha_cache: Dict[str, str] = (
            {}
        )  # Track main branch SHA for cache invalidation
        # Add counters for merge detection methods
        self.merge_detection_stats = {
            "method0": 0,  # Squash merge detection
            "method1": 0,  # Fast rev-list
            "method2": 0,  # Ancestor check
            "method3": 0,  # Commit message search
            "method4": 0,  # All commits exist
        }
        self._stats_lock = Lock()  # Thread safety for stats access

        logger.debug("Merge detector initialized")

    def _get_repo(self):
        """Get a thread-safe git.Repo instance.

        Creates a new repo instance for each call to ensure thread safety.
        GitPython repos are lightweight - they don't clone, just open the existing repo.

        Returns:
            git.Repo: A fresh repository instance
        """
        return git.Repo(self.repo_path)

    def _check_cache(self, key: str) -> tuple[bool, bool]:
        """Thread-safe cache check. Returns (found, value)."""
        with self._cache_lock:
            if key in self._merge_status_cache:
                return (True, self._merge_status_cache[key])
            return (False, False)

    def _set_in_cache(self, key: str, value: bool):
        """Thread-safe cache write."""
        with self._cache_lock:
            self._merge_status_cache[key] = value

    def _increment_stat(self, method: str):
        """Thread-safe stats increment."""
        with self._stats_lock:
            self.merge_detection_stats[method] += 1

    def _get_main_branch_sha(self, main_branch: str) -> str:
        """Get the current SHA of the main branch."""
        try:
            repo = self._get_repo()
            return repo.refs[main_branch].commit.hexsha
        except Exception as e:
            logger.debug(f"Error getting main branch SHA: {e}")
            return ""

    def _invalidate_cache_if_needed(self, main_branch: str):
        """Invalidate cache if main branch has changed."""
        current_sha = self._get_main_branch_sha(main_branch)
        if not current_sha:
            return

        with self._cache_lock:
            cached_sha = self._main_branch_sha_cache.get(main_branch)
            if cached_sha and cached_sha != current_sha:
                # Main branch has changed, invalidate all cached merge statuses
                logger.debug(
                    f"Main branch {main_branch} changed ({cached_sha[:7]} -> {current_sha[:7]}), invalidating cache"
                )
                self._merge_status_cache.clear()
            # Update cached SHA
            self._main_branch_sha_cache[main_branch] = current_sha

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

        # Invalidate cache if main branch has changed
        self._invalidate_cache_if_needed(main_branch)

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
                self._check_fast_revlist,  # Fastest: single git command
                self._check_ancestor,  # Fast: single git command
                self._check_merge_commit_message,  # Medium: scans 100 commit messages
                self._check_full_commit_history,  # Slow: loads all commits
                self._check_squash_merge,  # Slowest: diffs + 50 commits (last resort)
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
        """Method 0: Check for squash merge by comparing diffs.

        This is an expensive operation (last resort) that compares the combined diff
        of the branch against recent commits in main to detect squash merges.
        """
        logger.debug("[Method 0] Checking for squash merge...")
        try:
            repo = self._get_repo()
            # Get all commits on the branch that aren't on main
            branch_commits = list(repo.iter_commits(f"{main_branch}..{branch_name}"))
            if not branch_commits:
                return False

            # Get the combined diff of all branch commits (normalized)
            branch_diff = repo.git.diff(
                f"{main_branch}...{branch_name}",
                "--no-color",
                "--ignore-space-change",  # Normalize whitespace
                "--ignore-blank-lines",  # Ignore blank line changes
            )

            if not branch_diff or len(branch_diff) < 100:
                # Skip if diff is empty or too small to be meaningful
                return False

            # Search recent commits in main for matching changes
            # Only check recent commits (50 instead of 100 for performance)
            for commit in repo.iter_commits(main_branch, max_count=50):
                try:
                    commit_diff = repo.git.show(
                        commit.hexsha,
                        "--no-color",
                        "--format=",  # No commit message in output
                        "--ignore-space-change",
                        "--ignore-blank-lines",
                    )

                    # Check for exact match (not substring)
                    # This is more reliable than substring matching
                    if branch_diff == commit_diff:
                        logger.debug(
                            f"[Method 0] Found squash merge (exact diff match) in commit {commit.hexsha[:7]}"
                        )
                        self._increment_stat("method0")
                        return True

                    # Fallback: check if branch diff is substantial portion of commit diff
                    # (handles cases where commit has additional changes)
                    if len(branch_diff) > 200 and branch_diff in commit_diff:
                        similarity = len(branch_diff) / len(commit_diff)
                        if similarity > 0.9:  # 90% match
                            logger.debug(
                                f"[Method 0] Found likely squash merge (high similarity) in commit {commit.hexsha[:7]}"
                            )
                            self._increment_stat("method0")
                            return True

                except Exception as e:
                    logger.debug(f"[Method 0] Error processing commit {commit.hexsha[:7]}: {e}")
                    continue
        except git.exc.GitCommandError as e:
            logger.debug(f"[Method 0] Error checking squash merge: {e}")

        return False

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
            branch_commit_set = set(repo.git.rev_list(branch_name).split())
            main_commit_set = set(repo.git.rev_list(main_branch).split())
            if branch_commit_set.issubset(main_commit_set):
                logger.debug(f"[Method 4] Branch {branch_name} is merged (all commits in main)")
                self._increment_stat("method4")
                return True
        except Exception as e:
            logger.debug(f"[Method 4] Error checking commit history: {e}")

        return False
