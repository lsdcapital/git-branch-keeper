"""Real Git regression tests for removal of externally damaged worktrees."""

import shutil
from pathlib import Path

import pytest

from git_branch_keeper.services.git.worktrees import WorktreeService


@pytest.fixture
def linked_worktree(git_repo):
    path = Path(git_repo.working_dir).parent / "linked"
    git_repo.git.worktree("add", "-b", "feature/stale", str(path))
    return git_repo, path, WorktreeService(git_repo.working_dir)


def test_missing_directory_is_pruned_and_cache_refreshed(linked_worktree):
    repo, path, service = linked_worktree
    service.get_worktree_info()
    shutil.rmtree(path)

    assert service.remove_worktree(str(path)) == (True, None)
    assert str(path) not in repo.git.worktree("list", "--porcelain")
    assert all(Path(wt.path) != path for wt in service.get_worktree_info())
    assert "feature/stale" in repo.heads


def test_broken_worktree_offers_cleanup_without_changing_files_or_registration(linked_worktree):
    repo, path, service = linked_worktree
    (path / ".git").unlink()
    (path / "valuable.txt").write_text("keep me")

    success, error = service.remove_worktree(str(path))

    assert not success
    assert "force" in error
    assert "files will be kept" in error
    assert (path / "valuable.txt").read_text() == "keep me"
    assert str(path) in repo.git.worktree("list", "--porcelain")


def test_force_cleanup_preserves_tracked_and_untracked_files(linked_worktree):
    repo, path, service = linked_worktree
    (path / ".git").unlink()
    (path / "README.md").write_text("uncommitted edits")
    (path / "valuable.txt").write_text("keep me")

    assert service.remove_worktree(str(path), force=True) == (True, None)
    assert (path / "README.md").read_text() == "uncommitted edits"
    assert (path / "valuable.txt").read_text() == "keep me"
    assert str(path) not in repo.git.worktree("list", "--porcelain")
    assert "feature/stale" in repo.heads


@pytest.mark.parametrize("directory_remains", [False, True])
def test_locked_stale_registration_is_not_reported_as_removed(linked_worktree, directory_remains):
    repo, path, service = linked_worktree
    repo.git.worktree("lock", str(path))
    if directory_remains:
        (path / ".git").unlink()
    else:
        shutil.rmtree(path)

    success, error = service.remove_worktree(str(path), force=True)

    assert not success
    assert "locked" in error
    assert str(path) in repo.git.worktree("list", "--porcelain")
    assert path.exists() == directory_remains


def test_healthy_dirty_worktree_still_requires_force(linked_worktree):
    _repo, path, service = linked_worktree
    (path / "valuable.txt").write_text("keep me")

    success, error = service.remove_worktree(str(path))

    assert not success
    assert "git worktree remove failed" in error
    assert (path / "valuable.txt").read_text() == "keep me"


def test_unregistered_directory_is_not_reported_as_removed(git_repo):
    path = Path(git_repo.working_dir).parent / "unregistered"
    path.mkdir()
    service = WorktreeService(git_repo.working_dir)

    success, _ = service.remove_worktree(str(path), force=True)

    assert not success
    assert path.is_dir()


def test_batch_deletion_does_not_prune_after_declining_broken_worktree(linked_worktree):
    from git_branch_keeper.config import Config
    from git_branch_keeper.core import BranchKeeper

    repo, path, _service = linked_worktree
    keeper = BranchKeeper(
        repo.working_dir,
        Config(dry_run=False, interactive=False, main_branch="main", protected_branches=["main"]),
    )
    result = keeper.analyze_branches()
    row = next(b for b in result.branches if b.is_worktree and Path(b.worktree_path) == path)
    (path / ".git").unlink()

    _deleted, _failed, removed, failed_worktrees = keeper.perform_deletion([], [row])

    assert removed == []
    assert len(failed_worktrees) == 1
    assert "Broken worktree" in failed_worktrees[0][1]
    assert str(path) in repo.git.worktree("list", "--porcelain")
    assert (path / "README.md").exists()


def test_worktree_removed_and_pruned_outside_gbk_counts_as_removed(linked_worktree):
    """The TUI can hold a worktree row while the worktree is removed elsewhere."""
    repo, path, service = linked_worktree
    service.get_worktree_info()
    repo.git.worktree("remove", str(path))

    assert service.remove_worktree(str(path)) == (True, None)
    assert all(Path(wt.path) != path for wt in service.get_worktree_info())


def test_unregistered_directory_is_left_untouched(linked_worktree):
    repo, path, service = linked_worktree
    repo.git.worktree("remove", str(path))
    path.mkdir()
    (path / "valuable.txt").write_text("keep me")

    success, error = service.remove_worktree(str(path), force=True)

    assert not success
    assert "No longer a registered worktree" in error
    assert (path / "valuable.txt").read_text() == "keep me"
