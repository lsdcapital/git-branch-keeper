"""Branch validation service for git-branch-keeper."""

from __future__ import annotations

from collections import Counter

from git_branch_keeper.models.branch import BranchDetails, BranchStatus


class BranchValidationService:
    """Service for validating branch operations."""

    @staticmethod
    def is_deletable(branch: BranchDetails, protected_branches: list[str]) -> bool:
        """
        Check if a branch is deletable based on status and protection.

        Args:
            branch: Branch details
            protected_branches: List of protected branch names

        Returns:
            True if branch can be deleted (is stale/merged, not protected, and has no issues)
        """
        # A merged remote-only branch is a first-class cleanup candidate. There is
        # no local ref to inspect or remove, so stale alone is deliberately not
        # enough: remote-only cleanup requires positive merge proof.
        if not branch.has_local:
            return (
                branch.has_remote
                and branch.status == BranchStatus.MERGED
                and branch.name not in protected_branches
            )

        # Check if branch has issues preventing deletion
        has_uncommitted = (
            branch.modified_files is True
            or branch.untracked_files is True
            or branch.staged_files is True
        )
        is_in_worktree = branch.in_worktree

        return (
            branch.status in [BranchStatus.STALE, BranchStatus.MERGED]
            and branch.name not in protected_branches
            and not has_uncommitted
            and not is_in_worktree
        )

    @staticmethod
    def blocking_reason(
        branch: BranchDetails,
        protected_branches: list[str],
        current_branch: str | None = None,
    ) -> str | None:
        """Why a cleanup candidate is not deletable, or None if it is deletable.

        Returns None for branches that were never candidates (active, unstarted) -
        those are not "held back", they simply have no cleanup signal. The order
        mirrors is_deletable() plus the current-branch skip in
        get_deletable_branches(), so the reason given is the one that actually
        decided the outcome.
        """
        if branch.is_worktree or branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
            return None

        if not branch.has_local and not (
            branch.has_remote and branch.status == BranchStatus.MERGED
        ):
            return "remote-only and not confirmed merged"
        if branch.name in protected_branches:
            return "protected"
        if current_branch is not None and branch.name == current_branch:
            return "currently checked out"
        if branch.in_worktree:
            return "checked out in a worktree"
        if (
            branch.modified_files is True
            or branch.untracked_files is True
            or branch.staged_files is True
        ):
            return "has uncommitted changes"
        return None

    @staticmethod
    def summarize_blocked(
        branches: list[BranchDetails],
        protected_branches: list[str],
        current_branch: str | None = None,
    ) -> list[tuple[int, str]]:
        """Count cleanup candidates that were held back, grouped by reason.

        Without this a report can show 36 merged branches and then "No branches to
        clean up!", which reads as a contradiction rather than as the two separate
        facts it is. Ordered most-common-first.
        """
        counts: Counter[str] = Counter()
        for branch in branches:
            reason = BranchValidationService.blocking_reason(
                branch, protected_branches, current_branch
            )
            if reason:
                counts[reason] += 1
        return [(count, reason) for reason, count in counts.most_common()]

    @staticmethod
    def is_protected(branch_name: str, protected_branches: list[str]) -> bool:
        """
        Check if a branch is protected.

        Args:
            branch_name: Name of the branch
            protected_branches: List of protected branch names

        Returns:
            True if branch is protected
        """
        return branch_name in protected_branches

    @staticmethod
    def is_worktree_removable(branch: BranchDetails) -> bool:
        """
        Check if a worktree is removable.

        Deprecated: Use WorktreeService.is_worktree_removable() instead.
        This is kept for backwards compatibility.

        Args:
            branch: Branch details (representing a worktree entry)

        Returns:
            True if worktree can be removed (is orphaned or parent branch is stale/merged)
        """
        from git_branch_keeper.services.git import WorktreeService

        return WorktreeService.is_worktree_removable(branch)
