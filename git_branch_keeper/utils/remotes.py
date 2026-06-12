"""Helpers for working with git remotes."""

from git_branch_keeper.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_REMOTE = "origin"


def detect_remote_name(repo) -> str:
    """Determine which remote to operate on.

    Preference order, chosen to keep the common case identical to before:
    1. "origin" if it exists (the overwhelming default).
    2. The sole remote, if the repo has exactly one with a different name.
    3. "origin" as a harmless fallback (downstream lookups fail gracefully).

    Args:
        repo: A git.Repo instance.

    Returns:
        The remote name to use.
    """
    try:
        names = [remote.name for remote in repo.remotes]
    except Exception as e:
        logger.debug(f"Could not enumerate remotes: {e}")
        return DEFAULT_REMOTE

    if DEFAULT_REMOTE in names:
        return DEFAULT_REMOTE
    if len(names) == 1:
        logger.debug(f"No 'origin' remote; using sole remote '{names[0]}'")
        return names[0]
    if len(names) > 1:
        logger.debug(
            f"No 'origin' remote and multiple remotes {names}; "
            f"falling back to '{DEFAULT_REMOTE}' (set one explicitly if this is wrong)"
        )
    return DEFAULT_REMOTE


def get_remote_url(repo, remote_name: str):
    """Return the URL of the named remote, or None if it does not exist.

    Args:
        repo: A git.Repo instance.
        remote_name: Name of the remote.

    Returns:
        The remote URL string, or None.
    """
    try:
        return repo.remote(remote_name).url
    except Exception:
        return None
