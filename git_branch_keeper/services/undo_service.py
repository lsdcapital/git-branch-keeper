"""Shared undo/restore helpers for deleted branches."""

from typing import Dict, List, Optional, Tuple

import git

from git_branch_keeper.services.deletion_journal import DeletionJournal


def _local_branch_names(repo: git.Repo) -> List[str]:
    return [head.name for head in repo.heads]


def pick_entry(
    deletions: List[Dict], repo: git.Repo, target: Optional[str] = None
) -> Optional[Dict]:
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


def restore_entry(
    repo_path: str, entry: Dict, journal: DeletionJournal, include_remote: bool = False
) -> Tuple[bool, Optional[str]]:
    """Restore a branch from a journal entry.

    Returns:
        Tuple of (success, error_message). error_message is None on success.
    """
    branch_name = entry["branch"]
    sha = entry["sha"]

    try:
        repo = git.Repo(repo_path)
    except Exception as e:
        return False, f"Could not open repository: {e}"

    if branch_name in _local_branch_names(repo):
        return False, f"Branch {branch_name} already exists locally"

    try:
        # cat-file -e verifies the object actually exists in the object database
        # (GitPython's repo.commit() creates lazy objects without checking)
        repo.git.cat_file("-e", f"{sha}^{{commit}}")
    except Exception:
        return False, (
            f"Commit {sha[:12]} no longer exists in this repository "
            "(it may have been garbage-collected)"
        )

    try:
        repo.create_head(branch_name, sha)
    except Exception as e:
        return False, f"Could not recreate branch: {e}"

    if include_remote and entry.get("remote_deleted"):
        try:
            remote = repo.remote(entry.get("remote", "origin"))
            remote.push(refspec=f"{sha}:refs/heads/{branch_name}")
        except Exception as e:
            journal.record_restore(branch_name, sha)
            return False, f"Branch restored locally, but remote push failed: {e}"

    journal.record_restore(branch_name, sha)
    return True, None
