"""Worktree operations service for git-branch-keeper."""

import git
import os
import tempfile
import shutil
from contextlib import contextmanager
from typing import Optional, Dict, Any
from threading import Lock

from git_branch_keeper.models.worktree import WorktreeInfo
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.utils.logging import get_logger

logger = get_logger(__name__)


class WorktreeService:
    """Service for managing git worktrees."""

    def __init__(self, repo_path: str):
        """Initialize the worktree service.

        Args:
            repo_path: Path to the git repository
        """
        self.repo_path = repo_path
        self._worktree_info: Optional[list[WorktreeInfo]] = None  # Cache for worktree information
        self._cache_lock = Lock()  # Thread safety for cache access

    def _get_repo(self):
        """Get a thread-safe git.Repo instance.

        Creates a new repo instance for each call to ensure thread safety.

        Returns:
            git.Repo: A fresh repository instance
        """
        return git.Repo(self.repo_path)

    def clear_cache(self):
        """Clear the worktree information cache."""
        with self._cache_lock:
            self._worktree_info = None

    def get_worktree_branches(self) -> set[str]:
        """Get set of branch names that are checked out in worktrees.

        Returns:
            Set of branch names currently in worktrees
        """
        # Get full worktree info and extract branch names
        worktree_infos = self.get_worktree_info()
        return {wt.branch_name for wt in worktree_infos if wt.branch_name}

    def get_worktree_info(self) -> list[WorktreeInfo]:
        """Get detailed information about all worktrees.

        Returns:
            List of WorktreeInfo objects for all worktrees
        """
        # Return cached result if available
        with self._cache_lock:
            if self._worktree_info is not None:
                return self._worktree_info

        worktree_list = []
        try:
            repo = self._get_repo()
            # Use --porcelain for machine-readable output
            output = repo.git.worktree("list", "--porcelain")

            # Parse porcelain output
            # Format:
            # worktree /path/to/worktree
            # HEAD commit_sha
            # branch refs/heads/branch-name
            # (blank line between worktrees)

            current_worktree: Dict[str, Any] = {}
            for line in output.split("\n"):
                line = line.strip()

                if not line:
                    # Empty line marks end of worktree entry
                    if current_worktree:
                        # Create WorktreeInfo from collected data
                        path = current_worktree.get("path", "")
                        branch_name = current_worktree.get("branch", "")
                        commit_sha = current_worktree.get("HEAD", "")
                        is_main = current_worktree.get("is_main", False)

                        # Check if directory exists
                        is_orphaned = not os.path.exists(path) if path else True

                        if path:  # Only add if we have a path
                            worktree_list.append(
                                WorktreeInfo(
                                    path=path,
                                    branch_name=branch_name,
                                    commit_sha=commit_sha,
                                    is_main=is_main,
                                    is_orphaned=is_orphaned,
                                )
                            )
                        current_worktree = {}
                    continue

                # Parse each line
                if line.startswith("worktree "):
                    current_worktree["path"] = line.split(" ", 1)[1]
                    # First worktree in list is always the main one
                    if not worktree_list:
                        current_worktree["is_main"] = True
                    else:
                        current_worktree["is_main"] = False
                elif line.startswith("HEAD "):
                    current_worktree["HEAD"] = line.split(" ", 1)[1]
                elif line.startswith("branch "):
                    # Extract branch name from "branch refs/heads/branch-name"
                    branch_ref = line.split(" ", 1)[1]
                    if branch_ref.startswith("refs/heads/"):
                        current_worktree["branch"] = branch_ref[len("refs/heads/") :]
                    else:
                        current_worktree["branch"] = ""  # Detached HEAD
                elif line.startswith("detached"):
                    current_worktree["branch"] = ""  # Detached HEAD

            # Handle last entry if no trailing blank line
            if current_worktree and current_worktree.get("path"):
                path = current_worktree.get("path", "")
                branch_name = current_worktree.get("branch", "")
                commit_sha = current_worktree.get("HEAD", "")
                is_main = current_worktree.get("is_main", False)
                is_orphaned = not os.path.exists(path) if path else True

                worktree_list.append(
                    WorktreeInfo(
                        path=path,
                        branch_name=branch_name,
                        commit_sha=commit_sha,
                        is_main=is_main,
                        is_orphaned=is_orphaned,
                    )
                )

            logger.debug(f"Found {len(worktree_list)} worktrees")
            for wt in worktree_list:
                logger.debug(f"  {wt}")
        except Exception as e:
            logger.debug(f"Could not list worktrees: {e}")
            # Return empty list if worktree command fails

        # Cache the result
        with self._cache_lock:
            self._worktree_info = worktree_list
        return worktree_list

    def remove_worktree(self, path: str, force: bool = False) -> tuple[bool, Optional[str]]:
        """Remove a worktree at the specified path.

        Args:
            path: Path to the worktree directory
            force: Force removal even if working tree is dirty or locked

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        try:
            repo = self._get_repo()
            args = ["remove", path]
            if force:
                args.append("--force")

            repo.git.worktree(*args)
            logger.info(f"Removed worktree at {path}")

            # Clear cache since worktree list changed
            self.clear_cache()

            return True, None
        except git.exc.GitCommandError as e:
            # Extract detailed error information from GitCommandError
            stderr = (e.stderr if hasattr(e, "stderr") else str(e)).strip()
            status = e.status if hasattr(e, "status") else "unknown"

            if stderr:
                error_msg = f"git worktree remove failed (exit {status}): {stderr}"
            else:
                error_msg = f"git worktree remove failed with exit code {status}"

            logger.error(f"Failed to remove worktree at {path}: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error removing worktree: {e}"
            logger.error(error_msg)
            return False, error_msg

    def prune_worktrees(self) -> tuple[bool, Optional[str]]:
        """Prune orphaned worktree metadata.

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        try:
            repo = self._get_repo()
            repo.git.worktree("prune")
            logger.info("Pruned orphaned worktree metadata")

            # Clear cache since worktree list changed
            self.clear_cache()

            return True, None
        except git.exc.GitCommandError as e:
            # Extract detailed error information from GitCommandError
            stderr = (e.stderr if hasattr(e, "stderr") else str(e)).strip()
            status = e.status if hasattr(e, "status") else "unknown"

            if stderr:
                error_msg = f"git worktree prune failed (exit {status}): {stderr}"
            else:
                error_msg = f"git worktree prune failed with exit code {status}"

            logger.error(f"Failed to prune worktrees: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error pruning worktrees: {e}"
            logger.error(error_msg)
            return False, error_msg

    def get_worktree_status_details(self, worktree_path: str) -> dict:
        """Get detailed file status of a worktree without checkout.

        Args:
            worktree_path: Path to the worktree directory

        Returns:
            Dict with 'modified', 'untracked', 'staged' boolean flags,
            or empty dict if worktree path doesn't exist (orphaned)
        """
        try:
            # Check if worktree directory exists
            if not os.path.exists(worktree_path):
                logger.debug(f"Worktree path {worktree_path} doesn't exist (orphaned)")
                return {}

            # Run git status in the worktree directory
            # Use git -C <path> to run command in that directory
            repo = self._get_repo()
            status = repo.git.execute(["git", "-C", worktree_path, "status", "--porcelain"])

            # Parse porcelain format: XY filename
            # X = index status (first char), Y = working tree status (second char)
            has_modified = False
            has_untracked = False
            has_staged = False

            for line in status.split("\n"):
                if not line:
                    continue

                # Get the two-character status code
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
            stderr = (e.stderr if hasattr(e, "stderr") else str(e)).strip()
            status_code = e.status if hasattr(e, "status") else "unknown"

            if stderr:
                error_msg = f"git status in worktree failed (exit {status_code}): {stderr}"
            else:
                error_msg = f"git status in worktree failed with exit code {status_code}"

            logger.warning(f"Could not check worktree status for {worktree_path}: {error_msg}")
            return {}
        except Exception as e:
            logger.warning(f"Could not check worktree status for {worktree_path}: {e}")
            return {}

    @contextmanager
    def create_temporary_worktree(self, branch_name: str):
        """Create a temporary worktree for safe branch operations.

        This context manager creates a temporary worktree, yields its path,
        and ensures cleanup even if an error occurs.

        Args:
            branch_name: Name of the branch to check out in the worktree

        Yields:
            str: Path to the temporary worktree directory

        Example:
            with worktree_service.create_temporary_worktree("feature-branch") as temp_path:
                # Do work with the temporary worktree
                status = repo.git.execute(["git", "-C", temp_path, "status"])
        """
        temp_dir = None
        try:
            # Create a temporary directory for the worktree
            # Sanitize branch name to avoid issues with slashes in branch names (e.g., feat/branch-name)
            sanitized_name = branch_name.replace("/", "-")
            temp_dir = tempfile.mkdtemp(prefix=f"gbk-{sanitized_name}-")
            logger.debug(f"Created temp directory: {temp_dir}")

            # Create worktree in temp directory
            repo = self._get_repo()
            repo.git.worktree("add", temp_dir, branch_name)
            logger.debug(f"Created worktree at {temp_dir} for branch {branch_name}")

            yield temp_dir

        finally:
            # Clean up worktree
            if temp_dir:
                try:
                    logger.debug(f"Removing worktree at {temp_dir}")
                    repo = self._get_repo()
                    repo.git.worktree("remove", temp_dir, "--force")
                except Exception as cleanup_error:
                    logger.debug(f"Error removing worktree {temp_dir}: {cleanup_error}")

                # Clean up temp directory
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as dir_error:
                    logger.debug(f"Error removing temp directory {temp_dir}: {dir_error}")

    @staticmethod
    def is_worktree_removable(branch: BranchDetails) -> bool:
        """Check if a worktree is removable.

        Args:
            branch: Branch details (representing a worktree entry)

        Returns:
            True if worktree can be removed (is orphaned or parent branch is stale/merged)
        """
        # Only worktree entries can be removed as worktrees
        if not branch.is_worktree:
            return False

        # Worktree is removable if:
        # 1. It's orphaned (directory doesn't exist) - check notes for [ORPHANED]
        # 2. OR the parent branch is stale/merged (same status as parent)
        is_orphaned = branch.notes and "[ORPHANED]" in branch.notes
        is_stale_or_merged = branch.status in [BranchStatus.STALE, BranchStatus.MERGED]

        return is_orphaned or is_stale_or_merged
