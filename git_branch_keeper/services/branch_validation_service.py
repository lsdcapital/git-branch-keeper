"""Branch validation service for git-branch-keeper."""

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
