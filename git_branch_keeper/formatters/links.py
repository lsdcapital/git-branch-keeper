"""GitHub link formatting utilities."""

from typing import Optional
from git_branch_keeper.formatters.branch import format_branch_name


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
