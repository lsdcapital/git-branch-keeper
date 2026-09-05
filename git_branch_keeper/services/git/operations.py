"""Git operations service - Facade for git operations."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import git
from rich.console import Console

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.services.deletion_journal import DeletionJournal
from git_branch_keeper.services.git.branch_queries import BranchQueries
from git_branch_keeper.services.git.merge_detector import MergeDetector
from git_branch_keeper.services.git.refs import BranchRefResolver
from git_branch_keeper.services.git.worktrees import WorktreeService
from git_branch_keeper.utils.logging import get_logger
from git_branch_keeper.utils.remotes import detect_remote_name

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RemoteDeletionResult:
    """Structured outcome for a remote-only deletion attempt."""

    success: bool
    outcome: str
    message: str
    remote_deleted: bool = False


class GitOperations:
    """Facade for Git operations, composing specialized services."""

    def __init__(self, repo_path: str, config: Config | dict):
        """Initialize the service.

        Args:
            repo_path: Path to the git repository (string path, not repo object)
            config: Configuration dictionary or Config object
        """
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get("verbose", False)
        self.debug_mode = config.get("debug", False)
        # Detect the remote once (prefers "origin"; adapts to a single non-origin remote).
        # Defensive: the repo is otherwise opened lazily per-call, so a bad path must not
        # fail construction here - fall back to the default and let _get_repo() surface it.
        try:
            self.remote_name = detect_remote_name(git.Repo(repo_path))
        except GIT_ERRORS:
            self.remote_name = "origin"
        self.in_git_operation = False  # Track if operation is in progress

        # Compose specialized services (Dependency Injection pattern)
        self.ref_resolver = BranchRefResolver(repo_path, self.remote_name)
        self.merge_detector = MergeDetector(
            repo_path, config, remote_name=self.remote_name, ref_resolver=self.ref_resolver
        )
        # One WorktreeService for the whole facade: it caches Git's worktree list,
        # and a second instance would keep its own copy, so a cache refresh taken
        # before a deletion would not be seen by branch queries.
        self.worktree_service = WorktreeService(repo_path)
        self.branch_queries = BranchQueries(
            repo_path,
            config,
            self.merge_detector,
            remote_name=self.remote_name,
            worktree_service=self.worktree_service,
            ref_resolver=self.ref_resolver,
        )
        self.deletion_journal = DeletionJournal(repo_path)

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

    @staticmethod
    def _git_error_detail(error: git.exc.GitCommandError) -> str:
        """Return Git's useful stderr without reducing it to a generic failure."""
        stderr = (getattr(error, "stderr", None) or "").strip()
        if stderr:
            return stderr
        stdout = (getattr(error, "stdout", None) or "").strip()
        if stdout:
            return stdout
        return str(error)

    def invalidate_analysis_caches(self) -> None:
        """Discard ref and merge memos before an explicit reanalysis."""
        self.ref_resolver.invalidate()
        self.merge_detector.invalidate()

    def sync_remote_refs(self) -> tuple[bool, str | None]:
        """Fetch and prune the selected remote for an explicit user refresh."""
        try:
            repo = self._get_repo()
            repo.git.fetch("--prune", "--no-tags", self.remote_name)
            self.invalidate_analysis_caches()
            return True, None
        except git.exc.GitCommandError as error:
            self.invalidate_analysis_caches()
            detail = self._git_error_detail(error)
            logger.warning(f"Could not synchronize {self.remote_name}: {detail}")
            return False, detail

    def get_live_remote_branch_sha(self, branch_name: str) -> tuple[str | None, str | None]:
        """Return the server's exact branch tip, distinguishing absence from failure."""
        try:
            repo = self._get_repo()
            ref_name = f"refs/heads/{branch_name}"
            output = repo.git.ls_remote("--heads", self.remote_name, ref_name).strip()
            if not output:
                return None, None

            for line in output.splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[1] == ref_name:
                    return fields[0], None
            return None, f"Unexpected ls-remote response for {self.remote_name}/{branch_name}"
        except git.exc.GitCommandError as error:
            return None, self._git_error_detail(error)

    def _discard_remote_tracking_ref(
        self, repo: git.Repo, branch_name: str, expected_sha: str
    ) -> None:
        """Drop one stale tracking ref without touching any server-side state."""
        tracking_ref = f"refs/remotes/{self.remote_name}/{branch_name}"
        try:
            repo.git.update_ref("-d", tracking_ref, expected_sha)
        except git.exc.GitCommandError as error:
            logger.debug(
                f"Could not remove tracking ref {tracking_ref}: {self._git_error_detail(error)}"
            )

    # ============================================================================
    # Delegation methods to MergeDetector
    # ============================================================================

    def is_branch_merged(
        self, branch_name: str, main_branch: str, force_refresh: bool = False
    ) -> bool:
        """Check if a branch is merged. Delegates to MergeDetector."""
        return self.merge_detector.is_branch_merged(
            branch_name, main_branch, force_refresh=force_refresh
        )

    def is_unstarted_branch(self, branch_name: str, main_branch: str) -> bool:
        """Whether a branch was created and never moved. Delegates to MergeDetector."""
        return self.merge_detector.is_unstarted_branch(branch_name, main_branch)

    def get_merge_stats(self) -> str:
        """Get merge detection statistics. Delegates to MergeDetector."""
        return self.merge_detector.get_merge_stats()

    def is_likely_squash_merged(self, branch_name: str) -> bool:
        """Whether a branch looked squash-merged by fuzzy similarity only (advisory).

        Delegates to MergeDetector. Only meaningful after is_branch_merged() has
        run for the branch (that is when the fuzzy check executes).
        """
        return self.merge_detector.is_likely_squash_merged(branch_name)

    def get_partial_merge(self, branch_name: str) -> tuple[int, int] | None:
        """``(landed, total)`` unique commits when only part of the branch is in main.

        Delegates to MergeDetector. Only meaningful after is_branch_merged() has run
        for the branch (that is when `git cherry` executes).
        """
        return self.merge_detector.get_partial_merge(branch_name)

    def unstarted_is_unverifiable(self, branch_name: str) -> bool:
        """Whether an UNSTARTED verdict lacks reflog proof. Delegates to MergeDetector.

        Only meaningful after is_unstarted_branch() has run for the branch.
        """
        return self.merge_detector.unstarted_is_unverifiable(branch_name)

    def remote_history_is_unverifiable(self, branch_name: str) -> bool:
        """Whether remote branch-name provenance cannot be proved."""
        return self.merge_detector.remote_history_is_unverifiable(branch_name)

    def get_merge_detection_info(self, branch_name: str) -> dict:
        """Get structured merge-detection details for display/JSON output."""
        return self.merge_detector.get_merge_detection_info(branch_name)

    def validate_exact_squash_match(
        self, branch_name: str, main_branch: str, matched_commit: str
    ) -> bool:
        """Revalidate a stored exact squash proof without a bounded history scan."""
        return self.merge_detector.validate_exact_squash_match(
            branch_name,
            main_branch,
            matched_commit,
        )

    # ============================================================================
    # Delegation methods to BranchRefResolver
    # ============================================================================

    def has_local_branch(self, branch_name: str, *, refresh: bool = False) -> bool:
        """Whether refs/heads/<branch_name> exists. Delegates to BranchRefResolver."""
        return self.ref_resolver.has_local(branch_name, refresh=refresh)

    def get_remote_only_branches(self) -> list[str]:
        """Plain names of branches on the remote with no local head."""
        return self.ref_resolver.remote_only_branch_names()

    # ============================================================================
    # Delegation methods to BranchQueries
    # ============================================================================

    def get_branch_tip_sha(self, branch_name: str) -> str | None:
        """Return the effective local-or-selected-remote tip SHA."""
        return self.branch_queries.get_branch_tip_sha(branch_name)

    def has_remote_branch(self, branch_name: str, *, refresh: bool = False) -> bool:
        """Check if branch has a remote. Delegates to BranchQueries."""
        return self.branch_queries.has_remote_branch(branch_name, refresh=refresh)

    def get_branch_age(self, branch_name: str) -> int:
        """Get branch age. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_age(branch_name)

    def get_branch_sync_status(self, branch_name: str, main_branch: str) -> str:
        """Get branch sync status. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_sync_status(branch_name, main_branch)

    def get_last_commit_at(self, branch_name: str) -> str:
        """Get last commit timestamp. Delegates to BranchQueries."""
        return self.branch_queries.get_last_commit_at(branch_name)

    def get_last_commit_date(self, branch_name: str) -> str:
        """Get last commit date. Delegates to BranchQueries."""
        return self.branch_queries.get_last_commit_date(branch_name)

    def get_branch_status_details(self, branch_name: str) -> dict:
        """Get branch status details. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_status_details(branch_name)

    def get_file_status_detailed(
        self, branch_name: str | None = None, worktree_path: str | None = None
    ) -> dict:
        """Get detailed file status. Delegates to BranchQueries."""
        return self.branch_queries.get_file_status_detailed(branch_name, worktree_path)

    def get_diff(
        self,
        branch_name: str | None = None,
        worktree_path: str | None = None,
        staged: bool = False,
    ) -> str:
        """Get diff output. Delegates to BranchQueries."""
        return self.branch_queries.get_diff(branch_name, worktree_path, staged)

    def get_branch_commits(self, branch_name: str, main_branch: str, limit: int = 20) -> list[dict]:
        """Get branch commits. Delegates to BranchQueries."""
        return self.branch_queries.get_branch_commits(branch_name, main_branch, limit)

    def get_merge_details(self, branch_name: str, main_branch: str) -> dict:
        """Get merge details. Delegates to BranchQueries."""
        return self.branch_queries.get_merge_details(branch_name, main_branch)

    def get_comparison_to_main(self, branch_name: str, main_branch: str) -> dict:
        """Get exact comparison info vs main. Delegates to BranchQueries."""
        return self.branch_queries.get_comparison_to_main(branch_name, main_branch)

    def get_divergence_info(self, branch_name: str, main_branch: str) -> dict:
        """Get divergence info. Delegates to BranchQueries."""
        return self.branch_queries.get_divergence_info(branch_name, main_branch)

    def is_tag(self, ref_name: str) -> bool:
        """Check if ref is a tag. Delegates to MergeDetector."""
        return self.merge_detector.is_tag(ref_name)

    # ============================================================================
    # Core Git Operations (not delegated)
    # ============================================================================

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

    def _git_can_verify_deletion(self, repo, branch_name: str) -> bool:
        """Whether `git branch -d` would accept this branch on its own.

        `-d` refuses anything not reachable from HEAD (or the branch's upstream), so
        it can only vouch for ordinary merges and fast-forwards. Rebase- and
        squash-merged branches are genuinely unreachable and always need `-D`; using
        `-d` where it *can* answer keeps git as an independent check on GBK's own
        merge detection, without turning legitimate cleanups into failures.
        """
        try:
            head_commit = repo.head.commit
            branch_commit = repo.heads[branch_name].commit
        except (*GIT_ERRORS, ValueError, IndexError) as e:
            logger.debug(f"Could not compare {branch_name} against HEAD: {e}")
            return False

        try:
            return repo.is_ancestor(branch_commit, head_commit)
        except GIT_ERRORS as e:
            logger.debug(f"Ancestry check failed for {branch_name}: {e}")
            return False

    def delete_branch(
        self,
        branch_name: str,
        dry_run: bool = False,
        delete_remote: bool = False,
        batch_id: str | None = None,
        verify_remote: Callable[[str], bool] | None = None,
    ) -> bool:
        """Delete a branch locally, and remotely only when explicitly requested.

        By default the remote branch is kept (local-only deletion) because
        remote deletions affect collaborators and are harder to undo. Pass
        delete_remote=True to also delete it.

        Every real deletion is recorded in the deletion journal (with the
        branch's tip SHA) so it can be restored with `git-branch-keeper undo`.
        """
        with self._git_operation():
            try:
                repo = self._get_repo()

                # Resolve the local head immediately before acting. Planning and the
                # TUI may have been sitting on a cached row while refs changed.
                try:
                    local_head = repo.heads[branch_name]
                except GIT_ERRORS:
                    logger.warning(
                        f"Refusing to delete {branch_name}: no local branch exists "
                        "(remote-only branches are read-only)"
                    )
                    return False

                # Refresh remote state at the same execution boundary.
                has_remote = self.has_remote_branch(branch_name, refresh=True)
                should_delete_remote = has_remote and delete_remote

                # Capture the tip SHA before deletion so the branch is recoverable
                deleted_sha = local_head.commit.hexsha

                if should_delete_remote:
                    live_sha, error = self.get_live_remote_branch_sha(branch_name)
                    if error:
                        raise ValueError(f"Could not verify remote tip: {error}")
                    if live_sha is None:
                        should_delete_remote = False
                    elif live_sha != deleted_sha:
                        raise ValueError(
                            "Remote tip differs from local tip; remote work must be reviewed"
                        )
                    elif verify_remote is not None:
                        if not verify_remote(deleted_sha):
                            raise ValueError("Remote ownership or merged PR could not be verified")
                    elif not self.is_branch_merged(
                        branch_name, self.config.get("main_branch", "main"), force_refresh=True
                    ):
                        raise ValueError("Remote deletion requires positive merge proof")

                # Delete local branch. Prefer `-d` so git independently confirms the
                # branch is reachable before anything is discarded; fall back to `-D`
                # only where `-d` structurally cannot succeed (rebase/squash merges,
                # and stale branches, which are unmerged by definition). A `-d` that
                # fails is git disagreeing with us - surface it instead of forcing.
                local_deleted = False
                if not dry_run:
                    console.print(f"Deleting local branch {branch_name}...")
                    safe_delete = self._git_can_verify_deletion(repo, branch_name)
                    repo.delete_head(branch_name, force=not safe_delete)
                    local_deleted = True

                # Delete remote branch only when requested. Journal in `finally` so
                # the entry is written even if the remote push fails after the local
                # branch is already gone - that's when recovery info matters most.
                remote_deleted = False
                try:
                    if should_delete_remote:
                        if not dry_run:
                            try:
                                console.print(f"Deleting remote branch {branch_name}...")
                                repo.git.push(
                                    self.remote_name,
                                    f":refs/heads/{branch_name}",
                                    force_with_lease=f"refs/heads/{branch_name}:{deleted_sha}",
                                )
                                remote_deleted = True
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
                    elif has_remote:
                        # Remote exists but we're keeping it
                        kept = (
                            f"remote {self.remote_name}/{branch_name} kept; "
                            "use --remote to also delete it"
                        )
                        if not dry_run:
                            console.print(
                                f"[green]Deleted branch {branch_name} (local only - {kept})[/green]"
                            )
                        else:
                            console.print(
                                f"[yellow]Would delete branch {branch_name} (local only - {kept})[/yellow]"
                            )
                    else:
                        if not dry_run:
                            console.print(
                                f"[green]Deleted branch {branch_name} (local only)[/green]"
                            )
                        else:
                            console.print(
                                f"[yellow]Would delete branch {branch_name} (local only)[/yellow]"
                            )
                finally:
                    if local_deleted and deleted_sha:
                        self.deletion_journal.record_deletion(
                            branch_name,
                            deleted_sha,
                            had_remote=has_remote,
                            remote_deleted=remote_deleted,
                            remote_name=self.remote_name,
                            batch_id=batch_id,
                        )

                return True

            except Exception as e:  # noqa: BLE001 - deletion must fail closed
                # Deliberately broad: report the failure and keep the branch rather than
                # letting anything unexpected propagate mid-way through a cleanup run.
                console.print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
                return False
            finally:
                self.ref_resolver.invalidate()

    def delete_remote_only_branch(
        self,
        branch_name: str,
        *,
        expected_sha: str,
        dry_run: bool = False,
        batch_id: str | None = None,
        verify_merge: Callable[[], bool] | None = None,
    ) -> RemoteDeletionResult:
        """Delete a remote-only ref if it still has the expected tip.

        This deliberately differs from ``delete_branch``: there is no local head
        to delete first. A force-with-lease expectation makes the operation fail
        closed if the upstream branch advanced after it was analyzed.
        """
        with self._git_operation():
            try:
                repo = self._get_repo()
                state = self.ref_resolver.snapshot(repo, refresh=True).get(branch_name)
                if not state or not state.has_remote or state.has_local:
                    message = "Branch location changed before remote deletion"
                    logger.warning(f"Refusing remote-only deletion of {branch_name}: {message}")
                    return RemoteDeletionResult(
                        success=False,
                        outcome="location_changed",
                        message=message,
                    )
                if state.tip_sha != expected_sha:
                    message = f"Branch tip changed from {expected_sha[:12]} to {state.tip_sha[:12]}"
                    logger.warning(f"Refusing remote-only deletion of {branch_name}: {message}")
                    return RemoteDeletionResult(
                        success=False,
                        outcome="tip_changed",
                        message=message,
                    )

                live_sha, live_error = self.get_live_remote_branch_sha(branch_name)
                if live_error:
                    return RemoteDeletionResult(
                        success=False,
                        outcome="remote_check_failed",
                        message=f"Could not check live remote ref: {live_error}",
                    )
                if live_sha is None:
                    if not dry_run:
                        self._discard_remote_tracking_ref(repo, branch_name, expected_sha)
                    message = f"Remote branch {self.remote_name}/{branch_name} was already absent"
                    console.print(f"[green]{message}[/green]")
                    return RemoteDeletionResult(
                        success=True,
                        outcome="already_absent",
                        message=message,
                    )
                if live_sha != expected_sha:
                    message = f"Remote branch advanced from {expected_sha[:12]} to {live_sha[:12]}"
                    return RemoteDeletionResult(
                        success=False,
                        outcome="tip_changed",
                        message=message,
                    )

                # Validate merge proof only after establishing the live ref exists.
                # This lets callers share this one network check while the push
                # lease still rejects a ref that changes during verification.
                if verify_merge is not None and not verify_merge():
                    return RemoteDeletionResult(
                        success=False,
                        outcome="merge_unverified",
                        message=(
                            "Could not re-establish merge proof for unchanged branch tip "
                            f"{expected_sha[:12]}"
                        ),
                    )

                if dry_run:
                    message = f"Would delete remote-only branch {self.remote_name}/{branch_name}"
                    console.print(f"[yellow]{message}[/yellow]")
                    return RemoteDeletionResult(
                        success=True,
                        outcome="dry_run",
                        message=message,
                    )

                console.print(f"Deleting remote-only branch {self.remote_name}/{branch_name}...")
                try:
                    repo.git.push(
                        self.remote_name,
                        f":refs/heads/{branch_name}",
                        force_with_lease=f"refs/heads/{branch_name}:{expected_sha}",
                    )
                except git.exc.GitCommandError as error:
                    detail = self._git_error_detail(error)
                    return RemoteDeletionResult(
                        success=False,
                        outcome="push_failed",
                        message=f"Remote rejected deletion: {detail}",
                    )
                self.deletion_journal.record_deletion(
                    branch_name,
                    expected_sha,
                    had_remote=True,
                    remote_deleted=True,
                    remote_name=self.remote_name,
                    batch_id=batch_id,
                )
                self._discard_remote_tracking_ref(repo, branch_name, expected_sha)
                console.print(
                    f"[green]Deleted remote-only branch {self.remote_name}/{branch_name}[/green]"
                )
                return RemoteDeletionResult(
                    success=True,
                    outcome="deleted",
                    message=f"Deleted remote branch {self.remote_name}/{branch_name}",
                    remote_deleted=True,
                )
            except Exception as e:  # noqa: BLE001 - deletion must fail closed
                console.print(f"[red]Error deleting remote branch {branch_name}: {e}[/red]")
                return RemoteDeletionResult(
                    success=False,
                    outcome="error",
                    message=f"Unexpected remote deletion error: {e}",
                )
            finally:
                self.ref_resolver.invalidate()
