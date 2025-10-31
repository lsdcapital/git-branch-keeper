"""Utility functions for git-branch-keeper.

This package provides utility modules:
- logging: Logging configuration and logger creation
- threading: Threading utilities for Python 3.13+ free-threading support
"""

from .logging import setup_logging, get_logger, ColoredFormatter
from .threading import (
    is_free_threading_enabled,
    get_python_threading_mode,
    get_optimal_worker_count,
    get_threading_info,
)

__all__ = [
    # Logging
    "setup_logging",
    "get_logger",
    "ColoredFormatter",
    # Threading
    "is_free_threading_enabled",
    "get_python_threading_mode",
    "get_optimal_worker_count",
    "get_threading_info",
]
