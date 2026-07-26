"""Worktree operations service for git-branch-keeper."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from threading import Lock
from typing import Any

import git

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.models.worktree import WorktreeInfo
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
        self._worktree_info: list[WorktreeInfo] | None = None  # Cache for worktree information
        self._cache_lock = Lock()  # Thread safety for cache access
        self._cleanup_lock = Lock()  # Avoid concurrent self-healing prune attempts
        self._current_path: str | None = None  # Cache for the running worktree's path
        self._current_path_resolved = False
        self._current_path_lock = Lock()

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

    def get_current_worktree_path(self) -> str | None:
        """Return the real path of the worktree GBK itself is running in.

        This is the main working tree in the common case, but it is a linked
        worktree whenever GBK is invoked from inside one.
        """
        with self._current_path_lock:
            if self._current_path_resolved:
                return self._current_path

            current: str | None = None
            try:
                working_dir = self._get_repo().working_dir
                if working_dir:
                    current = os.path.realpath(working_dir)
            except GIT_ERRORS as e:
                logger.debug(f"Could not resolve current worktree path: {e}")

            self._current_path = current
            self._current_path_resolved = True
            return current

    def is_current_worktree(self, path: str) -> bool:
        """Return True when path is the worktree GBK is running in."""
        if not path:
            return False
        current = self.get_current_worktree_path()
        if not current:
            return False
        return os.path.realpath(path) == current

    def get_other_worktrees(self, refresh: bool = False) -> list[WorktreeInfo]:
        """Return every worktree except the one GBK is running in.

        ``is_main`` is not a safe stand-in for "not ours": when GBK runs from a
        linked worktree, the main working tree holds a *different* branch that
        still must be protected from deletion.
        """
        return [
            worktree
            for worktree in self.get_worktree_info(refresh=refresh)
            if not self.is_current_worktree(worktree.path)
        ]

    def find_worktree_for_branch(
        self, branch_name: str, refresh: bool = False
    ) -> WorktreeInfo | None:
        """Return the worktree holding branch_name, ignoring our own worktree.

        Args:
            branch_name: Branch to look for
            refresh: Bypass the cache and re-read Git's worktree metadata. Use
                this immediately before destructive operations, where a stale
                cache would let GBK act on an out-of-date view.
        """
        if not branch_name:
            return None
        return next(
            (
                worktree
                for worktree in self.get_other_worktrees(refresh=refresh)
                if worktree.branch_name == branch_name
            ),
            None,
        )

    def get_worktree_branches(self) -> set[str]:
        """Get set of branch names that are checked out in worktrees.

        Returns:
            Set of branch names currently in worktrees
        """
        # Get full worktree info and extract branch names
        worktree_infos = self.get_worktree_info()
        return {wt.branch_name for wt in worktree_infos if wt.branch_name}

    def _worktree_info_from_entry(self, entry: dict[str, Any]) -> WorktreeInfo | None:
        """Build a WorktreeInfo object from parsed porcelain fields."""
        path = entry.get("path", "")
        if not path:
            return None

        branch_name = entry.get("branch", "")
        commit_sha = entry.get("HEAD", "")
        is_main = entry.get("is_main", False)
        is_orphaned = not os.path.exists(path)

        return WorktreeInfo(
            path=path,
            branch_name=branch_name,
            commit_sha=commit_sha,
            is_main=is_main,
            is_orphaned=is_orphaned,
        )

    def _parse_worktree_info(self, output: str) -> list[WorktreeInfo]:
        """Parse ``git worktree list --porcelain`` output."""
        worktree_list: list[WorktreeInfo] = []
        current_worktree: dict[str, Any] = {}

        for line in output.split("\n"):
            line = line.strip()

            if not line:
                # Empty line marks end of worktree entry
                if current_worktree:
                    worktree_info = self._worktree_info_from_entry(current_worktree)
                    if worktree_info is not None:
                        worktree_list.append(worktree_info)
                    current_worktree = {}
                continue

            if line.startswith("worktree "):
                current_worktree["path"] = line.split(" ", 1)[1]
                # First worktree in list is always the main one
                current_worktree["is_main"] = not bool(worktree_list)
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
        if current_worktree:
            worktree_info = self._worktree_info_from_entry(current_worktree)
            if worktree_info is not None:
                worktree_list.append(worktree_info)

        return worktree_list

    def _is_gbk_temp_worktree_path(self, path: str) -> bool:
        """Return True for GBK-owned temp worktree paths."""
        try:
            real_path = os.path.realpath(path)
            temp_root = os.path.realpath(tempfile.gettempdir())
            return os.path.basename(real_path).startswith("gbk-") and real_path.startswith(
                temp_root + os.sep
            )
        except OSError:
            return False

    def _is_valid_git_worktree(self, path: str) -> bool:
        """Return True when path is an existing, usable Git worktree."""
        if not os.path.isdir(path):
            return False

        try:
            repo = self._get_repo()
            result = repo.git.execute(["git", "-C", path, "rev-parse", "--is-inside-work-tree"])
            return result.strip() == "true"
        except GIT_ERRORS:
            return False

    def _cleanup_stale_gbk_temp_worktree(self, path: str) -> bool:
        """Remove stale GBK temp worktree files/metadata if safe to do so."""
        if not self._is_gbk_temp_worktree_path(path):
            return False

        with self._cleanup_lock:
            try:
                if os.path.exists(path):
                    shutil.rmtree(path, ignore_errors=True)

                repo = self._get_repo()
                repo.git.worktree("prune", "--expire=now")
                logger.info(f"Pruned stale GBK temporary worktree metadata at {path}")
                self.clear_cache()
                return True
            except (*GIT_ERRORS, OSError) as e:
                logger.debug(f"Could not prune stale GBK temporary worktree {path}: {e}")
                return False

    def _cleanup_stale_gbk_temp_worktrees(self, worktree_list: list[WorktreeInfo]) -> bool:
        """Self-heal stale GBK temporary worktrees left by interrupted runs."""
        cleaned = False
        for worktree in worktree_list:
            if not self._is_gbk_temp_worktree_path(worktree.path):
                continue
            if worktree.is_orphaned or not self._is_valid_git_worktree(worktree.path):
                cleaned = self._cleanup_stale_gbk_temp_worktree(worktree.path) or cleaned
        return cleaned

    def get_worktree_info(self, refresh: bool = False) -> list[WorktreeInfo]:
        """Get detailed information about all worktrees.

        Args:
            refresh: Ignore the cache and re-read Git's worktree metadata.

        Returns:
            List of WorktreeInfo objects for all worktrees
        """
        # Return cached result if available
        if not refresh:
            with self._cache_lock:
                if self._worktree_info is not None:
                    return self._worktree_info

        worktree_list: list[WorktreeInfo] = []
        try:
            repo = self._get_repo()
            # Use --porcelain for machine-readable output
            output = repo.git.worktree("list", "--porcelain")
            worktree_list = self._parse_worktree_info(output)

            if self._cleanup_stale_gbk_temp_worktrees(worktree_list):
                # Re-read after self-healing so callers don't see stale entries.
                output = repo.git.worktree("list", "--porcelain")
                worktree_list = self._parse_worktree_info(output)

            # GBK-created temp worktrees are implementation details. Never cache
            # or expose them as user worktrees; otherwise parallel analysis can
            # observe another worker's temporary checkout after it has vanished.
            worktree_list = [
                worktree
                for worktree in worktree_list
                if not self._is_gbk_temp_worktree_path(worktree.path)
            ]

            logger.debug(f"Found {len(worktree_list)} worktrees")
            for wt in worktree_list:
                logger.debug(f"  {wt}")
        except GIT_ERRORS as e:
            logger.debug(f"Could not list worktrees: {e}")
            # Return empty list if worktree command fails

        # Cache the result
        with self._cache_lock:
            self._worktree_info = worktree_list
        return worktree_list

    def remove_worktree(self, path: str, force: bool = False) -> tuple[bool, str | None]:
        """Remove a worktree at the specified path.

        Args:
            path: Path to the worktree directory
            force: Force removal even if working tree is dirty or locked

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        # Git happily removes the worktree the caller is standing in (it only
        # refuses for the *main* working tree), which with force=True would
        # delete the directory GBK is running from along with any work in it.
        if self.is_current_worktree(path):
            error_msg = "Refusing to remove the worktree git-branch-keeper is running in"
            logger.error(f"Failed to remove worktree at {path}: {error_msg}")
            return False, error_msg

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
        except GIT_ERRORS as e:
            error_msg = f"Unexpected error removing worktree: {e}"
            logger.error(error_msg)
            return False, error_msg

    def prune_worktrees(self) -> tuple[bool, str | None]:
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
        except GIT_ERRORS as e:
            error_msg = f"Unexpected error pruning worktrees: {e}"
            logger.error(error_msg)
            return False, error_msg

    def get_worktree_status_details(self, worktree_path: str) -> dict:
        """Get detailed file status of a worktree without checkout.

        Args:
            worktree_path: Path to the worktree directory

        Returns:
            Dict with 'modified', 'untracked', 'staged' boolean flags.
            On error, returns 'error' and optionally 'orphaned'.
        """
        try:
            # Check if worktree directory exists
            if not os.path.exists(worktree_path):
                logger.debug(f"Worktree path {worktree_path} doesn't exist (orphaned)")
                if self._cleanup_stale_gbk_temp_worktree(worktree_path):
                    return {
                        "error": "Stale GBK temporary worktree metadata was pruned",
                        "orphaned": True,
                    }
                return {"error": "Worktree path does not exist", "orphaned": True}

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

            if (
                self._is_gbk_temp_worktree_path(worktree_path)
                and not self._is_valid_git_worktree(worktree_path)
                and self._cleanup_stale_gbk_temp_worktree(worktree_path)
            ):
                return {
                    "error": "Stale GBK temporary worktree metadata was pruned",
                    "orphaned": True,
                }

            logger.warning(f"Could not check worktree status for {worktree_path}: {error_msg}")
            return {"error": f"Git error: {error_msg}"}
        except GIT_ERRORS as e:
            error_msg = str(e)
            logger.warning(f"Could not check worktree status for {worktree_path}: {error_msg}")
            return {"error": f"Status check failed: {error_msg}"}

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
                except GIT_ERRORS as cleanup_error:
                    logger.debug(f"Error removing worktree {temp_dir}: {cleanup_error}")

                # Clean up temp directory
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except OSError as dir_error:
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
