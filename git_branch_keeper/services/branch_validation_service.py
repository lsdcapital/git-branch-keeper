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
            True if branch can be deleted (is stale/merged and not protected)
        """
        return (
            branch.status in [BranchStatus.STALE, BranchStatus.MERGED]
            and branch.name not in protected_branches
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
