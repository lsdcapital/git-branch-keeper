"""Merge detection service for git-branch-keeper.

Detection uses three principled, git-native checks, ordered cheapest-first:

1. Reachability (`git merge-base --is-ancestor`): the branch tip is reachable from
   main. Covers ordinary merge commits and fast-forward merges.
2. Patch-equivalence (`git cherry`): every commit unique to the branch has a
   patch-identical commit already in main. Covers rebase-merges, cherry-picks, and
   single-commit squashes - cases where the work lives in main under different SHAs.
3. Combined patch-id match (last resort): the branch's combined diff has the
   same stable patch-id as a first-parent commit on main since the branch fork point.
   Covers multi-commit squash merges, which collapse N commits into one and so have
   no per-commit patch-id match. A high-similarity (non-exact) match is treated as
   advisory only (see `is_likely_squash_merged`), never as merged.
"""

from __future__ import annotations

import subprocess
from threading import Lock
from typing import TYPE_CHECKING, Any

import git

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

logger = get_logger(__name__)


class MergeDetector:
    """Service for detecting if branches have been merged."""

    def __init__(self, repo_path: str, config: Config | dict):
        """Initialize the merge detector.

        Args:
            repo_path: Path to the git repository
            config: Configuration dictionary or Config object
        """
        self.repo_path = repo_path
        self.config = config
        self.debug_mode = config.get("debug", False)
        self._merge_status_cache: dict[str, bool] = {}  # Cache for merge status checks
        self._cache_lock = Lock()  # Thread safety for cache access
        self._main_branch_sha_cache: dict[str, str] = (
            {}
        )  # Track main branch SHA for cache invalidation
        # Branches that look squash-merged by fuzzy diff similarity but were NOT
        # confirmed merged by any reliable method. Surfaced as an advisory note,
        # never treated as merged (a fuzzy guess must not trigger deletion).
        self._likely_squash_merged: set = set()
        self._squash_lock = Lock()
        self._merge_detection_info: dict[str, dict[str, Any]] = {}
        self._merge_info_lock = Lock()
        # (patch-id, diff length) per first-parent commit on main, shared by every
        # branch checked in this run. These depend only on the commit and the fixed
        # diff flags, so recomputing them per branch made squash detection
        # O(branches x main commits) - the dominant cost on large repos.
        self._main_commit_signatures: dict[str, tuple[str | None, int]] = {}
        self._signature_lock = Lock()
        # Add counters for merge detection methods
        self.merge_detection_stats = {
            "reachable": 0,  # merge commit / fast-forward (tip reachable from main)
            "patch_equivalent": 0,  # rebase / cherry-pick / single-commit squash (git cherry)
            "squash_diff": 0,  # multi-commit squash (combined patch-id exact match)
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

    def _default_merge_detection_info(self, method: str = "not_checked") -> dict[str, Any]:
        """Return a stable, JSON-friendly merge-detection info object."""
        return {
            "merged": False,
            "method": method,
            "confidence": "none",
            "matched_commit": None,
            "searched_commits": 0,
            "scan_limit": None,
            "truncated": False,
        }

    def _set_merge_detection_info(self, branch_name: str, info: dict[str, Any]) -> None:
        """Record structured merge-detection info for later display/JSON output."""
        with self._merge_info_lock:
            self._merge_detection_info[branch_name] = info

    def get_merge_detection_info(self, branch_name: str) -> dict[str, Any]:
        """Return structured merge-detection info for a branch."""
        with self._merge_info_lock:
            return dict(
                self._merge_detection_info.get(branch_name, self._default_merge_detection_info())
            )

    def _get_squash_scan_limit(self) -> int:
        """Return the maximum number of first-parent main commits to scan for squash matches."""
        try:
            return max(1, int(self.config.get("squash_scan_limit", 500)))
        except (TypeError, ValueError):
            return 500

    def _patch_id_for_diff(self, diff: str) -> str | None:
        """Return git's stable patch-id for a diff, or None if no patch-id exists."""
        if not diff.strip():
            return None

        try:
            result = subprocess.run(
                ["git", "patch-id", "--stable"],
                cwd=self.repo_path,
                input=diff,
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"[patch-id] Error running git patch-id: {e}")
            return None

        if result.returncode != 0:
            logger.debug(f"[patch-id] git patch-id failed: {result.stderr.strip()}")
            return None

        output = result.stdout.strip()
        if not output:
            return None
        return output.split()[0]

    def _commit_diff(self, repo, commit_sha: str) -> str:
        """Return a commit's diff using the flags squash detection compares on."""
        return repo.git.show(
            commit_sha,
            "--no-color",
            "--format=",
            "--ignore-space-change",
            "--ignore-blank-lines",
        )

    def _main_commit_signature(self, repo, commit_sha: str) -> tuple[str | None, int]:
        """Return (patch-id, diff length) for a commit on main, computed once per run.

        The length is kept so the advisory similarity pass can rule a commit out
        without fetching its diff text again: `branch_diff in commit_diff` requires
        the commit diff to be at least as long as the branch diff, and the >0.9
        similarity ratio caps how much longer it can be. That narrows hundreds of
        candidates down to the handful worth reading.

        A commit that cannot be read is memoised as unusable rather than retried.
        """
        with self._signature_lock:
            cached = self._main_commit_signatures.get(commit_sha)
        if cached is not None:
            return cached

        try:
            diff = self._commit_diff(repo, commit_sha)
            signature: tuple[str | None, int] = (self._patch_id_for_diff(diff), len(diff))
        except GIT_ERRORS as e:
            logger.debug(f"[squash-patch-id] Error reading {commit_sha[:7]}: {e}")
            signature = (None, -1)

        with self._signature_lock:
            self._main_commit_signatures[commit_sha] = signature
        return signature

    def _get_main_branch_sha(self, main_branch: str) -> str:
        """Get the current SHA of the main branch."""
        try:
            repo = self._get_repo()
            return repo.refs[main_branch].commit.hexsha
        except GIT_ERRORS as e:
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
        except GIT_ERRORS as e:
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
            "squash_diff": "Squash (combined patch-id)",
        }

        for method, count in self.merge_detection_stats.items():
            if count > 0:
                stats.append(f"{method_names[method]}: {count}")

        return f"Merges detected by: {', '.join(stats)}"

    def is_branch_merged(
        self, branch_name: str, main_branch: str, force_refresh: bool = False
    ) -> bool:
        """Check if a branch is merged using multiple methods, ordered by speed.

        Args:
            branch_name: Branch to check
            main_branch: Branch it should be merged into
            force_refresh: Recompute even if memoised. The memo is only invalidated
                when *main* moves, so it can outlive changes to the branch itself;
                pass True before acting destructively on the answer.
        """
        # A branch cannot be merged into itself
        if branch_name == main_branch:
            logger.debug(f"Skipping merge check: {branch_name} is the main branch")
            return False

        # Invalidate cache if main branch has changed
        self._invalidate_cache_if_needed(main_branch)

        # Check cache first (thread-safe)
        cache_key = f"{branch_name}:{main_branch}"
        if not force_refresh:
            found, value = self._check_cache(cache_key)
            if found:
                return value

        self._set_merge_detection_info(branch_name, self._default_merge_detection_info("none"))
        with self._squash_lock:
            self._likely_squash_merged.discard(branch_name)

        try:
            # Skip if it's a tag
            if self.is_tag(branch_name):
                logger.debug(f"Skipping tag: {branch_name}")
                self._set_merge_detection_info(
                    branch_name, self._default_merge_detection_info("tag")
                )
                self._set_in_cache(cache_key, False)
                return False

            # Try each detection method in order (cheapest first)
            methods = [
                self._check_reachable,  # merge commit / fast-forward (single is-ancestor)
                self._check_patch_equivalent,  # rebase / cherry-pick / single squash (git cherry)
                self._check_squash_merge,  # multi-commit squash via combined patch-id (last resort)
            ]

            for method in methods:
                result = method(branch_name, main_branch)
                if result:
                    self._set_in_cache(cache_key, True)
                    return True

            self._set_in_cache(cache_key, False)
            return False
        except Exception as e:  # noqa: BLE001 - last-resort safety net
            # Deliberately broad: the individual detection methods already narrow
            # their own git failures, so anything reaching here is unexpected. Report
            # "not merged" rather than propagating - an unexpected failure must never
            # mark a branch deletable, nor abort analysis of the remaining branches.
            logger.debug(f"Error checking if branch is merged: {e}")
            info = self._default_merge_detection_info("error")
            info["error"] = str(e)
            self._set_merge_detection_info(branch_name, info)
            self._set_in_cache(cache_key, False)
            return False

    def is_unstarted_branch(self, branch_name: str, main_branch: str) -> bool:
        """Whether the branch was created from main and never moved since.

        Such a branch trivially satisfies the reachability check in
        :meth:`_check_reachable` - its tip *is* an ancestor of main - so without this
        distinction it is reported as merged, asserting a merge that never happened.
        Callers use it to classify the branch as :attr:`BranchStatus.UNSTARTED`.

        Zero unique commits (``git rev-list --count <main>..<branch> == 0``) is
        necessary but *not* sufficient: a fast-forward-merged branch also has none,
        because its commits became main's. Refs alone cannot separate the two - after
        a fast-forward there is no merge commit and no record that those commits ever
        belonged to the branch. The reflog is that record, and it is the only local
        evidence which distinguishes them::

            feature/done        (ff-merged)   commit: work
                                              branch: Created from HEAD
            feature/not-started (unstarted)   branch: Created from HEAD

        So this requires *positive* proof: exactly one reflog entry, and that entry a
        creation. Anything else - a commit, reset, rebase, or a reflog that is missing,
        expired, or disabled - returns False and leaves the branch to the ordinary
        merge detection. Being wrong in that direction only over-reports "merged",
        which the existing deletion guards already re-verify; being wrong in the other
        direction would hide a genuinely merged branch behind a label that is never a
        cleanup candidate.
        """
        if branch_name == main_branch:
            return False

        try:
            repo = self._get_repo()
            count = repo.git.rev_list("--count", f"{main_branch}..{branch_name}").strip()
            if int(count) != 0:
                return False

            # %gs is the reflog subject, newest first.
            reflog = repo.git.reflog("show", "--format=%gs", branch_name).strip()
        except (*GIT_ERRORS, ValueError) as e:
            logger.debug(f"[unstarted] Error for {branch_name}: {e}")
            return False

        entries = [line for line in reflog.splitlines() if line.strip()]
        if len(entries) != 1 or not entries[0].startswith("branch: Created from"):
            return False

        logger.debug(f"[unstarted] {branch_name} was created from main and never moved")
        return True

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
                self._set_merge_detection_info(
                    branch_name,
                    {
                        "merged": True,
                        "method": "reachable",
                        "confidence": "exact",
                        "matched_commit": branch_tip.hexsha,
                        "searched_commits": 0,
                        "scan_limit": None,
                        "truncated": False,
                    },
                )
                self._increment_stat("reachable")
                return True
        except GIT_ERRORS as e:
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
                self._set_merge_detection_info(
                    branch_name,
                    {
                        "merged": True,
                        "method": "patch_equivalent",
                        "confidence": "exact",
                        "matched_commit": None,
                        "searched_commits": len(lines),
                        "scan_limit": None,
                        "truncated": False,
                    },
                )
                self._increment_stat("patch_equivalent")
                return True
        except GIT_ERRORS as e:
            logger.debug(f"[patch-equivalent] Error: {e}")

        return False

    def _check_squash_merge(self, branch_name: str, main_branch: str) -> bool:
        """Last resort: detect a multi-commit squash merge by combined patch-id.

        A squash merge collapses N branch commits into a single commit on main, so it
        has no per-commit patch-id match (``git cherry`` misses it). Compare the
        branch's combined patch-id against first-parent commits on main since the
        branch fork point, capped by ``squash_scan_limit`` (default: 500).

        Fuzzy/high-similarity matches are advisory only, never treated as merged.
        """
        logger.debug("[squash-patch-id] Checking for multi-commit squash merge...")
        scan_limit = self._get_squash_scan_limit()
        try:
            repo = self._get_repo()
            branch_commits = list(repo.iter_commits(f"{main_branch}..{branch_name}"))
            if not branch_commits:
                return False

            merge_bases = repo.merge_base(main_branch, branch_name)
            if not merge_bases:
                return False
            base_sha = merge_bases[0].hexsha

            branch_diff = repo.git.diff(
                f"{base_sha}..{branch_name}",
                "--no-color",
                "--ignore-space-change",
                "--ignore-blank-lines",
            )
            if not branch_diff:
                return False

            branch_patch_id = self._patch_id_for_diff(branch_diff)
            if not branch_patch_id:
                return False

            rev_list = repo.git.rev_list(
                "--first-parent",
                f"--max-count={scan_limit + 1}",
                f"{base_sha}..{main_branch}",
            ).strip()
            candidate_shas = [line for line in rev_list.splitlines() if line.strip()]
            truncated = len(candidate_shas) > scan_limit
            candidate_shas = candidate_shas[:scan_limit]

            advisory_info: dict[str, Any] | None = None

            # Pass 1 - exact patch-id match. Signatures are memoised across branches,
            # so a given main commit is read at most once per run.
            for searched_count, commit_sha in enumerate(candidate_shas, start=1):
                commit_patch_id, _ = self._main_commit_signature(repo, commit_sha)
                if commit_patch_id and branch_patch_id == commit_patch_id:
                    logger.debug(f"[squash-patch-id] Found squash merge in commit {commit_sha[:7]}")
                    self._set_merge_detection_info(
                        branch_name,
                        {
                            "merged": True,
                            "method": "squash_patch_id",
                            "confidence": "exact",
                            "matched_commit": commit_sha,
                            "searched_commits": searched_count,
                            "scan_limit": scan_limit,
                            "truncated": truncated,
                        },
                    )
                    self._increment_stat("squash_diff")
                    return True

            # Pass 2 - advisory similarity, only reached when nothing matched exactly.
            # Containment requires len(commit_diff) >= len(branch_diff), and the >0.9
            # ratio requires len(commit_diff) < len(branch_diff) / 0.9, so only commits
            # whose memoised length falls in that band need their diff text read.
            if len(branch_diff) > 200:
                min_len = len(branch_diff)
                max_len = len(branch_diff) / 0.9
                for searched_count, commit_sha in enumerate(candidate_shas, start=1):
                    _, diff_len = self._main_commit_signature(repo, commit_sha)
                    if not min_len <= diff_len < max_len:
                        continue
                    try:
                        commit_diff = self._commit_diff(repo, commit_sha)
                    except GIT_ERRORS as e:
                        logger.debug(f"[squash-patch-id] Error processing {commit_sha[:7]}: {e}")
                        continue

                    if branch_diff not in commit_diff:
                        continue
                    similarity = len(branch_diff) / len(commit_diff)
                    if similarity > 0.9:
                        advisory_info = {
                            "merged": False,
                            "method": "squash_similarity",
                            "confidence": "advisory",
                            "matched_commit": commit_sha,
                            "searched_commits": searched_count,
                            "scan_limit": scan_limit,
                            "truncated": truncated,
                            "similarity": round(similarity, 4),
                        }
                        logger.debug(
                            f"[squash-patch-id] Possible squash merge for {branch_name} "
                            f"in commit {commit_sha[:7]} ({similarity:.1%} similarity)"
                        )

            if advisory_info:
                with self._squash_lock:
                    self._likely_squash_merged.add(branch_name)
                self._set_merge_detection_info(branch_name, advisory_info)
            elif candidate_shas or truncated:
                self._set_merge_detection_info(
                    branch_name,
                    {
                        "merged": False,
                        "method": "squash_patch_id",
                        "confidence": "none",
                        "matched_commit": None,
                        "searched_commits": len(candidate_shas),
                        "scan_limit": scan_limit,
                        "truncated": truncated,
                    },
                )
        except git.exc.GitCommandError as e:
            logger.debug(f"[squash-patch-id] Error checking squash merge: {e}")

        return False

    def is_likely_squash_merged(self, branch_name: str) -> bool:
        """Whether a branch looked squash-merged by fuzzy similarity but was not
        confirmed merged by any reliable method. Advisory only - never deletable.
        """
        with self._squash_lock:
            return branch_name in self._likely_squash_merged
