"""Tests that the deletion journal is scoped to a repository, not an invocation path.

The journal is shared across repositories and filtered by a repo key. If that key
is the directory GBK was run from, deletions made inside a linked worktree become
unreachable from `undo` in the main working tree - and disappear for good once the
worktree is removed, which is exactly when the journal matters most.
"""

from pathlib import Path

import pytest

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.services.deletion_journal import DeletionJournal


@pytest.fixture
def repo_with_worktree(git_repo, temp_dir):
    """Yields (repo, linked_worktree_path)."""
    repo = git_repo
    repo.git.branch("feature/wt")
    linked = temp_dir / "linked"
    repo.git.worktree("add", str(linked), "feature/wt")

    yield repo, linked

    try:
        repo.git.worktree("remove", str(linked), "--force")
    except GIT_ERRORS:
        pass
    repo.git.worktree("prune", "--expire=now")


def test_worktree_and_main_repo_share_one_journal_scope(repo_with_worktree, temp_dir):
    repo, linked = repo_with_worktree
    journal_file = temp_dir / "deletions.jsonl"

    from_worktree = DeletionJournal(str(linked), journal_file=journal_file)
    from_main = DeletionJournal(repo.working_dir, journal_file=journal_file)

    assert from_worktree.repo_path == from_main.repo_path

    from_worktree.record_deletion(
        "feature/wt", "a" * 40, had_remote=False, remote_deleted=False, batch_id="b1"
    )

    restored_view = from_main.deletions()
    assert [e["branch"] for e in restored_view] == ["feature/wt"]


def test_repo_key_is_the_main_working_tree(repo_with_worktree):
    repo, linked = repo_with_worktree

    expected = str(Path(repo.working_dir).resolve())
    assert DeletionJournal(str(linked)).repo_path == expected
    assert DeletionJournal(repo.working_dir).repo_path == expected


def test_existing_journals_keyed_by_repo_root_stay_readable(git_repo, temp_dir):
    """The key is unchanged for ordinary runs, so old entries still resolve."""
    repo = git_repo
    journal_file = temp_dir / "deletions.jsonl"

    # An entry as written by the previous path-based scheme.
    legacy = {
        "action": "deleted",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "repo": str(Path(repo.working_dir).resolve()),
        "batch_id": "legacy",
        "branch": "feature/legacy",
        "sha": "b" * 40,
        "had_remote": False,
        "remote_deleted": False,
        "remote": "origin",
    }
    import json

    journal_file.write_text(json.dumps(legacy) + "\n")

    journal = DeletionJournal(repo.working_dir, journal_file=journal_file)
    assert [e["branch"] for e in journal.deletions()] == ["feature/legacy"]


def test_unopenable_path_falls_back_to_the_resolved_path(temp_dir):
    not_a_repo = temp_dir / "nope"
    not_a_repo.mkdir()

    journal = DeletionJournal(str(not_a_repo))
    assert journal.repo_path == str(not_a_repo.resolve())
