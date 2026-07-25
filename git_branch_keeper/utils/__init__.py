"""Utility functions for git-branch-keeper.

This package provides utility modules:
- logging: Logging configuration and logger creation
- threading: Threading utilities for Python 3.13+ free-threading support
"""

from .logging import ColoredFormatter, get_logger, setup_logging
from .threading import (
    get_optimal_worker_count,
    get_python_threading_mode,
    get_threading_info,
    is_free_threading_enabled,
)

__all__ = [
    "ColoredFormatter",
    "get_logger",
    "get_optimal_worker_count",
    "get_python_threading_mode",
    "get_threading_info",
    "is_free_threading_enabled",
    "setup_logging",
]
