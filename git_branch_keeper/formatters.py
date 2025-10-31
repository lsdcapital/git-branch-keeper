"""Shared formatting utilities for git-branch-keeper."""

from typing import Optional, Any
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.constants import (
    SYMBOL_HAS_REMOTE,
    SYMBOL_NO_REMOTE,
    SYMBOL_CURRENT_BRANCH,
    STATUS_DISPLAY,
    BranchStyleType,
)


def format_date(date: Any) -> str:
    """
    Format a date object to YYYY-MM-DD string.

    Args:
        date: Date object (datetime or string)

    Returns:
        Formatted date string
    """
    if hasattr(date, "strftime"):
        return date.strftime("%Y-%m-%d")
    return str(date)


def format_age(age_days: int) -> str:
    """
    Format age in days.

    Args:
        age_days: Number of days

    Returns:
        Formatted age string
    """
    return f"{age_days}d"


def format_remote_status(has_remote: bool) -> str:
    """
    Format remote status as a symbol.

    Args:
        has_remote: Whether branch has a remote

    Returns:
        Symbol for remote status
    """
    return SYMBOL_HAS_REMOTE if has_remote else SYMBOL_NO_REMOTE


def format_branch_name(name: str, is_current: bool = False) -> str:
    """
    Format branch name with optional current branch indicator.

    Args:
        name: Branch name
        is_current: Whether this is the current branch

    Returns:
        Formatted branch name
    """
    return name + (SYMBOL_CURRENT_BRANCH if is_current else "")


def format_branch_name_with_indent(
    name: str, is_worktree: bool = False, is_current: bool = False
) -> str:
    """
    Format branch name with optional indent for worktrees and current branch indicator.

    Args:
        name: Branch name
        is_worktree: Whether this is a worktree entry
        is_current: Whether this is the current branch

    Returns:
        Formatted branch name with indent if worktree
    """
    indent = "  └─ " if is_worktree else ""
    current_marker = SYMBOL_CURRENT_BRANCH if is_current else ""
    return f"{indent}{name}{current_marker}"


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
        from git_branch_keeper.logging_config import get_logger

        logger = get_logger(__name__)
        logger.debug(
            f"Branch {branch.name}: status={branch.status.value}, in_worktree={is_in_worktree}, has_uncommitted={has_uncommitted}"
        )

        if has_uncommitted or is_in_worktree:
            return BranchStyleType.WARNING  # Can't delete - has issues
        return BranchStyleType.DELETABLE  # Will be deleted

    return BranchStyleType.ACTIVE


def format_pr_link(pr_status: Optional[str], github_base_url: Optional[str]) -> str:
    """
    Format PR status with optional link for CLI output.

    Args:
        pr_status: PR status string
        github_base_url: Base GitHub URL (if available)

    Returns:
        Formatted PR display string (may include Rich markup for links)
    """
    if not pr_status:
        return ""

    if not github_base_url:
        return pr_status

    # For main branch showing target PRs
    if pr_status.startswith("target:"):
        count = pr_status.split(":")[1]
        return f"[link={github_base_url}/pulls]{count}[/link]"

    # For branch with specific PR
    return f"[link={github_base_url}/pull/{pr_status}]{pr_status}[/link]"


def format_branch_link(
    branch_name: str, github_base_url: Optional[str], is_current: bool = False
) -> str:
    """
    Format branch name with optional GitHub link for CLI output.

    Args:
        branch_name: Branch name
        github_base_url: Base GitHub URL (if available)
        is_current: Whether this is the current branch

    Returns:
        Formatted branch name (may include Rich markup for link)
    """
    display_name = format_branch_name(branch_name, is_current)

    if not github_base_url:
        return display_name

    return f"[link={github_base_url}/tree/{branch_name}]{display_name}[/link]"


def format_branch_link_with_indent(
    branch_name: str,
    github_base_url: Optional[str],
    is_worktree: bool = False,
    is_current: bool = False,
) -> str:
    """
    Format branch name with optional indent and GitHub link for CLI output.

    Args:
        branch_name: Branch name
        github_base_url: Base GitHub URL (if available)
        is_worktree: Whether this is a worktree entry
        is_current: Whether this is the current branch

    Returns:
        Formatted branch name (may include indent and Rich markup for link)
    """
    indent = "  └─ " if is_worktree else ""
    display_name = format_branch_name(branch_name, is_current)

    if not github_base_url:
        return f"{indent}{display_name}"

    # Add indent before the link
    return f"{indent}[link={github_base_url}/tree/{branch_name}]{display_name}[/link]"


def format_changes(branch: BranchDetails, current_branch: Optional[str] = None) -> str:
    """
    Format branch state indicators showing uncommitted changes.

    Args:
        branch: Branch details
        current_branch: Name of the current branch (optional)

    Returns:
        String with change indicators, @ if current, W if has worktree, ⊢ if is worktree, ✓ if clean, or ⚠ if unknown.
        @ = Current branch (you are here)
        W = Has worktree(s) (branch is checked out elsewhere)
        ⊢ = Is a worktree entry (this is the worktree itself)
        ✓ = Clean (no uncommitted changes)
        ⚠ = Unknown (couldn't check status - see info tab for details)
        M = Modified files
        U = Untracked files
        S = Staged files

    Example:
        "@" for current branch (clean)
        "@MU" for current branch with modified and untracked files
        "W" for branch with worktree(s) (clean)
        "WS" for branch with worktree(s) and staged files
        "⊢✓" for worktree entry (clean)
        "⊢U" for worktree entry with untracked files
        "✓" for clean branch
        "⚠" for unchecked branch (see info tab)
        "MU" for modified and untracked files
        "S" for only staged files
    """
    # Determine location prefix (current branch, has worktree, or is worktree)
    location_prefix = ""
    if branch.is_worktree:
        # This IS a worktree entry
        location_prefix = "⊢"
    elif current_branch and branch.name == current_branch:
        # Current branch (you are here)
        location_prefix = "@"
    elif branch.in_worktree:
        # Has worktree(s) (checked out elsewhere)
        location_prefix = "W"

    # If any status is None, we couldn't check the branch
    if (
        branch.modified_files is None
        or branch.untracked_files is None
        or branch.staged_files is None
    ):
        # Use ⚠ to indicate status couldn't be determined
        # Check if there's an error in notes that the user should see
        return location_prefix + "⚠" if location_prefix else "⚠"

    # Build change indicators
    change_indicators = []
    if branch.modified_files:
        change_indicators.append("M")
    if branch.untracked_files:
        change_indicators.append("U")
    if branch.staged_files:
        change_indicators.append("S")

    # Combine location prefix with change indicators
    if change_indicators:
        return location_prefix + "".join(change_indicators)
    else:
        # Clean branch - show location prefix alone (@ or W), or ✓ if no location
        return location_prefix if location_prefix else "✓"
