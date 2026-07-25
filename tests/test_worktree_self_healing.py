"""Tests for self-healing stale GBK temporary worktrees."""

import os
import shutil
import tempfile

from git_branch_keeper.config import Config
from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.services.git import GitOperations
from git_branch_keeper.services.git.worktrees import WorktreeService


def _create_invalid_gbk_temp_worktree(repo, branch_name: str) -> str:
    """Create Git worktree metadata pointing at an invalid GBK temp path."""
    path = tempfile.mkdtemp(prefix=f"gbk-{branch_name.replace('/', '-')}-")
    repo.git.worktree("add", path, branch_name)

    # Simulate interrupted cleanup / OS temp cleanup: path still exists but is no
    # longer a Git worktree, matching the real-world `fatal: not a git repository` case.
    shutil.rmtree(path)
    os.makedirs(path)
    return path


def test_valid_gbk_temp_worktree_is_hidden_from_user_worktrees(git_repo):
    repo = git_repo
    repo.git.checkout("-b", "feature/internal-temp")
    repo.git.checkout("main")
    temp_path = tempfile.mkdtemp(prefix="gbk-feature-internal-temp-")
    repo.git.worktree("add", temp_path, "feature/internal-temp")

    try:
        service = WorktreeService(repo.working_dir)
        infos = service.get_worktree_info()

        assert all(info.path != temp_path for info in infos)
        assert temp_path in repo.git.worktree("list", "--porcelain")
    finally:
        try:
            repo.git.worktree("remove", temp_path, "--force")
        except GIT_ERRORS:
            pass
        shutil.rmtree(temp_path, ignore_errors=True)
        repo.git.worktree("prune", "--expire=now")


def test_stale_gbk_temp_worktree_metadata_is_pruned(git_repo):
    repo = git_repo
    repo.git.checkout("-b", "feature/stale-temp")
    repo.git.checkout("main")
    stale_path = _create_invalid_gbk_temp_worktree(repo, "feature/stale-temp")

    try:
        service = WorktreeService(repo.working_dir)
        infos = service.get_worktree_info()

        assert all(info.path != stale_path for info in infos)
        assert stale_path not in repo.git.worktree("list", "--porcelain")
    finally:
        shutil.rmtree(stale_path, ignore_errors=True)
        repo.git.worktree("prune", "--expire=now")


def test_branch_status_self_heals_stale_gbk_temp_worktree(git_repo):
    repo = git_repo
    repo.git.checkout("-b", "feature/status-self-heal")
    repo.git.checkout("main")
    stale_path = _create_invalid_gbk_temp_worktree(repo, "feature/status-self-heal")

    try:
        git_service = GitOperations(
            repo.working_dir,
            Config(dry_run=True, main_branch="main", protected_branches=["main"]),
        )

        status = git_service.get_branch_status_details("feature/status-self-heal")

        assert status == {"modified": False, "untracked": False, "staged": False}
        assert stale_path not in repo.git.worktree("list", "--porcelain")
    finally:
        shutil.rmtree(stale_path, ignore_errors=True)
        repo.git.worktree("prune", "--expire=now")
