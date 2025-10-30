"""Cache service for storing branch analysis results."""
import json
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from contextlib import contextmanager

from git_branch_keeper.models.branch import BranchDetails, BranchStatus

# Import fcntl for POSIX file locking (Unix/Linux/macOS)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

logger = logging.getLogger(__name__)


class CacheService:
    """Manages caching of branch analysis results."""

    def __init__(self, repo_path: str):
        """Initialize cache service for a repository.

        Args:
            repo_path: Path to the git repository
        """
        self.repo_path = Path(repo_path).resolve()
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
            except Exception as e:
                logger.debug(f"Error releasing lock: {e}")

    def _validate_cache_data(self, cache_data: Dict) -> bool:
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

            if 'branches' not in cache_data:
                logger.warning("Cache missing 'branches' key")
                return False

            if not isinstance(cache_data['branches'], dict):
                logger.warning("Cache 'branches' is not a dictionary")
                return False

            # Validate each branch entry
            for branch_name, branch_data in cache_data['branches'].items():
                if not isinstance(branch_data, dict):
                    logger.warning(f"Branch data for '{branch_name}' is not a dictionary")
                    return False

                # Check required fields
                required_fields = ['name', 'last_commit_date', 'age_days', 'status']
                for field in required_fields:
                    if field not in branch_data:
                        logger.warning(f"Branch '{branch_name}' missing required field '{field}'")
                        return False

                # Validate last_commit_date is not "unknown"
                if branch_data['last_commit_date'] == 'unknown':
                    logger.warning(f"Branch '{branch_name}' has invalid last_commit_date")
                    return False

            return True
        except Exception as e:
            logger.warning(f"Error validating cache: {e}")
            return False

    def load_cache(self) -> Dict[str, Dict]:
        """Load cached branch data from disk with file locking and validation.

        Returns:
            Dictionary mapping branch names to cached branch details
        """
        if not self.cache_file.exists():
            logger.debug("No cache file found")
            return {}

        try:
            with open(self.cache_file, 'r') as f:
                # Acquire shared lock for reading
                with self._acquire_cache_lock(f, operation="read"):
                    cache_data = json.load(f)

            # Validate cache data
            if not self._validate_cache_data(cache_data):
                logger.warning("Cache validation failed, ignoring cache")
                return {}

            logger.debug(f"Loaded cache with {len(cache_data.get('branches', {}))} branches")
            return cache_data.get('branches', {})
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in cache file: {e}")
            return {}
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return {}

    def save_cache(self, branches: List[BranchDetails], main_branch: str) -> None:
        """Save branch data to cache using atomic writes with file locking.

        Saves all branches with metadata indicating stability.

        Args:
            branches: List of branch details to cache
            main_branch: Name of the main branch
        """
        try:
            # Load existing cache to preserve data
            existing_cache = self.load_cache()

            # Update with all branches
            for branch in branches:
                serialized = self._serialize_branch(branch)
                # Skip if branch has invalid data
                if serialized.get('last_commit_date') == 'unknown':
                    logger.debug(f"Skipping cache for branch '{branch.name}' with invalid date")
                    continue
                existing_cache[branch.name] = serialized

            logger.debug(f"Cached {len(existing_cache)} branches out of {len(branches)} total")

            cache_data = {
                "repo_path": str(self.repo_path),
                "main_branch": main_branch,
                "last_updated": datetime.now().isoformat(),
                "branches": existing_cache
            }

            # Atomic write: write to temp file, then rename
            temp_file = self.cache_file.with_suffix('.tmp')
            try:
                with open(temp_file, 'w') as f:
                    # Acquire exclusive lock for writing
                    with self._acquire_cache_lock(f, operation="write"):
                        json.dump(cache_data, f, indent=2)
                        f.flush()  # Ensure data is written to disk

                # Atomic rename (POSIX systems guarantee atomicity)
                temp_file.replace(self.cache_file)

                stable_count = sum(1 for b in existing_cache.values() if b.get('stable', False))
                logger.debug(f"Saved cache with {len(existing_cache)} branches ({stable_count} stable)")
            finally:
                # Clean up temp file if it still exists
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
        except Exception as e:
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
        if not branch.pr_status.startswith("open"):
            return True

        return False

    def _serialize_branch(self, branch: BranchDetails) -> Dict:
        """Convert BranchDetails to a cache-friendly dictionary.

        Args:
            branch: Branch details to serialize

        Returns:
            Dictionary representation of the branch
        """
        return {
            "name": branch.name,
            "last_commit_date": branch.last_commit_date,
            "age_days": branch.age_days,
            "status": branch.status.value,
            "modified_files": branch.modified_files,
            "untracked_files": branch.untracked_files,
            "staged_files": branch.staged_files,
            "has_remote": branch.has_remote,
            "sync_status": branch.sync_status,
            "pr_status": branch.pr_status,
            "notes": branch.notes,
            "stable": self.is_stable(branch),
            "cached_at": datetime.now().isoformat()
        }

    def deserialize_branch(self, data: Dict) -> Optional[BranchDetails]:
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

            return BranchDetails(
                name=data["name"],
                last_commit_date=data["last_commit_date"],
                age_days=data["age_days"],
                status=BranchStatus(data["status"]),
                modified_files=data["modified_files"],
                untracked_files=data["untracked_files"],
                staged_files=data["staged_files"],
                has_remote=data["has_remote"],
                sync_status=data["sync_status"],
                pr_status=data.get("pr_status"),
                notes=data.get("notes"),
                in_worktree=False  # Don't cache worktree status - it's dynamic
            )
        except Exception as e:
            logger.warning(f"Failed to deserialize branch {data.get('name', 'unknown')}: {e}")
            return None

    def get_cached_branches(self, current_branches: List[str]) -> Dict[str, BranchDetails]:
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

        stable_count = sum(1 for b in cache.values() if b.get('stable', False))
        logger.debug(f"Found {len(cached_branches)} cached branches ({stable_count} stable)")
        return cached_branches

    def get_stale_branches(self, current_branches: List[str], main_branch: str) -> List[str]:
        """Get list of branches that need to be refreshed (unstable or not cached).

        A branch needs refresh if:
        - It's not in cache, OR
        - It's the main branch (always check sync status), OR
        - It's marked as unstable (not merged or has open PR)

        Args:
            current_branches: List of current branch names in the repository
            main_branch: Name of the main branch (always refreshed)

        Returns:
            List of branch names that need to be refreshed
        """
        cache = self.load_cache()
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
            if not branch_data.get('stable', False):
                logger.debug(f"Branch '{branch_name}' is unstable, needs refresh")
                stale_branches.append(branch_name)

        stable_skipped = len(current_branches) - len(stale_branches)
        logger.debug(f"Found {len(stale_branches)} branches needing refresh, {stable_skipped} stable branches skipped")
        return stale_branches

    def clear_cache(self) -> None:
        """Clear all cached data for this repository."""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
                logger.info("Cache cleared")
        except Exception as e:
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
                with open(self.cache_file, 'r') as f:
                    with self._acquire_cache_lock(f, operation="read"):
                        full_cache_data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read full cache data: {e}")
                return

            # Update the branches section
            full_cache_data['branches'] = cache
            full_cache_data['last_updated'] = datetime.now().isoformat()

            # Atomic write: write to temp file, then rename
            temp_file = self.cache_file.with_suffix('.tmp')
            try:
                with open(temp_file, 'w') as f:
                    with self._acquire_cache_lock(f, operation="write"):
                        json.dump(full_cache_data, f, indent=2)
                        f.flush()

                temp_file.replace(self.cache_file)
                logger.debug(f"Cache updated, {len(cache)} branches remaining")
            finally:
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to remove branch '{branch_name}' from cache: {e}")

    def remove_branches_from_cache(self, branch_names: List[str]) -> None:
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
                with open(self.cache_file, 'r') as f:
                    with self._acquire_cache_lock(f, operation="read"):
                        full_cache_data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read full cache data: {e}")
                return

            # Update the branches section
            full_cache_data['branches'] = cache
            full_cache_data['last_updated'] = datetime.now().isoformat()

            # Atomic write: write to temp file, then rename
            temp_file = self.cache_file.with_suffix('.tmp')
            try:
                with open(temp_file, 'w') as f:
                    with self._acquire_cache_lock(f, operation="write"):
                        json.dump(full_cache_data, f, indent=2)
                        f.flush()

                temp_file.replace(self.cache_file)
                logger.debug(f"Cache updated after removing {removed_count} branches, {len(cache)} branches remaining")
            finally:
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to remove branches from cache: {e}")
