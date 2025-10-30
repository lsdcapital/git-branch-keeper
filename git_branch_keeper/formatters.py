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
    if hasattr(date, 'strftime'):
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


def format_status(status: BranchStatus) -> str:
    """
    Format branch status as display text.

    Args:
        status: Branch status enum value

    Returns:
        Display text for status
    """
    return STATUS_DISPLAY.get(status.value, status.value)


def get_branch_style_type(
    branch: BranchDetails,
    protected_branches: list[str]
) -> str:
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
        return BranchStyleType.DELETABLE
    return BranchStyleType.ACTIVE


def is_deletable(branch: BranchDetails, protected_branches: list[str]) -> bool:
    """
    Check if a branch is deletable.

    Args:
        branch: Branch details
        protected_branches: List of protected branch names

    Returns:
        True if branch can be deleted
    """
    return (
        branch.status in [BranchStatus.STALE, BranchStatus.MERGED]
        and branch.name not in protected_branches
    )


def is_protected(branch: BranchDetails, protected_branches: list[str]) -> bool:
    """
    Check if a branch is protected.

    Args:
        branch: Branch details
        protected_branches: List of protected branch names

    Returns:
        True if branch is protected
    """
    return branch.name in protected_branches


def format_pr_link(
    pr_status: Optional[str],
    github_base_url: Optional[str]
) -> str:
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
    if pr_status.startswith('target:'):
        count = pr_status.split(':')[1]
        return f"[link={github_base_url}/pulls]{count}[/link]"

    # For branch with specific PR
    return f"[link={github_base_url}/pull/{pr_status}]{pr_status}[/link]"


def format_branch_link(
    branch_name: str,
    github_base_url: Optional[str],
    is_current: bool = False
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
