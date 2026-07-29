"""Cache service for storing branch analysis results."""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import git

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.services.git.refs import BranchRefResolver, BranchRefState

# Import fcntl for POSIX file locking (Unix/Linux/macOS)
try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

logger = logging.getLogger(__name__)


class CacheService:
    """Manages caching of branch analysis results."""

    def __init__(self, repo_path: str, remote_name: str = "origin", include_remote: bool = True):
        """Initialize cache service for a repository.

        Args:
            repo_path: Path to the git repository
            remote_name: Remote whose refs share the branch-name namespace
            include_remote: Whether branch discovery includes remote-only branches.
                Must match ``Config.include_remote_branches`` - see
                :meth:`_get_current_branch_tips`.
        """
        self.repo_path = Path(repo_path).resolve()
        self.remote_name = remote_name
        self.include_remote = include_remote
        self.ref_resolver = BranchRefResolver(str(self.repo_path), remote_name)
        self.cache_dir = Path.home() / ".git-branch-keeper" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / f"{self._get_repo_hash()}.json"

    def _get_repo_hash(self) -> str:
        """Generate a unique hash for the repository path."""
        return hashlib.md5(str(self.repo_path).encode()).hexdigest()

    @contextmanager
    def _acquire_cache_lock(self, file_handle, operation: str = "read"):
        """Acquire file lock for cache operations.

        Args:
            file_handle: Open file handle to lock
            operation: Type of operation ("read" or "write")

        Yields:
            None when lock is acquired
        """
        if not HAS_FCNTL:
            logger.debug("File locking not available on this platform")
            yield
            return

        try:
            # Acquire exclusive lock for writes, shared lock for reads
            lock_type = fcntl.LOCK_EX if operation == "write" else fcntl.LOCK_SH
            fcntl.flock(file_handle.fileno(), lock_type)
            logger.debug(f"Acquired {operation} lock on cache file")
            yield
        finally:
            try:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
                logger.debug(f"Released {operation} lock on cache file")
            except OSError as e:
                logger.debug(f"Error releasing lock: {e}")

    def _get_current_branch_states(self) -> dict[str, BranchRefState] | None:
        """Return live tip and location state for every branch this repository analyzes.

        Cache entries are keyed by branch name, so pruning must use the same namespace
        as branch discovery (``BranchKeeper._get_filtered_branches``). That means local
        heads *and*, when ``include_remote`` is set, remote-only branches under
        ``refs/remotes/<remote>/*`` mapped back to their plain names. Narrowing this to
        local heads while discovery includes remotes would prune every remote row on
        each run, so the cache could never warm up.

        Both tip and location are required to verify a cached row. A local+remote branch
        can become remote-only without changing its SHA, and the reverse transition is
        possible too. Reusing ``has_local`` across that transition can turn a read-only
        remote row into a deletion candidate. If the repository cannot be opened, return
        None so callers preserve existing cache data instead of risking data loss.
        """
        try:
            repo = git.Repo(self.repo_path)
            try:
                states = self.ref_resolver.snapshot(repo, refresh=True)
                if self.include_remote:
                    return states
                return {name: state for name, state in states.items() if state.has_local}
            finally:
                repo.close()
        except GIT_ERRORS as e:
            logger.debug(f"Could not read branch states for cache: {e}")
            return None

    def _get_current_branch_tips(self) -> dict[str, str] | None:
        """Backward-compatible tip-only view over :meth:`_get_current_branch_states`."""
        states = self._get_current_branch_states()
        return None if states is None else {name: state.tip_sha for name, state in states.items()}

    def _get_current_local_branch_names(self) -> set[str] | None:
        """Return the names of branches currently analyzed, or None if the repo cannot
        be opened. Kept as a name-set view over :meth:`_get_current_branch_tips` so both
        stay in the same namespace.
        """
        tips = self._get_current_branch_tips()
        return None if tips is None else set(tips)

    def _branches_reachable_from(self, main_branch: str) -> set[str] | None:
        """Return local branches whose tip is currently reachable from main_branch.

        One `git branch --merged` call answers this for every branch at once, which
        is what makes it usable as a cheap re-validation for cache entries written
        before tip SHAs were recorded. Returns None if the query fails.
        """
        try:
            repo = git.Repo(self.repo_path)
            try:
                output = repo.git.branch("--merged", main_branch, "--format=%(refname:short)")
            finally:
                repo.close()
        except GIT_ERRORS as e:
            logger.debug(f"Could not list branches merged into {main_branch}: {e}")
            return None

        return {line.strip() for line in output.splitlines() if line.strip()}

    def _validate_cache_data(self, cache_data: dict) -> bool:
        """Validate cache data structure and required fields.

        Args:
            cache_data: Cache data dictionary to validate

        Returns:
            True if cache is valid, False otherwise
        """
        try:
            # Check basic structure
            if not isinstance(cache_data, dict):
                logger.warning("Cache data is not a dictionary")
                return False

            if "branches" not in cache_data:
                logger.warning("Cache missing 'branches' key")
                return False

            if not isinstance(cache_data["branches"], dict):
                logger.warning("Cache 'branches' is not a dictionary")
                return False

            # Validate each branch entry
            for branch_name, branch_data in cache_data["branches"].items():
                if not isinstance(branch_data, dict):
                    logger.warning(f"Branch data for '{branch_name}' is not a dictionary")
                    return False

                # Check required fields
                required_fields = ["name", "last_commit_date", "age_days", "status"]
                for field in required_fields:
                    if field not in branch_data:
                        logger.warning(f"Branch '{branch_name}' missing required field '{field}'")
                        return False

                # Validate last_commit_date is not "unknown"
                if branch_data["last_commit_date"] == "unknown":
                    logger.warning(f"Branch '{branch_name}' has invalid last_commit_date")
                    return False

            return True
        except Exception as e:  # noqa: BLE001 - validates untrusted on-disk cache data
            # Deliberately broad: cache_data comes from a JSON file that could be
            # corrupt or hand-edited; any anomaly here means "not valid", not a bug.
            logger.warning(f"Error validating cache: {e}")
            return False

    def load_cache(self) -> dict[str, dict]:
        """Load cached branch data from disk with file locking and validation.

        Returns:
            Dictionary mapping branch names to cached branch details
        """
        if not self.cache_file.exists():
            logger.debug("No cache file found")
            return {}

        try:
            # Acquire shared lock for reading
            with open(self.cache_file) as f, self._acquire_cache_lock(f, operation="read"):
                cache_data = json.load(f)

            # Validate cache data
            if not self._validate_cache_data(cache_data):
                logger.warning("Cache validation failed, ignoring cache")
                return {}

            logger.debug(f"Loaded cache with {len(cache_data.get('branches', {}))} branches")
            return cache_data.get("branches", {})
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in cache file: {e}")
            return {}
        except Exception as e:  # noqa: BLE001 - cache file is untrusted disk state
            # Deliberately broad: any read/lock/permission failure should degrade
            # to "no cache" rather than crash the whole run.
            logger.warning(f"Failed to load cache: {e}")
            return {}

    def save_cache(self, branches: list[BranchDetails], main_branch: str) -> None:
        """Save branch data to cache using atomic writes with file locking.

        Saves all branches with metadata indicating stability.

        Args:
            branches: List of branch details to cache
            main_branch: Name of the main branch
        """
        try:
            # Load existing cache to preserve data for branches that still exist locally.
            existing_cache = self.load_cache()

            branch_states = self._get_current_branch_states()
            current_local_branches = None if branch_states is None else set(branch_states)
            if current_local_branches is not None:
                before_count = len(existing_cache)
                existing_cache = {
                    branch_name: branch_data
                    for branch_name, branch_data in existing_cache.items()
                    if branch_name in current_local_branches
                }
                pruned_count = before_count - len(existing_cache)
                if pruned_count:
                    logger.debug(
                        f"Pruned {pruned_count} cache entr{'y' if pruned_count == 1 else 'ies'} "
                        "that no longer correspond to local branches"
                    )

            # Update with all current branch rows. Skip anything outside refs/heads/* so
            # custom refs or detached/orphaned metadata can never be persisted as branches.
            for branch in branches:
                if current_local_branches is not None and branch.name not in current_local_branches:
                    logger.debug(f"Skipping cache for non-local branch ref '{branch.name}'")
                    continue

                state = branch_states.get(branch.name) if branch_states else None
                tip_sha = state.tip_sha if state else None
                serialized = self._serialize_branch(branch, tip_sha)
                # Skip if branch has invalid data
                if serialized.get("last_commit_date") == "unknown":
                    logger.debug(f"Skipping cache for branch '{branch.name}' with invalid date")
                    continue
                existing_cache[branch.name] = serialized

            logger.debug(f"Cached {len(existing_cache)} branches out of {len(branches)} total")

            cache_data = {
                "repo_path": str(self.repo_path),
                "main_branch": main_branch,
                "last_updated": datetime.now(UTC).isoformat(),
                "branches": existing_cache,
            }

            # Atomic write: write to temp file, then rename
            temp_file = self.cache_file.with_suffix(".tmp")
            try:
                # Acquire exclusive lock for writing
                with open(temp_file, "w") as f, self._acquire_cache_lock(f, operation="write"):
                    json.dump(cache_data, f, indent=2)
                    f.flush()  # Ensure data is written to disk

                # Atomic rename (POSIX systems guarantee atomicity)
                temp_file.replace(self.cache_file)

                stable_count = sum(1 for b in existing_cache.values() if b.get("stable", False))
                logger.debug(
                    f"Saved cache with {len(existing_cache)} branches ({stable_count} stable)"
                )
            finally:
                # Clean up temp file if it still exists
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except OSError:
                        logger.debug("Could not remove leftover temp cache file")
        except Exception as e:  # noqa: BLE001 - cache save must not crash the run
            logger.warning(f"Failed to save cache: {e}")

    def is_stable(self, branch: BranchDetails) -> bool:
        """Check if a branch's state is stable (unlikely to change).

        A branch is stable if:
        - It's merged AND (has a closed PR OR has no PR)

        Args:
            branch: Branch details to check

        Returns:
            True if the branch state is stable
        """
        if branch.status != BranchStatus.MERGED:
            return False

        # If there's no PR status (None or empty string), the branch is merged and stable
        if not branch.pr_status:
            return True

        # If the PR is closed (not open), it's stable
        # pr_status format is like "open" or "closed:merged" or "closed:unmerged"
        return not branch.pr_status.startswith("open")

    def _serialize_branch(self, branch: BranchDetails, tip_sha: str | None = None) -> dict:
        """Convert BranchDetails to a cache-friendly dictionary.

        Args:
            branch: Branch details to serialize
            tip_sha: The branch's tip SHA at the time of caching. This is what lets
                get_stale_branches() tell "still merged" from "was merged, then grew
                new commits"; an entry without it is never reused.

        Returns:
            Dictionary representation of the branch
        """
        return {
            "name": branch.name,
            "tip_sha": tip_sha,
            "last_commit_date": branch.last_commit_date,
            "age_days": branch.age_days,
            "status": branch.status.value,
            "modified_files": branch.modified_files,
            "untracked_files": branch.untracked_files,
            "staged_files": branch.staged_files,
            "has_remote": branch.has_remote,
            "has_local": branch.has_local,
            "sync_status": branch.sync_status,
            "pr_status": branch.pr_status,
            "pr_details": branch.pr_details,
            "notes": branch.notes,
            "merge_detection": branch.merge_detection,
            "stable": self.is_stable(branch),
            "cached_at": datetime.now(UTC).isoformat(),
        }

    def deserialize_branch(self, data: dict) -> BranchDetails | None:
        """Convert cached dictionary back to BranchDetails.

        Args:
            data: Dictionary representation of a branch

        Returns:
            BranchDetails object or None if deserialization fails or data is invalid
        """
        try:
            # Validate that critical data is not "unknown"
            if data.get("last_commit_date") == "unknown":
                logger.debug(f"Skipping cached branch '{data.get('name')}' with invalid date")
                return None

            last_commit_date = data["last_commit_date"]
            commit_date = date.fromisoformat(last_commit_date)
            age_days = (datetime.now(UTC).date() - commit_date).days

            return BranchDetails(
                name=data["name"],
                last_commit_date=last_commit_date,
                # Age is derived from the commit date, so its cached value becomes
                # stale at midnight even when the rest of the branch row is stable.
                age_days=age_days,
                status=BranchStatus(data["status"]),
                modified_files=data["modified_files"],
                untracked_files=data["untracked_files"],
                staged_files=data["staged_files"],
                has_remote=data["has_remote"],
                # Entries written before remote enumeration existed have no
                # has_local key, and every branch they described was local.
                has_local=data.get("has_local", True),
                sync_status=data["sync_status"],
                pr_status=data.get("pr_status"),
                pr_details=data.get("pr_details"),
                notes=data.get("notes"),
                in_worktree=False,  # Don't cache worktree status - it's dynamic
                merge_detection=data.get("merge_detection"),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to deserialize branch {data.get('name', 'unknown')}: {e}")
            return None

    def get_cached_branches(self, current_branches: list[str]) -> dict[str, BranchDetails]:
        """Get cached branch details for all branches that still exist.

        Args:
            current_branches: List of current branch names in the repository

        Returns:
            Dictionary mapping branch names to cached BranchDetails
        """
        cache = self.load_cache()
        cached_branches = {}

        for branch_name in current_branches:
            if branch_name in cache:
                branch_data = cache[branch_name]
                branch_details = self.deserialize_branch(branch_data)
                if branch_details:
                    cached_branches[branch_name] = branch_details

        stable_count = sum(1 for b in cache.values() if b.get("stable", False))
        logger.debug(f"Found {len(cached_branches)} cached branches ({stable_count} stable)")
        return cached_branches

    def get_stale_branches(self, current_branches: list[str], main_branch: str) -> list[str]:
        """Get list of branches that need to be refreshed (unstable or not cached).

        A branch needs refresh if:
        - It's not in cache, OR
        - It's the main branch (always check sync status), OR
        - It's marked as unstable (not merged or has open PR), OR
        - Its tip SHA has moved since it was cached, or cannot be re-validated

        The tip check is a safety requirement, not an optimisation. A merged branch
        is cached as "stable" and would otherwise never be re-examined, so commits
        added to it after the merge - the ordinary "merge the PR, keep working"
        flow - would still be reported as merged and deleted with `git branch -D`.

        Entries written before tip SHAs were recorded get one cheap chance to prove
        themselves: if the branch is still reachable from main, its cached "merged"
        verdict is confirmed by git right now and the row is kept (the end-of-run
        save records its tip, so this only happens once). Anything else - including
        rebase- and squash-merged branches, which are not reachable - is re-analysed.

        Args:
            current_branches: List of current branch names in the repository
            main_branch: Name of the main branch (always refreshed)

        Returns:
            List of branch names that need to be refreshed
        """
        cache = self.load_cache()
        # None means the repo could not be read; then nothing can be validated, so
        # every cached entry has to be treated as stale.
        branch_states = self._get_current_branch_states()
        # Resolved lazily - only legacy entries need it, so repos with a current
        # cache never pay for the extra git call.
        reachable_from_main: set[str] | None = None
        reachable_resolved = False
        stale_branches = []

        for branch_name in current_branches:
            # Always refresh main branch to check sync status
            if branch_name == main_branch:
                logger.debug(f"Main branch '{branch_name}' needs refresh")
                stale_branches.append(branch_name)
                continue

            # If not in cache, needs refresh
            if branch_name not in cache:
                logger.debug(f"Branch '{branch_name}' not in cache, needs refresh")
                stale_branches.append(branch_name)
                continue

            # If in cache but not stable, needs refresh
            branch_data = cache[branch_name]
            if not branch_data.get("stable", False):
                logger.debug(f"Branch '{branch_name}' is unstable, needs refresh")
                stale_branches.append(branch_name)
                continue

            # Stable, but only trustworthy if the branch still points where it did.
            cached_tip = branch_data.get("tip_sha")
            current_state = branch_states.get(branch_name) if branch_states is not None else None
            current_tip = current_state.tip_sha if current_state else None

            if current_state is not None:
                cached_has_local = branch_data.get("has_local", True)
                cached_has_remote = branch_data.get("has_remote", False)
                if (
                    cached_has_local != current_state.has_local
                    or cached_has_remote != current_state.has_remote
                ):
                    logger.debug(
                        f"Branch '{branch_name}' location changed since caching "
                        f"(local={cached_has_local}, remote={cached_has_remote} -> "
                        f"local={current_state.has_local}, remote={current_state.has_remote}), "
                        "needs refresh"
                    )
                    stale_branches.append(branch_name)
                    continue

            if cached_tip:
                if cached_tip != current_tip:
                    logger.debug(
                        f"Branch '{branch_name}' tip changed since caching "
                        f"({cached_tip[:12]} -> {str(current_tip)[:12]}), needs refresh"
                    )
                    stale_branches.append(branch_name)
                continue

            # Legacy entry with no recorded tip. Re-validate against live git instead
            # of re-analysing: reachability proves the cached "merged" verdict still
            # holds, and costs one shared git call rather than a full branch analysis.
            if not reachable_resolved:
                reachable_from_main = self._branches_reachable_from(main_branch)
                reachable_resolved = True

            if reachable_from_main is not None and branch_name in reachable_from_main:
                logger.debug(
                    f"Branch '{branch_name}' has no cached tip but is still reachable "
                    f"from '{main_branch}' - keeping cached row"
                )
                continue

            logger.debug(f"Branch '{branch_name}' has no verifiable cached tip, needs refresh")
            stale_branches.append(branch_name)

        stable_skipped = len(current_branches) - len(stale_branches)
        logger.debug(
            f"Found {len(stale_branches)} branches needing refresh, {stable_skipped} stable branches skipped"
        )
        return stale_branches

    def clear_cache(self) -> None:
        """Clear all cached data for this repository."""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
                logger.info("Cache cleared")
        except OSError as e:
            logger.warning(f"Failed to clear cache: {e}")

    def remove_branch_from_cache(self, branch_name: str) -> None:
        """Remove a single branch from the cache.

        Args:
            branch_name: Name of the branch to remove from cache
        """
        try:
            # Load existing cache
            cache = self.load_cache()

            # Check if branch exists in cache
            if branch_name not in cache:
                logger.debug(f"Branch '{branch_name}' not in cache, nothing to remove")
                return

            # Remove the branch
            del cache[branch_name]
            logger.debug(f"Removed branch '{branch_name}' from cache")

            # Save updated cache back to disk
            # We need to reconstruct the full cache structure for saving
            if not self.cache_file.exists():
                logger.debug("Cache file no longer exists, skipping save")
                return

            # Read the full cache data to preserve metadata
            try:
                with open(self.cache_file) as f, self._acquire_cache_lock(f, operation="read"):
                    full_cache_data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to read full cache data: {e}")
                return

            # Update the branches section
            full_cache_data["branches"] = cache
            full_cache_data["last_updated"] = datetime.now(UTC).isoformat()

            # Atomic write: write to temp file, then rename
            temp_file = self.cache_file.with_suffix(".tmp")
            try:
                with open(temp_file, "w") as f, self._acquire_cache_lock(f, operation="write"):
                    json.dump(full_cache_data, f, indent=2)
                    f.flush()

                temp_file.replace(self.cache_file)
                logger.debug(f"Cache updated, {len(cache)} branches remaining")
            finally:
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except OSError:
                        logger.debug("Could not remove leftover temp cache file")
        except Exception as e:  # noqa: BLE001 - cache update must not crash the run
            logger.warning(f"Failed to remove branch '{branch_name}' from cache: {e}")

    def remove_branches_from_cache(self, branch_names: list[str]) -> None:
        """Remove multiple branches from the cache in a single operation.

        Args:
            branch_names: List of branch names to remove from cache
        """
        if not branch_names:
            logger.debug("No branches to remove from cache")
            return

        try:
            # Load existing cache
            cache = self.load_cache()

            # Remove all branches that exist in cache
            removed_count = 0
            for branch_name in branch_names:
                if branch_name in cache:
                    del cache[branch_name]
                    removed_count += 1

            if removed_count == 0:
                logger.debug("No branches were in cache, nothing to remove")
                return

            logger.debug(f"Removing {removed_count} branch(es) from cache")

            # Save updated cache back to disk
            if not self.cache_file.exists():
                logger.debug("Cache file no longer exists, skipping save")
                return

            # Read the full cache data to preserve metadata
            try:
                with open(self.cache_file) as f, self._acquire_cache_lock(f, operation="read"):
                    full_cache_data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to read full cache data: {e}")
                return

            # Update the branches section
            full_cache_data["branches"] = cache
            full_cache_data["last_updated"] = datetime.now(UTC).isoformat()

            # Atomic write: write to temp file, then rename
            temp_file = self.cache_file.with_suffix(".tmp")
            try:
                with open(temp_file, "w") as f, self._acquire_cache_lock(f, operation="write"):
                    json.dump(full_cache_data, f, indent=2)
                    f.flush()

                temp_file.replace(self.cache_file)
                logger.debug(
                    f"Cache updated after removing {removed_count} branches, {len(cache)} branches remaining"
                )
            finally:
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except OSError:
                        logger.debug("Could not remove leftover temp cache file")
        except Exception as e:  # noqa: BLE001 - cache update must not crash the run
            logger.warning(f"Failed to remove branches from cache: {e}")
