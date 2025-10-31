"""Formatting utilities for git-branch-keeper.

This package provides various formatting functions for displaying branch information,
organized into logical modules:
- date: Date and time formatting
- branch: Branch name and changes formatting
- status: Status and deletion formatting
- links: GitHub link formatting
"""

# Date formatters
from .date import format_date, format_age

# Branch formatters
from .branch import (
    format_remote_status,
    format_branch_name,
    format_branch_name_with_indent,
    format_changes,
)

# Status formatters
from .status import (
    format_status,
    format_deletion_reason,
    format_deletion_confirmation_items,
    get_branch_style_type,
)

# Link formatters
from .links import (
    format_pr_link,
    format_branch_link,
    format_branch_link_with_indent,
)

__all__ = [
    # Date
    "format_date",
    "format_age",
    # Branch
    "format_remote_status",
    "format_branch_name",
    "format_branch_name_with_indent",
    "format_changes",
    # Status
    "format_status",
    "format_deletion_reason",
    "format_deletion_confirmation_items",
    "get_branch_style_type",
    # Links
    "format_pr_link",
    "format_branch_link",
    "format_branch_link_with_indent",
]
