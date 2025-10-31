"""
git-branch-keeper - A smart Git branch management tool
"""

from .__version__ import __version__
from .core import BranchKeeper
from .cli.main import main

__all__ = ["BranchKeeper", "main", "__version__"]
