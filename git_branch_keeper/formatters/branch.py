"""Branch name and changes formatting utilities."""

from typing import Optional
from git_branch_keeper.models.branch import BranchDetails
from git_branch_keeper.constants import (
    SYMBOL_HAS_REMOTE,
    SYMBOL_NO_REMOTE,
    SYMBOL_CURRENT_BRANCH,
)


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
