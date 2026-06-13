"""Tests for CLI/TUI mode selection and cleanup semantics."""

import importlib
import sys

cli_main = importlib.import_module("git_branch_keeper.cli.main")
ui_module = importlib.import_module("git_branch_keeper.ui")


class FakeBranchKeeper:
    """Minimal BranchKeeper test double for CLI wiring tests."""

    instances = []

    def __init__(self, repo_path, config, tui_mode=False):
        self.repo_path = repo_path
        self.config = config
        self.tui_mode = tui_mode
        self.process_calls = []
        FakeBranchKeeper.instances.append(self)

    def process_branches(self, cleanup_enabled=False):
        self.process_calls.append(cleanup_enabled)


class FakeBranchKeeperApp:
    """Minimal TUI test double for cleanup mode wiring tests."""

    instances = []

    def __init__(self, keeper, cleanup_mode=False):
        self.keeper = keeper
        self.cleanup_mode = cleanup_mode
        self.ran = False
        FakeBranchKeeperApp.instances.append(self)

    def run(self):
        self.ran = True


def _run_cli(monkeypatch, argv):
    FakeBranchKeeper.instances.clear()
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli_main, "BranchKeeper", FakeBranchKeeper)

    assert cli_main.main() == 0
    assert len(FakeBranchKeeper.instances) == 1
    return FakeBranchKeeper.instances[0]


def _run_tui(monkeypatch, argv):
    FakeBranchKeeper.instances.clear()
    FakeBranchKeeperApp.instances.clear()
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli_main, "BranchKeeper", FakeBranchKeeper)
    monkeypatch.setattr(ui_module, "BranchKeeperApp", FakeBranchKeeperApp)

    assert cli_main.main() == 0
    assert len(FakeBranchKeeper.instances) == 1
    assert len(FakeBranchKeeperApp.instances) == 1
    return FakeBranchKeeper.instances[0], FakeBranchKeeperApp.instances[0]


def test_no_interactive_cli_is_report_only_by_default(monkeypatch):
    keeper = _run_cli(monkeypatch, ["git-branch-keeper", "--no-interactive", "--filter", "merged"])

    assert keeper.tui_mode is False
    assert keeper.process_calls == [False]
    assert keeper.config.dry_run is False
    assert keeper.config.force is False


def test_cli_alias_is_report_only_by_default(monkeypatch):
    keeper = _run_cli(monkeypatch, ["git-branch-keeper", "--cli", "--filter", "merged"])

    assert keeper.tui_mode is False
    assert keeper.process_calls == [False]


def test_cli_delete_enables_cleanup_with_confirmation(monkeypatch):
    keeper = _run_cli(monkeypatch, ["git-branch-keeper", "--cli", "--filter", "merged", "--delete"])

    assert keeper.process_calls == [True]
    assert keeper.config.dry_run is False
    assert keeper.config.force is False


def test_deprecated_cleanup_alias_enables_cleanup(monkeypatch):
    keeper = _run_cli(
        monkeypatch, ["git-branch-keeper", "--cli", "--filter", "merged", "--cleanup"]
    )

    assert keeper.process_calls == [True]


def test_cli_dry_run_enables_cleanup_preview_only(monkeypatch):
    keeper = _run_cli(
        monkeypatch, ["git-branch-keeper", "--cli", "--filter", "merged", "--dry-run"]
    )

    assert keeper.process_calls == [True]
    assert keeper.config.dry_run is True
    assert keeper.config.force is False


def test_legacy_force_still_implies_cleanup(monkeypatch):
    keeper = _run_cli(monkeypatch, ["git-branch-keeper", "--cli", "--filter", "merged", "--force"])

    assert keeper.process_calls == [True]
    assert keeper.config.dry_run is False
    assert keeper.config.force is True


def test_interactive_tui_auto_marks_recommendations_by_default(monkeypatch):
    keeper, app = _run_tui(monkeypatch, ["git-branch-keeper", "--interactive"])

    assert keeper.tui_mode is True
    assert keeper.process_calls == []
    assert app.cleanup_mode is True
    assert app.ran is True


def test_interactive_dry_run_disables_tui_auto_marking(monkeypatch):
    keeper, app = _run_tui(monkeypatch, ["git-branch-keeper", "--interactive", "--dry-run"])

    assert keeper.tui_mode is True
    assert keeper.process_calls == []
    assert app.cleanup_mode is False
    assert app.ran is True
