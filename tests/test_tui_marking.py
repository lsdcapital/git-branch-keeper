"""Unit tests for the TUI's branch-marking logic (safety-critical).

These exercise the pure marking methods directly (no running event loop needed),
covering protected-branch rejection, the uncommitted-changes gate and force
override, worktree hierarchy marking, and unmarking. The TUI previously had no
test coverage at all.
"""

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.ui.app import BranchKeeperApp


def _branch(
    name,
    *,
    status=BranchStatus.MERGED,
    modified=False,
    untracked=False,
    staged=False,
    is_worktree=False,
    worktree_path=None,
):
    return BranchDetails(
        name=name,
        last_commit_date="2024-01-01",
        age_days=10,
        status=status,
        modified_files=modified,
        untracked_files=untracked,
        staged_files=staged,
        has_remote=False,
        sync_status="local-only",
        is_worktree=is_worktree,
        worktree_path=worktree_path,
    )


@pytest.fixture
def app(git_repo):
    """A BranchKeeperApp over a real (local) repo, not run - just constructed."""
    keeper = BranchKeeper(
        git_repo.working_dir,
        Config(interactive=True, dry_run=True, main_branch="main"),
    )
    return BranchKeeperApp(keeper, branches=[], cleanup_mode=False)


class TestMarkWithHierarchy:
    def test_mark_normal_branch_succeeds(self, app):
        app.branches = [_branch("feature/clean")]
        ok, err = app._mark_with_hierarchy("feature/clean", app.marked_branches)
        assert ok is True and err is None
        assert "feature/clean" in app.marked_branches

    def test_protected_branch_is_rejected(self, app):
        app.branches = [_branch("main", status=BranchStatus.ACTIVE)]
        ok, err = app._mark_with_hierarchy("main", app.marked_branches)
        assert ok is False
        assert "protected" in err.lower()
        assert app.marked_branches == set()

    def test_uncommitted_changes_rejected_without_force(self, app):
        app.branches = [_branch("feature/dirty", modified=True)]
        ok, err = app._mark_with_hierarchy("feature/dirty", app.marked_branches)
        assert ok is False
        assert "uncommitted" in err.lower()
        assert "force-mark" in err.lower()
        assert app.marked_branches == set()

    def test_force_mark_overrides_uncommitted(self, app):
        app.branches = [_branch("feature/dirty", staged=True)]
        ok, err = app._mark_with_hierarchy(
            "feature/dirty", app.force_marked_branches, is_force=True
        )
        assert ok is True and err is None
        assert "feature/dirty" in app.force_marked_branches

    def test_unknown_branch_returns_error(self, app):
        app.branches = [_branch("feature/x")]
        ok, err = app._mark_with_hierarchy("does/not/exist", app.marked_branches)
        assert ok is False
        assert "not found" in err.lower()

    def test_branch_and_worktree_marked_together(self, app):
        # A branch entry and a worktree entry sharing the same name are marked as a unit.
        app.branches = [
            _branch("feature/wt"),
            _branch("feature/wt", is_worktree=True, worktree_path="/tmp/wt"),
        ]
        ok, err = app._mark_with_hierarchy("feature/wt", app.marked_branches)
        assert ok is True and err is None
        # Both entries share the name; the single name is recorded once.
        assert "feature/wt" in app.marked_branches

    def test_worktree_uncommitted_blocks_the_pair(self, app):
        # If the worktree sibling has uncommitted changes, the whole mark is blocked.
        app.branches = [
            _branch("feature/wt"),
            _branch("feature/wt", is_worktree=True, worktree_path="/tmp/wt", modified=True),
        ]
        ok, err = app._mark_with_hierarchy("feature/wt", app.marked_branches)
        assert ok is False
        assert "worktree" in err.lower()
        assert app.marked_branches == set()


class TestUnmark:
    def test_unmark_clears_both_sets(self, app):
        app.marked_branches.add("feature/a")
        app.force_marked_branches.add("feature/a")
        app._unmark_with_hierarchy("feature/a")
        assert "feature/a" not in app.marked_branches
        assert "feature/a" not in app.force_marked_branches
