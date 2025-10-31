"""Threading utilities for detecting and optimizing Python threading mode."""

import os
import sys
from typing import Dict, Any, Optional


def is_free_threading_enabled() -> bool:
    """Detect if Python is running with free-threading enabled.

    Returns:
        True if running on Python 3.13+ with GIL disabled (free-threading mode)
        False if running with GIL enabled or Python < 3.13
    """
    try:
        # sys._is_gil_enabled() returns False when GIL is disabled
        # Available in Python 3.13+
        return hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()
    except Exception:
        # Python < 3.13 always has GIL enabled
        return False


def get_python_threading_mode() -> str:
    """Get a description of the current threading mode.

    Returns:
        String describing threading mode: "free-threading", "GIL-enabled", or "GIL-enabled (Python < 3.13)"
    """
    try:
        if hasattr(sys, "_is_gil_enabled"):
            if sys._is_gil_enabled():
                return "GIL-enabled"
            else:
                return "free-threading"
        else:
            return "GIL-enabled (Python < 3.13)"
    except Exception:
        return "unknown"


def get_optimal_worker_count(user_specified: Optional[int] = None) -> int:
    """Calculate optimal worker count based on threading mode and CPU count.

    Args:
        user_specified: User-specified worker count, if provided

    Returns:
        Optimal number of workers for parallel processing
    """
    # If user specified, use that value
    if user_specified is not None and user_specified > 0:
        return user_specified

    cpu_count = os.cpu_count() or 1

    try:
        # Python 3.13+ with free-threading: can use more workers for true parallelism
        if is_free_threading_enabled():
            # Free-threading: use more workers (CPU_count * 2)
            # Cap at 64 to avoid excessive overhead
            return min(64, cpu_count * 2)
    except Exception:
        pass

    # GIL-enabled or older Python: use fewer workers for I/O-bound operations
    # CPU_count + 4 is a good heuristic for I/O-bound work
    # Cap at 32 to be conservative
    return min(32, cpu_count + 4)


def get_threading_info() -> Dict[str, Any]:
    """Get comprehensive information about Python threading configuration.

    Returns:
        Dictionary containing threading mode, worker count, and other details
    """
    return {
        "mode": get_python_threading_mode(),
        "free_threading": is_free_threading_enabled(),
        "cpu_count": os.cpu_count() or 1,
        "optimal_workers": get_optimal_worker_count(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
