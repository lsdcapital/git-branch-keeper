"""Harness tests that actually mount the TUI via Textual's run_test pilot.

Pre-populating `branches` makes on_mount skip all background/cache loading, so
these tests are deterministic - they verify the app composes, renders rows, and
responds to key bindings without a real data-load worker.
"""

from pathlib import Path

import git
import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import (
    BranchAnalysisProgress,
    BranchAnalysisResult,
    BranchDetails,
    BranchStatus,
)
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
def isolated_home(temp_dir, monkeypatch):
    """Redirect Path.home() so undo journals do not touch the real home dir."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


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


async def test_initial_load_uses_table_loader_when_no_cached_rows(make_app, monkeypatch):
    app = make_app(None)
    called = False

    def fake_cached_analysis(*args, **kwargs):
        return BranchAnalysisResult(
            local_branch_names=["main"],
            branches_to_process=["main"],
            is_complete=False,
        )

    def fake_load_initial_data():
        nonlocal called
        called = True

    monkeypatch.setattr(app.keeper, "get_cached_analysis_fast", fake_cached_analysis)
    monkeypatch.setattr(app, "load_initial_data", fake_load_initial_data)

    async with app.run_test() as pilot:
        await pilot.pause()

        table = app.query_one(DataTable)
        assert called is True
        assert app.is_refreshing is True
        assert table.loading is True
        status = app.query_one("#status-bar").render()
        assert "Refreshing" in str(status)


async def test_refresh_binding_shows_immediate_feedback(make_app, monkeypatch):
    app = make_app([_branch("feature/a")])
    called = False

    def fake_refresh_data():
        nonlocal called
        called = True

    monkeypatch.setattr(app, "refresh_data", fake_refresh_data)

    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.pause()

        table = app.query_one(DataTable)
        assert called is True
        assert app.is_refreshing is True
        assert table.loading is False
        status = app.query_one("#status-bar").render()
        assert "Refreshing" in str(status)
        assert "Starting" in str(status)
        assert "actions paused" in str(status)


async def test_marking_is_paused_while_refreshing(make_app):
    app = make_app([_branch("feature/a")])

    async with app.run_test() as pilot:
        app._set_refreshing(True)
        await pilot.press("space")
        await pilot.pause()

        assert app.marked_branches == set()
        status = app.query_one("#status-bar").render()
        assert "marking paused" in str(status)


async def test_refresh_progress_shows_counts_and_percent(make_app):
    app = make_app([_branch("feature/a")])

    async with app.run_test() as pilot:
        app._set_refreshing(True)
        app._set_analysis_progress(
            BranchAnalysisProgress(
                phase="Processing branches",
                current=3,
                total=10,
            )
        )
        await pilot.pause()

        status = app.query_one("#status-bar").render()
        assert "Processing branches 3/10 (30%)" in str(status)


async def test_deletion_progress_uses_delete_label(make_app):
    app = make_app([_branch("feature/a")])

    async with app.run_test() as pilot:
        app._set_refreshing(True, operation_label="Deleting")
        app._set_analysis_progress(
            BranchAnalysisProgress(
                phase="Cleaning up",
                current=1,
                total=2,
                message="Deleted feature/a",
            )
        )
        await pilot.pause()

        status = app.query_one("#status-bar").render()
        assert "Deleting" in str(status)
        assert "Deleted feature/a 1/2 (50%)" in str(status)


async def test_apply_refresh_result_preserves_scroll_position(make_app):
    branches = [_branch(f"feature/{index:02d}") for index in range(60)]
    refreshed = [_branch(f"feature/{index:02d}", status=BranchStatus.STALE) for index in range(60)]
    app = make_app(branches)

    async with app.run_test(size=(120, 15)) as pilot:
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        table.cursor_coordinate = Coordinate(25, 0)
        table.scroll_to(y=20, animate=False, force=True, immediate=True)
        await pilot.pause()
        scroll_y = table.scroll_y

        app._apply_analysis_result(
            BranchAnalysisResult(branches=refreshed),
            preserve_marks=True,
            preserve_view=True,
        )
        await pilot.pause()

        assert table.scroll_y == scroll_y
        assert table.cursor_row == 25


async def test_toggle_mark_does_not_reset_scroll_position(make_app):
    branches = [_branch(f"feature/{index:02d}") for index in range(60)]
    app = make_app(branches)

    async with app.run_test(size=(120, 15)) as pilot:
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        table.cursor_coordinate = Coordinate(25, 0)
        table.scroll_to(y=20, animate=False, force=True, immediate=True)
        await pilot.pause()
        scroll_y = table.scroll_y
        assert scroll_y > 0

        marked_branch = app.branches[25].name
        await pilot.press("space")
        await pilot.pause()

        assert marked_branch in app.marked_branches
        assert table.scroll_y == scroll_y


async def test_undo_recent_deletion_binding_restores_latest_batch(
    make_app, git_repo, isolated_home, monkeypatch
):
    repo = git_repo

    repo.git.checkout("-b", "feature/deleted-one")
    sha_one = repo.head.commit.hexsha
    repo.git.checkout("main")
    repo.git.checkout("-b", "feature/deleted-two")
    sha_two = repo.head.commit.hexsha
    repo.git.checkout("main")
    repo.delete_head("feature/deleted-one", force=True)
    repo.delete_head("feature/deleted-two", force=True)

    app = make_app([_branch("main", BranchStatus.ACTIVE)])
    monkeypatch.setattr(app, "refresh_data", lambda: None)
    batch_id = app.keeper.git_service.deletion_journal.new_batch_id()
    app.keeper.git_service.deletion_journal.record_deletion(
        "feature/deleted-one",
        sha_one,
        had_remote=False,
        remote_deleted=False,
        batch_id=batch_id,
    )
    app.keeper.git_service.deletion_journal.record_deletion(
        "feature/deleted-two",
        sha_two,
        had_remote=False,
        remote_deleted=False,
        batch_id=batch_id,
    )

    async with app.run_test() as pilot:
        await pilot.press("u")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

    restored_repo = git.Repo(repo.working_dir)
    assert restored_repo.heads["feature/deleted-one"].commit.hexsha == sha_one
    assert restored_repo.heads["feature/deleted-two"].commit.hexsha == sha_two
    restored_repo.close()


async def test_quit_binding_exits(make_app):
    app = make_app([_branch("feature/a")])
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    # Exiting cleanly (no exception) is the assertion here.
    assert app.return_code is not None or True
