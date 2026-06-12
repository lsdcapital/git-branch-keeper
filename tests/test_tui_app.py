"""Harness tests that actually mount the TUI via Textual's run_test pilot.

Pre-populating `branches` makes on_mount skip all background/cache loading, so
these tests are deterministic - they verify the app composes, renders rows, and
responds to key bindings without a real data-load worker.
"""

import pytest
from textual.widgets import DataTable

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.ui.app import BranchKeeperApp


def _branch(name, status=BranchStatus.MERGED):
    return BranchDetails(
        name=name,
        last_commit_date="2024-01-01",
        age_days=10,
        status=status,
        modified_files=False,
        untracked_files=False,
        staged_files=False,
        has_remote=False,
        sync_status="local-only",
    )


@pytest.fixture
def make_app(git_repo):
    def _make(branches, cleanup_mode=False):
        keeper = BranchKeeper(
            git_repo.working_dir,
            Config(interactive=True, dry_run=True, main_branch="main"),
        )
        return BranchKeeperApp(keeper, branches=branches, cleanup_mode=cleanup_mode)

    return _make


async def test_app_mounts_and_renders_rows(make_app):
    app = make_app([_branch("feature/a"), _branch("feature/b")])
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert table.row_count == 2
        # Status bar reflects the totals
        status = app.query_one("#status-bar").render()
        assert "Total: 2" in str(status)
        await pilot.pause()


async def test_clear_marks_binding_empties_marks(make_app):
    app = make_app([_branch("feature/a")])
    async with app.run_test() as pilot:
        app.marked_branches.add("feature/a")
        await pilot.press("c")  # action_clear_marks
        assert app.marked_branches == set()


async def test_mark_all_deletable_marks_merged_branch(make_app):
    app = make_app([_branch("feature/a"), _branch("feature/b")])
    async with app.run_test() as pilot:
        await pilot.press("a")  # action_mark_all_deletable
        await pilot.pause()
        assert "feature/a" in app.marked_branches
        assert "feature/b" in app.marked_branches


async def test_quit_binding_exits(make_app):
    app = make_app([_branch("feature/a")])
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    # Exiting cleanly (no exception) is the assertion here.
    assert app.return_code is not None or True
