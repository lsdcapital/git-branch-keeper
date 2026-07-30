"""Tests for TUI mouse clicks vs keyboard row selection."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.ui.app import BranchKeeperApp


def _branch(name: str, status: BranchStatus = BranchStatus.MERGED) -> BranchDetails:
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


def _pr_branch(name: str, number: int) -> BranchDetails:
    branch = _branch(name)
    branch.pr_status = str(number)
    branch.pr_details = {
        "number": number,
        "url": f"https://github.com/test/test-repo/pull/{number}",
    }
    return branch


@pytest.fixture
def isolated_home(temp_dir, monkeypatch):
    """Redirect branch-keeper cache/journal files under the test temp directory."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


@pytest.fixture
def make_app(git_repo, isolated_home):
    def _make(branches):
        keeper = BranchKeeper(
            git_repo.working_dir,
            Config(interactive=True, dry_run=True, main_branch="main"),
            tui_mode=True,
        )
        return BranchKeeperApp(keeper, branches=branches)

    return _make


async def test_mouse_click_on_row_does_not_trigger_delete_action(make_app):
    app = make_app([_branch("feature/a"), _branch("feature/b")])
    delete_mock = Mock()
    app.action_delete_marked = delete_mock

    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        # Click the second data row twice. In Textual's DataTable a second click on the
        # highlighted row emits RowSelected, which should not open the delete flow here.
        clicked = await pilot.click(table, offset=(5, 2), times=2)
        await pilot.pause()

        assert clicked is True
        assert table.cursor_row == 1
        delete_mock.assert_not_called()


async def test_enter_key_still_triggers_delete_action(make_app):
    app = make_app([_branch("feature/a")])
    delete_mock = Mock()
    app.action_delete_marked = delete_mock

    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        delete_mock.assert_called_once_with()


async def test_enter_after_mouse_click_still_triggers_delete_action(make_app):
    app = make_app([_branch("feature/a"), _branch("feature/b")])
    delete_mock = Mock()
    app.action_delete_marked = delete_mock

    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.click(table, offset=(5, 2))
        await pilot.pause()
        delete_mock.assert_not_called()

        await pilot.press("enter")
        await pilot.pause()

        delete_mock.assert_called_once_with()


async def test_clicking_pr_number_opens_default_browser(make_app):
    app = make_app([_pr_branch("feature/pr-link", 42)])
    open_url_mock = Mock()
    app.open_url = open_url_mock

    async with app.run_test(size=(160, 30)) as pilot:
        table = app.query_one(DataTable)
        await pilot.pause()

        pr_column = 8  # Mark + the seven columns before PRs.
        pr_region = table._get_cell_region(Coordinate(0, pr_column))
        clicked = await pilot.click(
            table,
            offset=(pr_region.x + table.cell_padding, pr_region.y),
        )
        await pilot.pause()

        assert clicked is True
        open_url_mock.assert_called_once_with("https://github.com/test/test-repo/pull/42")
