"""GitHub link formatting utilities."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

from git_branch_keeper.formatters.branch import format_branch_name


def _pr_display_and_url(
    pr_status: str | None, github_base_url: str | None
) -> tuple[str, str | None]:
    """Return compact PR text and its destination URL."""
    if not pr_status:
        return "", None

    if pr_status.startswith("target:"):
        count = pr_status.split(":", 1)[1]
        url = f"{github_base_url}/pulls" if github_base_url else None
        return count, url

    number = pr_status.removeprefix("#")
    url = f"{github_base_url}/pull/{number}" if github_base_url else None
    return f"#{number}", url


def format_pr_link(pr_status: str | None, github_base_url: str | None) -> str:
    """
    Format PR status with optional link for CLI output.

    Args:
        pr_status: PR status string
        github_base_url: Base GitHub URL (if available)

    Returns:
        Formatted PR display string (may include Rich markup for links)
    """
    display, url = _pr_display_and_url(pr_status, github_base_url)
    if not display:
        return ""
    if not url:
        return display
    return f"[link={url}]{display}[/link]"


def format_pr_text(pr_status: str | None, github_base_url: str | None) -> Text:
    """Format a PR as a directly clickable Textual/Rich renderable."""
    display, url = _pr_display_and_url(pr_status, github_base_url)
    if not url:
        return Text(display)

    style = Style(
        underline=True,
        link=url,
        meta={"@click": ("app.open_pr", (url,))},
    )
    return Text(display, style=style)


def format_branch_link(
    branch_name: str, github_base_url: str | None, is_current: bool = False
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
    github_base_url: str | None,
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
