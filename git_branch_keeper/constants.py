"""Shared constants for git-branch-keeper."""

from dataclasses import dataclass
from typing import List


@dataclass
class ColumnDefinition:
    """Definition of a table column."""

    key: str
    label: str
    width: int = 0  # 0 means auto-width


# Unified column definitions for both CLI and TUI
COLUMNS: List[ColumnDefinition] = [
    ColumnDefinition("branch", "Branch", 30),
    ColumnDefinition("status", "Status", 10),
    ColumnDefinition("last_commit", "Last Commit", 12),
    ColumnDefinition("age", "Age", 8),
    ColumnDefinition("changes", "Branch State", 12),
    ColumnDefinition("sync", "Sync", 12),
    ColumnDefinition("remote", "Remote", 8),
    ColumnDefinition("prs", "PRs", 15),
    ColumnDefinition("notes", "Notes", 30),
]


# Symbol constants
SYMBOL_HAS_REMOTE = "✓"
SYMBOL_NO_REMOTE = "✗"
SYMBOL_MARKED = "✓"
SYMBOL_UNMARKED = " "
SYMBOL_CURRENT_BRANCH = " *"


# Status display names
STATUS_DISPLAY = {
    "active": "active",
    "stale": "stale",
    "merged": "merged",
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
