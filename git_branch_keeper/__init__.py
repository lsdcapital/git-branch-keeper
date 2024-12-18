"""
git-branch-keeper - A smart Git branch management tool
"""

__version__ = "0.1.0"

from .core import BranchKeeper
from .cli import main

__all__ = ["BranchKeeper", "main"]
