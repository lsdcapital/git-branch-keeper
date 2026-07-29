"""Shared constants for git-branch-keeper."""

from dataclasses import dataclass


@dataclass
class ColumnDefinition:
    """Definition of a table column."""

    key: str
    label: str
    width: int = 0  # 0 means auto-width


# Unified column definitions for both CLI and TUI
COLUMNS: list[ColumnDefinition] = [
    ColumnDefinition("branch", "Branch", 30),
    ColumnDefinition("status", "Status", 10),
    ColumnDefinition("last_commit", "Last Commit", 12),
    ColumnDefinition("age", "Age", 8),
    ColumnDefinition("changes", "Branch State", 12),
    ColumnDefinition("sync", "Sync", 12),
    ColumnDefinition("location", "Location", 8),
    ColumnDefinition("prs", "PRs", 15),
    ColumnDefinition("notes", "Notes", 30),
]


# Branch location labels. Plain text keeps the three states understandable
# without a legend and avoids reusing ✓ for both selection and remote presence.
LOCATION_LOCAL = "local"
LOCATION_BOTH = "both"
LOCATION_REMOTE = "remote"
SYMBOL_MARKED = "✓"
SYMBOL_UNMARKED = " "
SYMBOL_CURRENT_BRANCH = " *"


# Status display names
STATUS_DISPLAY = {
    "active": "active",
    "stale": "stale",
    "merged": "merged",
    "unstarted": "unstarted",
}


# Color/style constants for different branch states
class BranchStyleType:
    """Style types for branches."""

    PROTECTED = "protected"
    DELETABLE = "deletable"
    WARNING = "warning"  # Has issues preventing deletion
    ACTIVE = "active"


# CLI colors (Rich color names)
CLI_COLORS = {
    BranchStyleType.PROTECTED: "cyan",
    BranchStyleType.DELETABLE: "red",  # Will be deleted
    BranchStyleType.WARNING: "yellow",  # Can't delete (has issues)
    BranchStyleType.ACTIVE: None,  # Default color
}


# TUI colors (color names for Textual)
TUI_COLORS = {
    BranchStyleType.PROTECTED: "cyan",
    BranchStyleType.DELETABLE: "red",  # Will be deleted
    BranchStyleType.WARNING: "yellow",  # Can't delete (has issues)
    BranchStyleType.ACTIVE: "green",
}


# Legend text for CLI summary
LEGEND_TEXT = """
Legend:
✓ = Has remote branch     ✗ = Local only
@ = Current branch        W = Has worktree(s)
⊢ = Is a worktree         M = Modified files
U = Untracked files       S = Staged files
⚠ = Status unknown (press i for error details)

Colors:
Red = Will be deleted
Yellow = Has issues (can't delete)
Cyan = Protected branch
"""
