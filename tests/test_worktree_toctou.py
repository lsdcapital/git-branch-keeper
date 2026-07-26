"""Tests for the worktree check-to-deletion race.

Worktree membership is read once during analysis and cached. The TUI can then
sit on that cache for as long as the user takes to review, so a worktree created
in the meantime must still be seen before GBK deletes anything.
"""

from pathlib import Path

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.exceptions import GIT_ERRORS


@pytest.fixture
def merged_branch_repo(git_repo):
    """Repo on main with a merged feature branch ready for cleanup."""
    repo = git_repo
    repo_path = Path(repo.working_dir)

    repo.git.checkout("-b", "feature/racy")
    (repo_path / "racy.txt").write_text("work\n")
    repo.index.add(["racy.txt"])
    repo.index.commit("Add racy feature")
    repo.git.checkout("main")
    repo.git.merge("feature/racy", "--no-ff", "-m", "Merge feature/racy")

    return repo


def _keeper(repo, **overrides) -> BranchKeeper:
    config = Config(
        dry_run=False,
        interactive=False,
        main_branch="main",
        protected_branches=["main"],
        **overrides,
    )
    return BranchKeeper(repo.working_dir, config)


def test_worktree_created_after_analysis_blocks_deletion(merged_branch_repo, temp_dir):
    repo = merged_branch_repo
    keeper = _keeper(repo)

    # Analysis sees no worktree and marks the branch deletable.
    result = keeper.analyze_branches()
    assert "feature/racy" in [b.name for b in result.deletable_branches]

    # Race: another process checks the branch out while GBK waits for the user.
    racing_path = temp_dir / "racing"
    repo.git.worktree("add", str(racing_path), "feature/racy")

    try:
        success, error = keeper.delete_branch("feature/racy", "merged")

        assert success is False
        assert "checked out in a worktree" in error
        assert str(racing_path) in error
        # The branch must survive.
        assert "feature/racy" in [h.name for h in repo.heads]
    finally:
        repo.git.worktree("remove", str(racing_path), "--force")
        repo.git.worktree("prune", "--expire=now")


def test_worktree_removed_after_analysis_does_not_block_deletion(merged_branch_repo, temp_dir):
    """The race runs both ways - a stale cache must not veto a valid deletion."""
    repo = merged_branch_repo

    stale_path = temp_dir / "stale"
    repo.git.worktree("add", str(stale_path), "feature/racy")

    keeper = _keeper(repo)

    # Analysis sees the worktree and caches that.
    result = keeper.analyze_branches()
    branch_row = next(b for b in result.branches if b.name == "feature/racy" and not b.is_worktree)
    assert branch_row.in_worktree is True

    # The worktree goes away behind GBK's back.
    repo.git.worktree("remove", str(stale_path), "--force")
    repo.git.worktree("prune", "--expire=now")

    success, error = keeper.delete_branch("feature/racy", "merged")

    assert success is True, error
    assert "feature/racy" not in [h.name for h in repo.heads]


def test_perform_deletion_reports_the_race_as_a_failed_branch(merged_branch_repo, temp_dir):
    """A racing worktree fails one branch cleanly, it does not abort the run."""
    repo = merged_branch_repo
    repo_path = Path(repo.working_dir)

    # A second merged branch that should still be deleted.
    repo.git.checkout("-b", "feature/calm")
    (repo_path / "calm.txt").write_text("work\n")
    repo.index.add(["calm.txt"])
    repo.index.commit("Add calm feature")
    repo.git.checkout("main")
    repo.git.merge("feature/calm", "--no-ff", "-m", "Merge feature/calm")

    keeper = _keeper(repo)
    result = keeper.analyze_branches()
    to_delete = [b for b in result.deletable_branches if b.name.startswith("feature/")]
    assert {b.name for b in to_delete} == {"feature/racy", "feature/calm"}

    racing_path = temp_dir / "racing"
    repo.git.worktree("add", str(racing_path), "feature/racy")

    try:
        deleted, failed, _removed, _failed_wt = keeper.perform_deletion(
            branches_to_delete=to_delete,
            worktrees_to_remove=[],
        )

        assert deleted == ["feature/calm"]
        assert [name for name, _ in failed] == ["feature/racy"]
        assert "checked out in a worktree" in failed[0][1]
    finally:
        try:
            repo.git.worktree("remove", str(racing_path), "--force")
        except GIT_ERRORS:
            pass
        repo.git.worktree("prune", "--expire=now")
