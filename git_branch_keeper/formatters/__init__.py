"""Formatting utilities for git-branch-keeper.

This package provides various formatting functions for displaying branch information,
organized into logical modules:
- date: Date and time formatting
- branch: Branch name and changes formatting
- status: Status and deletion formatting
- links: GitHub link formatting
"""

# Date formatters
# Branch formatters
from .branch import (
    format_branch_name,
    format_branch_name_with_indent,
    format_changes,
    format_remote_status,
)
from .date import format_age, format_date

# Link formatters
from .links import (
    format_branch_link,
    format_branch_link_with_indent,
    format_pr_link,
)

# Status formatters
from .status import (
    format_deletion_confirmation_items,
    format_deletion_reason,
    format_display_status,
    format_status,
    get_branch_style_type,
)

__all__ = [
    "format_age",
    "format_branch_link",
    "format_branch_link_with_indent",
    "format_branch_name",
    "format_branch_name_with_indent",
    "format_changes",
    "format_date",
    "format_deletion_confirmation_items",
    "format_deletion_reason",
    "format_display_status",
    "format_pr_link",
    "format_remote_status",
    "format_status",
    "get_branch_style_type",
]
