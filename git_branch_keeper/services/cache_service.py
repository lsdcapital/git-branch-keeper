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

        Only saves branches that are immutable (merged with closed/no PR).

        Args:
            branches: List of branch details to cache
            main_branch: Name of the main branch
        """
        try:
            # Load existing cache to preserve data
            existing_cache = self.load_cache()

            # Update with new immutable branches
            immutable_count = 0
            for branch in branches:
                is_immutable = self.is_immutable(branch)
                logger.debug(f"Branch '{branch.name}': status={branch.status.value}, pr_status={repr(branch.pr_status)}, immutable={is_immutable}")
                if is_immutable:
                    serialized = self._serialize_branch(branch)
                    # Skip if branch has invalid data
                    if serialized.get('last_commit_date') == 'unknown':
                        logger.debug(f"Skipping cache for branch '{branch.name}' with invalid date")
                        continue
                    existing_cache[branch.name] = serialized
                    immutable_count += 1

            logger.debug(f"Found {immutable_count} immutable branches to cache out of {len(branches)} total")

            # Remove branches that no longer exist or are no longer immutable
            # (This will be handled by the caller filtering out non-existent branches)

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

                immutable_count = sum(1 for b in existing_cache.values() if b.get('immutable', False))
                logger.debug(f"Saved cache with {len(existing_cache)} branches ({immutable_count} immutable)")
            finally:
                # Clean up temp file if it still exists
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

    def is_immutable(self, branch: BranchDetails) -> bool:
        """Check if a branch's state is immutable (won't change).

        A branch is immutable if:
        - It's merged AND (has a closed PR OR has no PR)

        Args:
            branch: Branch details to check

        Returns:
            True if the branch state is immutable
        """
        if branch.status != BranchStatus.MERGED:
            return False

        # If there's no PR status (None or empty string), the branch is merged and immutable
        if not branch.pr_status:
            return True

        # If the PR is closed (not open), it's immutable
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
            "has_local_changes": branch.has_local_changes,
            "has_remote": branch.has_remote,
            "sync_status": branch.sync_status,
            "pr_status": branch.pr_status,
            "notes": branch.notes,
            "immutable": self.is_immutable(branch),
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
                has_local_changes=data["has_local_changes"],
                has_remote=data["has_remote"],
                sync_status=data["sync_status"],
                pr_status=data.get("pr_status"),
                notes=data.get("notes")
            )
        except Exception as e:
            logger.warning(f"Failed to deserialize branch {data.get('name', 'unknown')}: {e}")
            return None

    def get_cached_branches(self, current_branches: List[str]) -> Dict[str, BranchDetails]:
        """Get cached branch details for branches that still exist.

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
                # Only use cache if it's marked as immutable
                if branch_data.get('immutable', False):
                    branch_details = self.deserialize_branch(branch_data)
                    if branch_details:
                        cached_branches[branch_name] = branch_details

        logger.debug(f"Found {len(cached_branches)} cached immutable branches")
        return cached_branches

    def clear_cache(self) -> None:
        """Clear all cached data for this repository."""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
                logger.info("Cache cleared")
        except Exception as e:
            logger.warning(f"Failed to clear cache: {e}")
