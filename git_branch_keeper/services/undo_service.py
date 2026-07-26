"""Shared undo/restore helpers for deleted branches."""

from __future__ import annotations

import git

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.services.deletion_journal import DeletionJournal


def _local_branch_names(repo: git.Repo) -> list[str]:
    return [head.name for head in repo.heads]


def pick_entry(deletions: list[dict], repo: git.Repo, target: str | None = None) -> dict | None:
    """Pick the journal entry to restore.

    With a target branch name, returns its most recent deletion entry.
    Without one, returns the most recent deletion whose branch does not
    currently exist locally (so repeated undo walks back through history).
    """
    existing = set(_local_branch_names(repo))
    for entry in reversed(deletions):
        if target is not None:
            if entry["branch"] == target:
                return entry
        elif entry["branch"] not in existing:
            return entry
    return None


def pick_latest_batch(deletions: list[dict], repo: git.Repo) -> list[dict]:
    """Pick the latest deletion batch with at least one missing local branch.

    Returns entries in original deletion order so restored branch creation is
    deterministic. Entries whose branches already exist locally are skipped.
    """
    existing = set(_local_branch_names(repo))

    latest_batch_id = None
    for entry in reversed(deletions):
        if entry["branch"] in existing:
            continue
        latest_batch_id = entry.get("batch_id")
        break

    if latest_batch_id is None:
        return []

    return [
        entry
        for entry in deletions
        if entry.get("batch_id") == latest_batch_id and entry["branch"] not in existing
    ]


def restore_entries(
    repo_path: str,
    entries: list[dict],
    journal: DeletionJournal,
    include_remote: bool = False,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Restore multiple deletion journal entries.

    Returns:
        Tuple of (restored_branch_names, failed_items). failed_items contains
        tuples of (branch_name, error_message).
    """
    restored = []
    failed = []

    for entry in entries:
        success, error = restore_entry(repo_path, entry, journal, include_remote=include_remote)
        if success:
            restored.append(entry["branch"])
        else:
            failed.append((entry["branch"], error or "Unknown error"))

    return restored, failed


def restore_entry(
    repo_path: str, entry: dict, journal: DeletionJournal, include_remote: bool = False
) -> tuple[bool, str | None]:
    """Restore a branch from a journal entry.

    Returns:
        Tuple of (success, error_message). error_message is None on success.
    """
    branch_name = entry["branch"]
    sha = entry["sha"]

    try:
        repo = git.Repo(repo_path)
    except GIT_ERRORS as e:
        return False, f"Could not open repository: {e}"

    if branch_name in _local_branch_names(repo):
        return False, f"Branch {branch_name} already exists locally"

    try:
        # cat-file -e verifies the object actually exists in the object database
        # (GitPython's repo.commit() creates lazy objects without checking)
        repo.git.cat_file("-e", f"{sha}^{{commit}}")
    except GIT_ERRORS:
        return False, (
            f"Commit {sha[:12]} no longer exists in this repository "
            "(it may have been garbage-collected)"
        )

    try:
        repo.create_head(branch_name, sha)
    except GIT_ERRORS as e:
        return False, f"Could not recreate branch: {e}"

    if include_remote and entry.get("remote_deleted"):
        try:
            remote = repo.remote(entry.get("remote", "origin"))
            remote.push(refspec=f"{sha}:refs/heads/{branch_name}")
        except GIT_ERRORS as e:
            journal.record_restore(branch_name, sha, batch_id=entry.get("batch_id"))
            return False, f"Branch restored locally, but remote push failed: {e}"

    journal.record_restore(branch_name, sha, batch_id=entry.get("batch_id"))
    return True, None
