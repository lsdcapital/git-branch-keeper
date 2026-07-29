"""Status and deletion formatting utilities."""

from git_branch_keeper.constants import STATUS_DISPLAY, BranchStyleType
from git_branch_keeper.models.branch import BranchDetails, BranchStatus


def format_status(status: BranchStatus) -> str:
    """
    Format branch status as display text.

    Args:
        status: Branch status enum value

    Returns:
        Display text for status
    """
    return STATUS_DISPLAY.get(status.value, status.value)


def has_cleanup_blockers(branch: BranchDetails, protected_branches: list[str]) -> bool:
    """Return True when a stale/merged row is blocked from cleanup.

    The underlying merge/stale status remains true, but displaying a plain
    ``merged`` label for dirty/protected cleanup candidates is misleading: from
    the user's perspective those rows still need attention before safe cleanup.
    """
    if branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
        return False

    if branch.name in protected_branches:
        return True

    if not branch.has_local and not (
        branch.has_remote and branch.status == BranchStatus.MERGED
    ):
        return True

    return (
        branch.modified_files is True
        or branch.untracked_files is True
        or branch.staged_files is True
    )


def format_display_status(branch: BranchDetails, protected_branches: list[str]) -> str:
    """Format status for cleanup-focused CLI/TUI display."""
    if has_cleanup_blockers(branch, protected_branches):
        return "blocked"
    return format_status(branch.status)


def format_deletion_reason(status: BranchStatus) -> str:
    """
    Format deletion reason based on branch status.

    Args:
        status: Branch status enum value

    Returns:
        Deletion reason string ("stale" or "merged")
    """
    return "stale" if status == BranchStatus.STALE else "merged"


def format_deletion_confirmation_items(
    branches: list[BranchDetails], delete_remote: bool = False
) -> str:
    """
    Format a list of branches for deletion confirmation message.

    Args:
        branches: List of BranchDetails objects to delete
        delete_remote: Whether remote branches will also be deleted

    Returns:
        Formatted string with bullet-pointed list including reason and remote info.
        Each branch is on a separate line with format:
        "  • branch-name (reason, <scope>)"

    Example:
        "  • feature/old (merged, local and remote)\\n  • bugfix/temp (stale, local only)"
    """
    lines = []
    for branch in branches:
        reason = format_deletion_reason(branch.status)
        if branch.has_remote and not branch.has_local:
            remote_info = "remote only"
        elif branch.has_remote:
            remote_info = "local and remote" if delete_remote else "local only, remote kept"
        else:
            remote_info = "local only"
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
        # Remote-only cleanup needs positive merge proof. A stale remote ref is
        # visible for review but is not safe to delete automatically.
        if not branch.has_local and not (
            branch.has_remote and branch.status == BranchStatus.MERGED
        ):
            return BranchStyleType.WARNING

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

        if has_uncommitted:
            return BranchStyleType.WARNING  # Can't delete - has uncommitted changes
        if is_in_worktree:
            # Clean merged/stale worktree checkouts are cleanup candidates: GBK
            # removes the worktree first, then deletes the now-unblocked branch.
            return BranchStyleType.DELETABLE
        return BranchStyleType.DELETABLE  # Will be deleted

    return BranchStyleType.ACTIVE
