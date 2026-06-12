"""Merge detection service for git-branch-keeper.

Detection uses three principled, git-native checks, ordered cheapest-first:

1. Reachability (`git merge-base --is-ancestor`): the branch tip is reachable from
   main. Covers ordinary merge commits and fast-forward merges.
2. Patch-equivalence (`git cherry`): every commit unique to the branch has a
   patch-identical commit already in main. Covers rebase-merges, cherry-picks, and
   single-commit squashes - cases where the work lives in main under different SHAs.
3. Combined-diff exact match (last resort): the branch's combined diff equals a
   single commit on main. Covers multi-commit squash merges, which collapse N commits
   into one and so have no per-commit patch-id match. A high-similarity (non-exact)
   match is treated as advisory only (see `is_likely_squash_merged`), never as merged.
"""

import git
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
        # Branches that look squash-merged by fuzzy diff similarity but were NOT
        # confirmed merged by any reliable method. Surfaced as an advisory note,
        # never treated as merged (a fuzzy guess must not trigger deletion).
        self._likely_squash_merged: set = set()
        self._squash_lock = Lock()
        # Add counters for merge detection methods
        self.merge_detection_stats = {
            "reachable": 0,  # merge commit / fast-forward (tip reachable from main)
            "patch_equivalent": 0,  # rebase / cherry-pick / single-commit squash (git cherry)
            "squash_diff": 0,  # multi-commit squash (combined-diff exact match)
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
            "reachable": "Reachable (merge/fast-forward)",
            "patch_equivalent": "Patch-equivalent (rebase/cherry-pick/squash)",
            "squash_diff": "Squash (combined-diff)",
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

            # Try each detection method in order (cheapest first)
            methods = [
                self._check_reachable,  # merge commit / fast-forward (single is-ancestor)
                self._check_patch_equivalent,  # rebase / cherry-pick / single squash (git cherry)
                self._check_squash_merge,  # multi-commit squash via combined diff (last resort)
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

    def _check_reachable(self, branch_name: str, main_branch: str) -> bool:
        """Reachability: the branch tip is an ancestor of main.

        Covers ordinary merge commits (``--no-ff``) and fast-forward merges - in both
        the branch's commits are reachable from main. This is the canonical
        ``git merge-base --is-ancestor`` check.
        """
        logger.debug("[reachable] Checking if branch tip is an ancestor of main...")
        try:
            repo = self._get_repo()
            branch_tip = repo.refs[branch_name].commit
            main_tip = repo.refs[main_branch].commit
            if repo.is_ancestor(branch_tip, main_tip):
                logger.debug(f"[reachable] {branch_name} tip is reachable from {main_branch}")
                self._increment_stat("reachable")
                return True
        except Exception as e:
            logger.debug(f"[reachable] Error: {e}")

        return False

    def _check_patch_equivalent(self, branch_name: str, main_branch: str) -> bool:
        """Patch-equivalence via ``git cherry``: every commit unique to the branch has
        a patch-identical commit already in main.

        Covers rebase-merges, cherry-picks, and single-commit squashes - cases where
        the branch's work lives in main under different SHAs. Uses git's patch-id,
        which is robust to differing SHAs, parents, and commit metadata (far more
        reliable than matching merge-commit message text).
        """
        logger.debug("[patch-equivalent] Checking via git cherry (patch-id)...")
        try:
            repo = self._get_repo()
            # `git cherry <upstream> <head>`: one line per commit in head not reachable
            # from upstream, prefixed '-' (a patch-equivalent commit exists in upstream)
            # or '+' (no equivalent). All '-' => every unique commit is already applied.
            output = repo.git.cherry(main_branch, branch_name).strip()
            if not output:
                # No commits unique to the branch - the reachable case, which is owned
                # by _check_reachable. Don't double-count it here.
                return False
            lines = [line for line in output.splitlines() if line.strip()]
            if lines and all(line.startswith("-") for line in lines):
                logger.debug(
                    f"[patch-equivalent] all {len(lines)} unique commit(s) of "
                    f"{branch_name} are applied to {main_branch}"
                )
                self._increment_stat("patch_equivalent")
                return True
        except Exception as e:
            logger.debug(f"[patch-equivalent] Error: {e}")

        return False

    def _check_squash_merge(self, branch_name: str, main_branch: str) -> bool:
        """Last resort: detect a multi-commit squash merge by combined-diff comparison.

        A squash merge collapses N branch commits into a single commit on main, so it
        has no per-commit patch-id match (``git cherry`` misses it). Here the branch's
        *combined* diff is compared against individual recent commits on main. An exact
        match counts as merged; a high-similarity (non-exact) match is advisory only
        (recorded in ``_likely_squash_merged``), never treated as merged.
        """
        logger.debug("[squash-diff] Checking for multi-commit squash merge...")
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
                            f"[squash-diff] Found squash merge (exact diff match) in commit {commit.hexsha[:7]}"
                        )
                        self._increment_stat("squash_diff")
                        return True

                    # Fallback: branch diff is a high-similarity substring of the
                    # commit diff (commit has some additional changes). This is a
                    # GUESS, not proof - diff-text containment does not prove the
                    # branch's work is actually in main (e.g. it may have been
                    # reverted, or the text may coincide). Record it as an advisory
                    # note instead of declaring the branch merged, so it is never
                    # auto-deleted on a heuristic alone. Keep scanning in case a
                    # later commit is an exact match.
                    if len(branch_diff) > 200 and branch_diff in commit_diff:
                        similarity = len(branch_diff) / len(commit_diff)
                        if similarity > 0.9:  # 90% match
                            logger.debug(
                                f"[squash-diff] Possible squash merge (high similarity, unconfirmed) "
                                f"for {branch_name} in commit {commit.hexsha[:7]}"
                            )
                            with self._squash_lock:
                                self._likely_squash_merged.add(branch_name)

                except Exception as e:
                    logger.debug(f"[squash-diff] Error processing commit {commit.hexsha[:7]}: {e}")
                    continue
        except git.exc.GitCommandError as e:
            logger.debug(f"[squash-diff] Error checking squash merge: {e}")

        return False

    def is_likely_squash_merged(self, branch_name: str) -> bool:
        """Whether a branch looked squash-merged by fuzzy similarity but was not
        confirmed merged by any reliable method. Advisory only - never deletable.
        """
        with self._squash_lock:
            return branch_name in self._likely_squash_merged
