"""Command-line interface for git-branch-keeper.

This package provides the CLI entry point and argument parsing.
"""

from .main import main
from .args import parse_args

__all__ = ["main", "parse_args"]
