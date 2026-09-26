"""Regression tests for configuration and destructive boundary safety."""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import git
import pytest
from github import GithubException

from git_branch_keeper.config import Config, load_config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.exceptions import GitHubAPIError
from git_branch_keeper.services.deletion_journal import DeletionJournal
from git_branch_keeper.services.git import GitOperations
from git_branch_keeper.services.git.github import GitHubService


@pytest.fixture
def local_remote(tmp_path):
    remote = git.Repo.init(tmp_path / "remote.git", bare=True)
    repo = git.Repo.init(tmp_path / "local", initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test").set_value("user", "email", "test@example.com")
    file = Path(repo.working_dir) / "file"
    file.write_text("base\n")
    repo.index.add(["file"])
    repo.index.commit("base")
    repo.create_head("feature").checkout()
    file.write_text("merged\n")
    repo.index.add(["file"])
    repo.index.commit("feature")
    repo.heads.main.checkout()
    repo.git.merge("feature")
    origin = repo.create_remote("origin", str(tmp_path / "remote.git"))
    origin.push(["main:main", "feature:feature"])
    ops = GitOperations(repo.working_dir, {})
    ops.deletion_journal = DeletionJournal(repo.working_dir, tmp_path / "journal.jsonl")
    yield repo, remote, ops
    repo.close()
    remote.close()


def test_matching_merged_remote_deleted_and_recoverable(local_remote):
    repo, remote, ops = local_remote
    sha = remote.heads.feature.commit.hexsha
    assert ops.delete_branch("feature", delete_remote=True)
    assert "feature" not in repo.heads and "feature" not in remote.heads
    assert ops.deletion_journal.deletions()[-1]["sha"] == sha


def test_advanced_remote_preserved_before_local_deletion(local_remote):
    repo, remote, ops = local_remote
    local_sha = repo.heads.feature.commit.hexsha
    repo.heads.feature.checkout()
    file = Path(repo.working_dir) / "file"
    file.write_text("new remote work\n")
    repo.index.add(["file"])
    repo.index.commit("new work")
    repo.remote().push("feature:feature")
    remote_sha = remote.heads.feature.commit.hexsha
    repo.heads.main.checkout()
    repo.git.branch("-f", "feature", local_sha)
    assert not ops.delete_branch("feature", delete_remote=True)
    assert repo.heads.feature.commit.hexsha == local_sha
    assert remote.heads.feature.commit.hexsha == remote_sha
    assert not ops.deletion_journal.deletions()


def test_remote_race_rejected_by_lease(local_remote, monkeypatch):
    repo, remote, ops = local_remote
    original = ops.get_live_remote_branch_sha

    def advance_after_check(name):
        result = original(name)
        remote.git.update_ref("refs/heads/feature", repo.git.rev_parse("main~1"))
        return result

    monkeypatch.setattr(ops, "get_live_remote_branch_sha", advance_after_check)
    # The lease protects the advanced remote. The local branch is already gone by
    # then, so the call reports that (partial) success rather than a failure.
    assert ops.delete_branch("feature", delete_remote=True)
    assert "feature" not in repo.heads
    assert "feature" in remote.heads
    entry = ops.deletion_journal.deletions()[-1]
    assert entry["remote_deleted"] is False


def test_unmerged_matching_remote_preserved(local_remote):
    repo, remote, ops = local_remote
    repo.heads.feature.checkout()
    repo.git.commit("--allow-empty", "-m", "unmerged")
    repo.remote().push("feature:feature")
    repo.heads.main.checkout()
    # Without merge proof the remote is never deleted. The local delete (which the
    # caller already validated, e.g. as stale) still happens rather than being
    # refused wholesale.
    assert ops.delete_branch("feature", delete_remote=True)
    assert "feature" not in repo.heads and "feature" in remote.heads
    assert ops.deletion_journal.deletions()[-1]["remote_deleted"] is False


def test_config_precedence_and_cli_protection(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    (home / ".git-branch-keeper.json").write_text('{"stale_days": 80}')
    assert load_config()["stale_days"] == 80
    (tmp_path / "git-branch-keeper.json").write_text(
        json.dumps(
            {
                "protected_branches": ["main", "release"],
                "ignore_patterns": ["keep/*"],
                "stale_days": 60,
                "delete_remote": True,
                "force": True,
            }
        )
    )
    cli = importlib.import_module("git_branch_keeper.cli.main")
    keeper = Mock()
    monkeypatch.setattr(cli, "BranchKeeper", keeper)
    monkeypatch.setattr(cli, "setup_logging", Mock())
    monkeypatch.setattr(
        sys,
        "argv",
        ["gbk", "--cli", "--stale-days", "45", "--protected", "develop", "--protected", "staging"],
    )
    assert cli.main() == 0
    cfg = keeper.call_args.args[1]
    assert cfg.stale_days == 45
    assert cfg.protected_branches == ["main", "release", "develop", "staging"]
    assert cfg.ignore_patterns == ["keep/*"]
    assert not cfg.delete_remote and not cfg.force
    explicit = tmp_path / "explicit.json"
    explicit.write_text('{"stale_days": 90}')
    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--config", str(explicit)])
    assert cli.main() == 0
    assert keeper.call_args.args[1].stale_days == 90


@pytest.mark.parametrize("contents", ["{", "[]", '{"protected_branches": "release"}'])
def test_bad_config_blocks_startup(monkeypatch, tmp_path, contents):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "git-branch-keeper.json").write_text(contents)
    cli = importlib.import_module("git_branch_keeper.cli.main")
    keeper = Mock()
    monkeypatch.setattr(cli, "BranchKeeper", keeper)
    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--force"])
    assert cli.main() == 1
    keeper.assert_not_called()


def test_config_environment_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv("GBK_TEST_AUTH", "example-value")
    path = tmp_path / "config.json"
    path.write_text('{"github_token": "${GBK_TEST_AUTH}"}')
    assert load_config(str(path))["github_token"] == "example-value"
    with pytest.raises(OSError):
        load_config(str(tmp_path / "missing.json"))


@pytest.mark.parametrize("remote_only", [False, True])
def test_pr_failure_blocks_branch_deletion(local_remote, monkeypatch, tmp_path, remote_only):
    repo, remote, _ = local_remote
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if remote_only:
        repo.delete_head("feature")
    keeper = BranchKeeper(repo.working_dir, Config(delete_remote=True), tui_mode=True)
    keeper.github_service.github_repo = "example/repo"
    keeper.github_service.gh_repo = Mock()
    keeper.github_service.gh_repo.get_pulls.side_effect = GithubException(
        403, {"message": "rate limit"}
    )
    success, error = keeper.delete_branch("feature", "merged", force_mode=True)
    assert not success and "deletion blocked" in error
    assert "feature" in remote.heads
    assert ("feature" in repo.heads) is not remote_only


def test_failed_github_setup_blocks_verification(monkeypatch):
    monkeypatch.setattr("git_branch_keeper.services.git.github.get_gh_cli_token", lambda: None)
    service = GitHubService(".", {})
    service.setup_failed = True
    with pytest.raises(GitHubAPIError, match="setup failed"):
        service.has_open_pr("feature")


def test_pr_failure_blocks_worktree_removal_and_candidates(local_remote, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from git_branch_keeper.models.branch import BranchStatus

    repo, _, _ = local_remote
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "worktree"
    repo.git.worktree("add", str(path), "feature")
    keeper = BranchKeeper(repo.working_dir, Config(), tui_mode=True)
    keeper.github_service.github_repo = "example/repo"
    keeper.github_service.gh_repo = Mock()
    keeper.github_service.gh_repo.get_pulls.side_effect = OSError("Network unavailable")
    data = keeper.github_service.get_pr_data_for_branch("feature")
    status, _, _, notes = keeper._determine_branch_status("feature", data)
    assert status == BranchStatus.ACTIVE
    assert "deletion blocked" in notes
    row = SimpleNamespace(name="feature", worktree_path=str(path), notes=None)
    _, _, removed, failures = keeper.perform_deletion([], [row], force_mode=True)
    assert not removed and failures
    assert path.exists()
