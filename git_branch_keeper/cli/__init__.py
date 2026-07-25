"""Command-line interface for git-branch-keeper.

This package provides the CLI entry point and argument parsing.
"""

from .args import parse_args
from .main import main

__all__ = ["main", "parse_args"]
