"""
git-branch-keeper - A smart Git branch management tool
"""

from .__version__ import __version__
from .cli.main import main
from .core import BranchKeeper

__all__ = ["BranchKeeper", "__version__", "main"]
