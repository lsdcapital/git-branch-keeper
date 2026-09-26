"""Branch-name pattern matching shared by protection checks."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase


def is_protected_name(branch_name: str, protected_branches: Iterable[str]) -> bool:
    """Whether a branch matches any `protected_branches` entry (exact name or glob).

    Globs such as `release/*` are safe to accept here: git refuses `*`, `?` and `[`
    in ref names, so a pattern can never be mistaken for a literal branch name.
    Case-sensitive, like git refs.
    """
    return any(fnmatchcase(branch_name, pattern) for pattern in protected_branches)
