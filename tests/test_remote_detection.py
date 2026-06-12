"""Tests for remote-name detection (no longer hardcoded to 'origin')."""

import git

from git_branch_keeper.services.git import GitOperations
from git_branch_keeper.utils.remotes import detect_remote_name, get_remote_url


def _init_repo(path):
    repo = git.Repo.init(path)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.co").release()
    (path / "a.txt").write_text("a\n")
    repo.index.add(["a.txt"])
    repo.index.commit("init")
    return repo


class TestDetectRemoteName:
    def test_prefers_origin(self, temp_dir):
        repo = _init_repo(temp_dir)
        repo.create_remote("upstream", "git@github.com:o/up.git")
        repo.create_remote("origin", "git@github.com:o/orig.git")
        assert detect_remote_name(repo) == "origin"

    def test_uses_sole_non_origin_remote(self, temp_dir):
        repo = _init_repo(temp_dir)
        repo.create_remote("upstream", "git@github.com:o/up.git")
        assert detect_remote_name(repo) == "upstream"

    def test_no_remotes_falls_back_to_origin(self, temp_dir):
        repo = _init_repo(temp_dir)
        assert detect_remote_name(repo) == "origin"

    def test_multiple_no_origin_falls_back_to_origin(self, temp_dir):
        repo = _init_repo(temp_dir)
        repo.create_remote("upstream", "git@github.com:o/up.git")
        repo.create_remote("fork", "git@github.com:o/fork.git")
        assert detect_remote_name(repo) == "origin"


class TestGetRemoteUrl:
    def test_returns_url(self, temp_dir):
        repo = _init_repo(temp_dir)
        repo.create_remote("upstream", "git@github.com:o/up.git")
        assert get_remote_url(repo, "upstream") == "git@github.com:o/up.git"

    def test_missing_remote_returns_none(self, temp_dir):
        repo = _init_repo(temp_dir)
        assert get_remote_url(repo, "nope") is None


class TestGitOperationsUsesDetectedRemote:
    def test_picks_up_non_origin_remote(self, temp_dir, mock_config):
        repo = _init_repo(temp_dir)
        repo.create_remote("upstream", "git@github.com:o/up.git")

        ops = GitOperations(str(temp_dir), mock_config)
        assert ops.remote_name == "upstream"
        assert ops.branch_queries.remote_name == "upstream"

    def test_bad_path_does_not_fail_construction(self, temp_dir, mock_config):
        # Construction must stay lazy: a bad path falls back to the default.
        ops = GitOperations(str(temp_dir / "nonexistent"), mock_config)
        assert ops.remote_name == "origin"
