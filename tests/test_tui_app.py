"""Harness tests that actually mount the TUI via Textual's run_test pilot.

Pre-populating `branches` makes on_mount skip all background/cache loading, so
these tests are deterministic - they verify the app composes, renders rows, and
responds to key bindings without a real data-load worker.
"""

from pathlib import Path

import git
import pytest
from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import (
    BranchAnalysisProgress,
    BranchAnalysisResult,
    BranchDetails,
    BranchStatus,
)
from git_branch_keeper.ui.app import BranchKeeperApp
from git_branch_keeper.ui.screens import ConfirmScreen


def _branch(
    name,
    status=BranchStatus.MERGED,
    *,
    is_worktree=False,
    in_worktree=False,
    wt=None,
    has_remote=False,
    has_local=True,
):
    return BranchDetails(
        name=name,
        last_commit_date="2024-01-01",
        age_days=10,
        status=status,
        modified_files=False,
        untracked_files=False,
        staged_files=False,
        has_remote=has_remote,
        has_local=has_local,
        sync_status="local-only",
        is_worktree=is_worktree,
        in_worktree=in_worktree,
        worktree_path=wt,
    )


def _status_text(app):
    """Return the three logical status rows as plain text."""
    rows = []
    for row_id in ("status-scope", "status-summary", "status-dynamic"):
        content = app.query_one(f"#{row_id}").content
        rows.append(content.plain if isinstance(content, Text) else str(content))
    return "\n".join(rows)


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
        status = _status_text(app)
        assert "Delete scope: LOCAL + MERGED REMOTE-ONLY [d]" in status
        assert "Total: 2" in status
        await pilot.pause()


async def test_status_feedback_stays_on_fixed_third_line(make_app):
    app = make_app([_branch("feature/a")])

    async with app.run_test(size=(55, 24)) as pilot:
        status = app.query_one("#status-bar")
        await pilot.pause()
        initial_height = status.region.height

        app._set_status_message("Branch data refreshed", severity="success")
        await pilot.pause()

        content = app.query_one("#status-dynamic").content
        assert isinstance(content, Text)
        lines = _status_text(app).split("\n")
        assert len(lines) == 3
        assert lines[0] == "Delete scope: LOCAL + MERGED REMOTE-ONLY [d]"
        assert lines[1].startswith("Total: 1 | Protected:")
        assert lines[2] == "✓ Branch data refreshed"
        assert status.region.height == initial_height == 5


async def test_delete_scope_binding_toggles_remote_deletion(make_app):
    app = make_app([_branch("feature/a", has_remote=True)])

    async with app.run_test() as pilot:
        await pilot.press("d")
        await pilot.pause()

        assert app.keeper.delete_remote is True
        status = _status_text(app)
        assert "Delete scope: LOCAL + REMOTE [d]" in status
        assert "undo restores local branches only" in status

        await pilot.press("d")
        await pilot.pause()

        assert app.keeper.delete_remote is False
        status = _status_text(app)
        assert "Delete scope: LOCAL + MERGED REMOTE-ONLY [d]" in status


async def test_delete_confirmation_explains_local_only_scope(make_app):
    app = make_app([_branch("feature/a", has_remote=True)])

    async with app.run_test() as pilot:
        app.marked_branches.add("feature/a")
        app.action_delete_marked()
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        message = app.screen.message
        assert "Deletion scope: LOCAL ONLY" in message
        assert "Matching remotes for local branches on origin will be kept" in message
        assert "remain visible as remote-only rows" in message
        assert "Cancel and press d to delete local and remote branches together" in message


async def test_delete_confirmation_warns_remote_undo_is_manual(make_app):
    app = make_app([_branch("feature/a", has_remote=True)])
    app.keeper.delete_remote = True

    async with app.run_test() as pilot:
        app.marked_branches.add("feature/a")
        app.action_delete_marked()
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        message = app.screen.message
        assert "Deletion scope: LOCAL + REMOTE" in message
        assert "Matching branches on origin will also be deleted" in message
        assert "Undo restores local branches only" in message
        assert "deleted remote branches must be pushed back manually" in message


async def test_delete_scope_cannot_change_behind_confirmation(make_app):
    """The confirmed scope and the scope used for deletion must not diverge."""
    app = make_app([_branch("feature/a", has_remote=True)])

    async with app.run_test() as pilot:
        app.marked_branches.add("feature/a")
        app.action_delete_marked()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)

        await pilot.press("d")
        await pilot.pause()

        assert app.keeper.delete_remote is False
        assert isinstance(app.screen, ConfirmScreen)
        assert "Deletion scope: LOCAL ONLY" in app.screen.message


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


