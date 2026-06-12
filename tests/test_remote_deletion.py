"""Tests for opt-in remote branch deletion (local-only by default)."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.services.git import GitOperations


@pytest.fixture
def isolated_home(temp_dir, monkeypatch):
    """Redirect Path.home() so journals write under the temp dir, not real home."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def _ops_with_mocked_remote(git_repo, mock_config):
    """Build GitOperations whose remote push is mocked and whose branch has a remote."""
    ops = GitOperations(git_repo.working_dir, mock_config)
    mock_repo = Mock()
    mock_remote = Mock()
    mock_repo.remote.return_value = mock_remote
    mock_repo.delete_head = Mock()
    # Tip SHA lookup: repo.heads[name].commit.hexsha
    head = Mock()
    head.commit.hexsha = "a" * 40
    mock_repo.heads = {"feature/x": head}
    return ops, mock_repo, mock_remote


class TestRemoteDeletionOptIn:
    def test_local_only_by_default_keeps_remote(self, git_repo, mock_config, isolated_home):
        ops, mock_repo, mock_remote = _ops_with_mocked_remote(git_repo, mock_config)

        with (
            patch.object(ops, "has_remote_branch", return_value=True),
            patch.object(ops, "_get_repo", return_value=mock_repo),
        ):
            result = ops.delete_branch("feature/x", dry_run=False)  # delete_remote defaults False

        assert result is True
        mock_repo.delete_head.assert_called_once()  # local deletion happened
        mock_remote.push.assert_not_called()  # remote was kept

        entry = ops.deletion_journal.deletions()[-1]
        assert entry["had_remote"] is True
        assert entry["remote_deleted"] is False

    def test_remote_deleted_when_opted_in(self, git_repo, mock_config, isolated_home):
        ops, mock_repo, mock_remote = _ops_with_mocked_remote(git_repo, mock_config)

        with (
            patch.object(ops, "has_remote_branch", return_value=True),
            patch.object(ops, "_get_repo", return_value=mock_repo),
        ):
            result = ops.delete_branch("feature/x", dry_run=False, delete_remote=True)

        assert result is True
        mock_repo.delete_head.assert_called_once()
        mock_remote.push.assert_called_once_with(refspec=":feature/x")

        entry = ops.deletion_journal.deletions()[-1]
        assert entry["had_remote"] is True
        assert entry["remote_deleted"] is True

    def test_no_remote_branch_unaffected_by_flag(
        self, git_repo_with_branches, mock_config, isolated_home
    ):
        # feature/to-merge exists locally but was never pushed -> no remote
        ops = GitOperations(git_repo_with_branches.working_dir, mock_config)
        assert ops.delete_branch("feature/to-merge", delete_remote=True) is True

        entry = ops.deletion_journal.deletions()[-1]
        assert entry["had_remote"] is False
        assert entry["remote_deleted"] is False


class TestConfigWiring:
    def test_config_defaults_to_local_only(self):
        assert Config().delete_remote is False

    def test_config_roundtrips_delete_remote(self):
        cfg = Config(delete_remote=True)
        assert cfg.to_dict()["delete_remote"] is True
        assert Config.from_dict({"delete_remote": True}).delete_remote is True
