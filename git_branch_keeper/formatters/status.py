"""Status and deletion formatting utilities."""

from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.constants import STATUS_DISPLAY, BranchStyleType


def format_status(status: BranchStatus) -> str:
    """
    Format branch status as display text.

    Args:
        status: Branch status enum value

    Returns:
        Display text for status
    """
    return STATUS_DISPLAY.get(status.value, status.value)


def format_deletion_reason(status: BranchStatus) -> str:
    """
    Format deletion reason based on branch status.

    Args:
        status: Branch status enum value

    Returns:
        Deletion reason string ("stale" or "merged")
    """
    return "stale" if status == BranchStatus.STALE else "merged"


def format_deletion_confirmation_items(branches: list[BranchDetails]) -> str:
    """
    Format a list of branches for deletion confirmation message.

    Args:
        branches: List of BranchDetails objects to delete

    Returns:
        Formatted string with bullet-pointed list including reason and remote info.
        Each branch is on a separate line with format:
        "  • branch-name (reason, local and remote/local only)"

    Example:
        "  • feature/old (merged, local and remote)\\n  • bugfix/temp (stale, local only)"
    """
    lines = []
    for branch in branches:
        reason = format_deletion_reason(branch.status)
        remote_info = "local and remote" if branch.has_remote else "local only"
        lines.append(f"  • {branch.name} ({reason}, {remote_info})")
    return "\n".join(lines)


def get_branch_style_type(branch: BranchDetails, protected_branches: list[str]) -> str:
    """
    Determine the style type for a branch based on its properties.

    Args:
        branch: Branch details
        protected_branches: List of protected branch names

    Returns:
        BranchStyleType constant
    """
    if branch.name in protected_branches:
        return BranchStyleType.PROTECTED

    if branch.status in [BranchStatus.STALE, BranchStatus.MERGED]:
        # Check if this is an orphaned worktree (directory doesn't exist)
        is_orphaned = branch.notes and "[ORPHANED]" in branch.notes

        # Orphaned worktrees are always deletable (will be cleaned up)
        # This includes both worktree entries and parent branches with orphaned worktrees
        if is_orphaned or branch.worktree_is_orphaned:
            return BranchStyleType.DELETABLE

        # Check if branch has issues preventing deletion
        has_uncommitted = (
            branch.modified_files is True
            or branch.untracked_files is True
            or branch.staged_files is True
        )
        is_in_worktree = branch.in_worktree

        # Debug logging
        from git_branch_keeper.utils.logging import get_logger

        logger = get_logger(__name__)
        logger.debug(
            f"Branch {branch.name}: status={branch.status.value}, in_worktree={is_in_worktree}, has_uncommitted={has_uncommitted}"
        )

        if has_uncommitted or is_in_worktree:
            return BranchStyleType.WARNING  # Can't delete - has issues
        return BranchStyleType.DELETABLE  # Will be deleted

    return BranchStyleType.ACTIVE
