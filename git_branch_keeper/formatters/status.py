"""Status and deletion formatting utilities."""

from git_branch_keeper.constants import STATUS_DISPLAY, BranchStyleType
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.utils.patterns import is_protected_name


def format_status(status: BranchStatus) -> str:
    """
    Format branch status as display text.

    Args:
        status: Branch status enum value

    Returns:
        Display text for status
    """
    return STATUS_DISPLAY.get(status.value, status.value)


def has_cleanup_blockers(
    branch: BranchDetails,
    protected_branches: list[str],
    allow_remote_deletion: bool = False,
) -> bool:
    """Return True when a stale/merged row is blocked from cleanup.

    The underlying merge/stale status remains true, but displaying a plain
    ``merged`` label for dirty/protected cleanup candidates is misleading: from
    the user's perspective those rows still need attention before safe cleanup.
    """
    if branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
        return False

    if is_protected_name(branch.name, protected_branches):
        return True

    if not branch.has_local and not (branch.has_remote and branch.status == BranchStatus.MERGED):
        return True
    if not branch.has_local and not allow_remote_deletion:
        return True

    return (
        branch.modified_files is True
        or branch.untracked_files is True
        or branch.staged_files is True
    )


def format_display_status(
    branch: BranchDetails,
    protected_branches: list[str],
    allow_remote_deletion: bool = False,
) -> str:
    """Show branch history independently of cleanup eligibility."""
    if branch.status == BranchStatus.UNSTARTED and not branch.has_local:
        return "already in main"
    return format_status(branch.status)


def format_cleanup_reason(
    branch: BranchDetails,
    protected_branches: list[str],
    current_branch: str | None = None,
    allow_remote_deletion: bool = False,
    remote_deletion_enabled: bool = False,
    remote_opt_in_hint: str | None = None,
) -> str:
    """Explain eligibility without hiding the branch's merge/stale status.

    `remote_opt_in_hint` replaces the CLI flag in the "needs --remote" reason for
    surfaces that opt in differently (the TUI's `d` key).
    """
    from git_branch_keeper.services.branch_validation_service import (
        REMOTE_CLEANUP_NOT_ENABLED,
        BranchValidationService,
    )

    if is_protected_name(branch.name, protected_branches):
        return "protected"
    if branch.name == current_branch and not branch.is_worktree:
        return "currently checked out"
    if not branch.has_local and branch.status == BranchStatus.UNSTARTED:
        return "merge history unclear"
    if branch.is_worktree:
        if any(
            v is True for v in (branch.modified_files, branch.untracked_files, branch.staged_files)
        ):
            return "has uncommitted changes"
        return ""
    reason = BranchValidationService.blocking_reason(
        branch,
        protected_branches,
        current_branch,
        allow_remote_deletion=allow_remote_deletion,
    )
    if reason == REMOTE_CLEANUP_NOT_ENABLED:
        if remote_deletion_enabled:
            return "outside selected remote scope"
        if remote_opt_in_hint:
            return remote_opt_in_hint
    if reason == "remote-only and not confirmed merged":
        return "merge not confirmed"
    return reason or ""


def format_deletion_reason(status: BranchStatus) -> str:
    """
    Format deletion reason based on branch status.

    Args:
        status: Branch status enum value

    Returns:
        Deletion reason string ("stale", "unstarted" or "merged")

    UNSTARTED must not become "merged": an unstarted tip is trivially reachable from
    main, so the deletion-time merge re-check would pass and the journal would record
    a merge that never happened.
    """
    if status == BranchStatus.STALE:
        return "stale"
    if status == BranchStatus.UNSTARTED:
        return "unstarted"
    return "merged"


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


def get_branch_style_type(
    branch: BranchDetails,
    protected_branches: list[str],
    allow_remote_deletion: bool = False,
) -> str:
    """
    Determine the style type for a branch based on its properties.

    Args:
        branch: Branch details
        protected_branches: List of protected branch names

    Returns:
        BranchStyleType constant
    """
    if is_protected_name(branch.name, protected_branches):
        return BranchStyleType.PROTECTED

    if branch.status in [BranchStatus.STALE, BranchStatus.MERGED]:
        if not branch.has_local and not allow_remote_deletion:
            return BranchStyleType.WARNING

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