async def test_cleanup_mode_auto_marks_merged_remote_only_branch(make_app):
    app = make_app(
        [_branch("feature/remote-only", has_remote=True, has_local=False)],
        cleanup_mode=True,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.marked_branches == {"feature/remote-only"}


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
        status = _status_text(app)
        assert "Refreshing" in status


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
        status = _status_text(app)
        assert "Refreshing" in status
        assert "Starting" in status
        assert "actions paused" in status


async def test_marking_is_paused_while_refreshing(make_app):
    app = make_app([_branch("feature/a")])

    async with app.run_test(size=(55, 24)) as pilot:
        app._set_refreshing(True)
        await pilot.press("space")
        await pilot.pause()

        assert app.marked_branches == set()
        status = _status_text(app)
        assert "marking paused" in status
        dynamic_status = app.query_one("#status-dynamic")
        content = dynamic_status.content
        assert isinstance(content, Text)
        assert content.plain.startswith("⚠ Refreshing in progress")
        assert "⟳" not in content.plain
        assert "marking paused" in dynamic_status.render_line(0).text


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

        status = _status_text(app)
        assert "Processing branches 3/10 (30%)" in status


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

        status = _status_text(app)
        assert "Deleting" in status
        assert "Deleted feature/a 1/2 (50%)" in status


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


async def test_startup_refresh_marks_branch_that_became_deletable(make_app):
    """A branch merged since the last run must still get its auto-mark.

    Startup paints cached rows first, then replaces them with a fresh analysis
    while preserving marks. Regression guard: the branch was `active` in the
    cache, so the first pass never offered it, and preserving marks alone would
    leave it rendered as `merged` but unmarked until the next launch.
    """
    cached = [_branch("feature/x", status=BranchStatus.ACTIVE)]
    app = make_app(cached, cleanup_mode=True)

    async with app.run_test() as pilot:
        app._apply_analysis_result(
            BranchAnalysisResult(branches=cached, deletable_branches=[], is_complete=False)
        )
        assert app.marked_branches == set()

        refreshed = [_branch("feature/x", status=BranchStatus.MERGED)]
        app._apply_analysis_result(
            BranchAnalysisResult(branches=refreshed, deletable_branches=list(refreshed)),
            preserve_marks=True,
            preserve_view=True,
            mark_newly_deletable=True,
        )
        await pilot.pause()

        assert app.marked_branches == {"feature/x"}


async def test_startup_refresh_keeps_user_unmark_of_already_offered_branch(make_app):
    """Re-marking must be limited to branches the user was never offered."""
    merged = [_branch("feature/x", status=BranchStatus.MERGED)]
    app = make_app(merged, cleanup_mode=True)

    async with app.run_test() as pilot:
        app._apply_analysis_result(
            BranchAnalysisResult(branches=merged, deletable_branches=list(merged))
        )
        assert app.marked_branches == {"feature/x"}

        app._unmark_with_hierarchy("feature/x")

        refreshed = [_branch("feature/x", status=BranchStatus.MERGED)]
        app._apply_analysis_result(
            BranchAnalysisResult(branches=refreshed, deletable_branches=list(refreshed)),
            preserve_marks=True,
            preserve_view=True,
            mark_newly_deletable=True,
        )
        await pilot.pause()

        assert app.marked_branches == set()


async def test_manual_refresh_never_re_marks(make_app):
    """`r` preserves marks verbatim - an unmark there is a deliberate choice."""
    cached = [_branch("feature/x", status=BranchStatus.ACTIVE)]
    app = make_app(cached, cleanup_mode=True)

    async with app.run_test() as pilot:
        app._apply_analysis_result(BranchAnalysisResult(branches=cached, deletable_branches=[]))

        refreshed = [_branch("feature/x", status=BranchStatus.MERGED)]
        app._apply_analysis_result(
            BranchAnalysisResult(branches=refreshed, deletable_branches=list(refreshed)),
            preserve_marks=True,
            preserve_view=True,
        )
        await pilot.pause()

        assert app.marked_branches == set()


async def test_status_bar_deletable_excludes_the_checked_out_branch(make_app, git_repo):
    """ "Deletable" must mean "what pressing `a` would mark".

    Counting rows with `is_deletable` instead of asking the planner advertised
    the current branch as a candidate. That is not hypothetical: running GBK from
    a linked worktree makes the checked-out branch a merged feature branch, and
    the status bar then read `Deletable: 1 | Marked: 0` forever.
    """
    git_repo.git.checkout("-b", "feature/current")
    app = make_app([_branch("main", status=BranchStatus.ACTIVE), _branch("feature/current")])

    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()

        status = _status_text(app)
        assert "Deletable: 0" in status
        assert "Marked: 0" in status
        assert app.marked_branches == set()


async def test_status_bar_counts_a_branch_and_its_worktree_as_one_candidate(make_app):
    """Rows are keyed by name+path, marks by name - the counts follow the marks."""
    rows = [
        _branch("main", status=BranchStatus.ACTIVE),
        _branch("feature/wt", in_worktree=True, wt="/tmp/wt"),
        _branch("feature/wt", is_worktree=True, wt="/tmp/wt"),
    ]
    app = make_app(rows)

    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()

        status = _status_text(app)
        assert "Total: 3" in status  # rows
        assert "Deletable: 1" in status  # names
        assert "Marked: 1" in status
        assert app.marked_branches == {"feature/wt"}


async def test_status_bar_recommends_merged_remote_only_candidates(make_app):
    rows = [
        _branch("main", status=BranchStatus.ACTIVE),
        _branch("feature/gone", has_remote=True, has_local=False),
        _branch("feature/also-gone", has_remote=True, has_local=False),
    ]
    app = make_app(rows)

    async with app.run_test() as pilot:
        await pilot.pause()

        status = _status_text(app)
        assert "Deletable: 2" in status
        assert "Blocked:" not in status


async def test_delete_confirmation_calls_out_remote_only_deletion(make_app):
    app = make_app(
        [_branch("feature/gone", has_remote=True, has_local=False)]
    )

    async with app.run_test() as pilot:
        app.marked_branches.add("feature/gone")
        app.action_delete_marked()
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        message = app.screen.message
        assert "1 merged remote-only branch on origin will be deleted" in message
        assert "delete-scope toggle does not apply" in message
        assert "feature/gone (merged, remote only)" in message


async def test_status_bar_omits_blocked_when_there_is_nothing_to_explain(make_app):
    """The figure earns its width only when it resolves a contradiction."""
    app = make_app([_branch("main", status=BranchStatus.ACTIVE), _branch("feature/a")])

    async with app.run_test() as pilot:
        await pilot.pause()

        status = _status_text(app)
        assert "Deletable: 1" in status
        assert "Blocked:" not in status


async def test_blocked_and_deletable_do_not_double_count(make_app):
    """A branch the planner unblocks by removing its worktree is not also blocked."""
    rows = [
        _branch("main", status=BranchStatus.ACTIVE),
        _branch("feature/wt", in_worktree=True, wt="/tmp/wt"),
        _branch("feature/wt", is_worktree=True, wt="/tmp/wt"),
    ]
    app = make_app(rows)

    async with app.run_test() as pilot:
        await pilot.pause()

        status = _status_text(app)
        assert "Deletable: 1" in status
        assert "Blocked:" not in status


async def test_quit_binding_exits(make_app):
    app = make_app([_branch("feature/a")])
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    # Exiting cleanly (no exception raised on context exit) is the assertion here.


async def test_confirm_screen_focuses_the_safe_option(make_app):
    """Focus starts on Cancel, never on the destructive button."""
    app = make_app([_branch("feature/a")])
    async with app.run_test() as pilot:
        app.push_screen(ConfirmScreen("Delete 1 branch?", dialog_title="Confirm deletion"))
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        assert app.screen.focused is not None
        assert app.screen.focused.id == "no"


@pytest.mark.parametrize(
    ("key", "expected"),
    [("escape", False), ("enter", False), ("n", False), ("y", True)],
)
async def test_confirm_screen_key_semantics(make_app, key, expected):
    """Enter activates the focused (Cancel) button; "y" is the only confirm key."""
    app = make_app([_branch("feature/a")])
    results: list[bool] = []
    async with app.run_test() as pilot:
        app.push_screen(ConfirmScreen("Delete 1 branch?"), results.append)
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()

    assert results == [expected]


async def test_confirm_screen_buttons_share_one_row_with_cancel_first(make_app):
    """Guards against the button container reverting to a vertical layout."""
    app = make_app([_branch("feature/a")])
    async with app.run_test() as pilot:
        app.push_screen(ConfirmScreen("Delete 1 branch?", confirm_label="Delete"))
        await pilot.pause()

        cancel = app.screen.query_one("#no", Button)
        delete = app.screen.query_one("#yes", Button)
        assert cancel.region.y == delete.region.y  # side by side, not stacked
        assert cancel.region.x < delete.region.x  # Cancel left of Delete
