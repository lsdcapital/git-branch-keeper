"""Tests for the deletion journal and undo/restore functionality."""

from pathlib import Path

import git
import pytest

from git_branch_keeper.cli.undo import pick_entry, restore_entry
from git_branch_keeper.services.deletion_journal import DeletionJournal
from git_branch_keeper.services.git import GitOperations


@pytest.fixture
def journal_file(temp_dir):
    """Journal file in a temp location (never touch the real home dir)."""
    return temp_dir / "deletions.jsonl"


@pytest.fixture
def isolated_home(temp_dir, monkeypatch):
    """Redirect Path.home() so services write journals under the temp dir."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


class TestDeletionJournal:
    def test_record_and_read_back(self, journal_file):
        journal = DeletionJournal("/repo/a", journal_file=journal_file)
        journal.record_deletion("feature/one", "a" * 40, had_remote=True, remote_deleted=True)
        journal.record_deletion("feature/two", "b" * 40, had_remote=False, remote_deleted=False)

        deletions = journal.deletions()
        assert [e["branch"] for e in deletions] == ["feature/one", "feature/two"]
        assert deletions[0]["sha"] == "a" * 40
        assert deletions[0]["remote_deleted"] is True
        assert deletions[1]["had_remote"] is False

    def test_entries_scoped_to_repo(self, journal_file):
        journal_a = DeletionJournal("/repo/a", journal_file=journal_file)
        journal_b = DeletionJournal("/repo/b", journal_file=journal_file)
        journal_a.record_deletion("only-in-a", "a" * 40, had_remote=False, remote_deleted=False)
        journal_b.record_deletion("only-in-b", "b" * 40, had_remote=False, remote_deleted=False)

        assert [e["branch"] for e in journal_a.deletions()] == ["only-in-a"]
        assert [e["branch"] for e in journal_b.deletions()] == ["only-in-b"]

    def test_corrupt_lines_are_skipped(self, journal_file):
        journal = DeletionJournal("/repo/a", journal_file=journal_file)
        journal.record_deletion("good", "a" * 40, had_remote=False, remote_deleted=False)
        with open(journal_file, "a") as f:
            f.write("not json at all\n")
        journal.record_deletion("also-good", "b" * 40, had_remote=False, remote_deleted=False)

        assert [e["branch"] for e in journal.deletions()] == ["good", "also-good"]

    def test_restores_excluded_from_deletions(self, journal_file):
        journal = DeletionJournal("/repo/a", journal_file=journal_file)
        journal.record_deletion("branch", "a" * 40, had_remote=False, remote_deleted=False)
        journal.record_restore("branch", "a" * 40)

        assert len(journal.deletions()) == 1

    def test_missing_journal_file(self, journal_file):
        journal = DeletionJournal("/repo/a", journal_file=journal_file)
        assert journal.deletions() == []


class TestDeleteBranchJournaling:
    def test_delete_branch_writes_journal_entry(
        self, git_repo_with_branches, mock_config, isolated_home
    ):
        repo = git_repo_with_branches
        repo_path = repo.working_dir
        expected_sha = repo.heads["feature/to-merge"].commit.hexsha

        ops = GitOperations(repo_path, mock_config)
        assert ops.delete_branch("feature/to-merge") is True

        assert "feature/to-merge" not in [h.name for h in repo.heads]
        deletions = ops.deletion_journal.deletions()
        assert len(deletions) == 1
        assert deletions[0]["branch"] == "feature/to-merge"
        assert deletions[0]["sha"] == expected_sha
        assert deletions[0]["remote_deleted"] is False

    def test_dry_run_writes_no_journal_entry(
        self, git_repo_with_branches, mock_config, isolated_home
    ):
        repo = git_repo_with_branches
        ops = GitOperations(repo.working_dir, mock_config)

        assert ops.delete_branch("feature/to-merge", dry_run=True) is True

        assert "feature/to-merge" in [h.name for h in repo.heads]
        assert ops.deletion_journal.deletions() == []


class TestUndo:
    def _delete_and_get_journal(self, repo, mock_config):
        ops = GitOperations(repo.working_dir, mock_config)
        assert ops.delete_branch("feature/to-merge") is True
        return ops.deletion_journal

    def test_restore_entry_recreates_branch_at_same_sha(
        self, git_repo_with_branches, mock_config, isolated_home
    ):
        repo = git_repo_with_branches
        expected_sha = repo.heads["feature/to-merge"].commit.hexsha
        journal = self._delete_and_get_journal(repo, mock_config)

        entry = journal.deletions()[-1]
        success, error = restore_entry(repo.working_dir, entry, journal)

        assert success is True, error
        restored = git.Repo(repo.working_dir).heads["feature/to-merge"]
        assert restored.commit.hexsha == expected_sha

    def test_restore_entry_refuses_existing_branch(
        self, git_repo_with_branches, mock_config, isolated_home
    ):
        repo = git_repo_with_branches
        journal = self._delete_and_get_journal(repo, mock_config)
        entry = journal.deletions()[-1]

        assert restore_entry(repo.working_dir, entry, journal)[0] is True
        success, error = restore_entry(repo.working_dir, entry, journal)
        assert success is False
        assert "already exists" in error

    def test_restore_entry_handles_missing_commit(
        self, git_repo_with_branches, mock_config, isolated_home, journal_file
    ):
        repo = git_repo_with_branches
        journal = DeletionJournal(repo.working_dir, journal_file=journal_file)
        journal.record_deletion("ghost", "0" * 40, had_remote=False, remote_deleted=False)

        success, error = restore_entry(repo.working_dir, journal.deletions()[-1], journal)
        assert success is False
        assert "no longer exists" in error

    def test_pick_entry_skips_existing_branches(
        self, git_repo_with_branches, mock_config, isolated_home
    ):
        repo = git_repo_with_branches
        journal = self._delete_and_get_journal(repo, mock_config)
        deletions = journal.deletions()

        # Most recent deletion whose branch is gone -> picked
        assert pick_entry(deletions, repo)["branch"] == "feature/to-merge"

        # Once restored, there is nothing left to pick
        restore_entry(repo.working_dir, deletions[-1], journal)
        assert pick_entry(journal.deletions(), git.Repo(repo.working_dir)) is None

    def test_pick_entry_with_target(self, git_repo_with_branches, mock_config, isolated_home):
        repo = git_repo_with_branches
        journal = self._delete_and_get_journal(repo, mock_config)

        entry = pick_entry(journal.deletions(), repo, target="feature/to-merge")
        assert entry is not None
        assert entry["branch"] == "feature/to-merge"
        assert pick_entry(journal.deletions(), repo, target="never-existed") is None
