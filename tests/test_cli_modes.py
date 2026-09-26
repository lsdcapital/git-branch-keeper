"""Tests for CLI/TUI mode selection and cleanup semantics."""

import importlib
import sys
from typing import ClassVar

cli_main = importlib.import_module("git_branch_keeper.cli.main")
ui_module = importlib.import_module("git_branch_keeper.ui")


class FakeBranchKeeper:
    """Minimal BranchKeeper test double for CLI wiring tests."""

    instances: ClassVar[list["FakeBranchKeeper"]] = []

    def __init__(self, repo_path, config, tui_mode=False):
        self.repo_path = repo_path
        self.config = config
        self.tui_mode = tui_mode
        self.process_calls: list[bool] = []
        FakeBranchKeeper.instances.append(self)

    def process_branches(self, cleanup_enabled=False):
        self.process_calls.append(cleanup_enabled)
        return True


class FakeBranchKeeperApp:
    """Minimal TUI test double for cleanup mode wiring tests."""

    instances: ClassVar[list["FakeBranchKeeperApp"]] = []

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


# --- exit codes against a real repository ---------------------------------


def _merged_branch_repo(git_repo, monkeypatch):
    git_repo.git.checkout("-b", "feature/done")
    git_repo.git.commit("--allow-empty", "-m", "work")
    git_repo.git.checkout("main")
    git_repo.git.merge("--no-ff", "feature/done", "-m", "merge")
    monkeypatch.chdir(git_repo.working_dir)


def test_cli_delete_without_tty_exits_nonzero(git_repo, monkeypatch, capsys):
    """No terminal to confirm on used to print an EOFError and exit 0."""
    _merged_branch_repo(git_repo, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--delete", "--filter", "merged"])
    monkeypatch.setattr(
        "rich.console.Console.input", lambda *a, **k: (_ for _ in ()).throw(EOFError)
    )

    assert cli_main.main() == 1
    assert "stdin is not a terminal" in " ".join(capsys.readouterr().out.split())
    assert "feature/done" in [h.name for h in git_repo.heads]


def test_cli_partial_deletion_failure_exits_nonzero(git_repo, monkeypatch):
    _merged_branch_repo(git_repo, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--delete", "--force", "--filter", "merged"])
    real_keeper = cli_main.BranchKeeper

    def failing_keeper(*args, **kwargs):
        keeper = real_keeper(*args, **kwargs)
        keeper.delete_branch = lambda *a, **k: (False, "simulated failure")
        return keeper

    monkeypatch.setattr(cli_main, "BranchKeeper", failing_keeper)

    assert cli_main.main() == 1


def test_cli_successful_deletion_and_read_only_run_exit_zero(git_repo, monkeypatch):
    _merged_branch_repo(git_repo, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--filter", "merged"])
    assert cli_main.main() == 0

    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--delete", "--force", "--filter", "merged"])
    assert cli_main.main() == 0
    assert "feature/done" not in [h.name for h in git_repo.heads]


def test_protected_glob_pattern_blocks_deletion(git_repo, monkeypatch):
    """`--protected 'release/*'` used to be compared as an exact name and protect nothing."""
    for name in ("release/1.0", "feature/done"):
        git_repo.git.checkout("-b", name)
        git_repo.git.commit("--allow-empty", "-m", f"work on {name}")
        git_repo.git.checkout("main")
        git_repo.git.merge("--no-ff", name, "-m", f"merge {name}")
    monkeypatch.chdir(git_repo.working_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gbk", "--cli", "--delete", "--force", "--filter", "merged", "--protected", "release/*"],
    )

    assert cli_main.main() == 0

    heads = [h.name for h in git_repo.heads]
    assert "release/1.0" in heads
    assert "feature/done" not in heads


def test_force_remote_on_stale_branch_deletes_local_and_keeps_remote(git_repo, monkeypatch):
    """`--force --remote` used to fail a stale local+remote branch outright."""
    git_repo.git.checkout("-b", "feature/old")
    old = "2020-01-01T00:00:00"
    with git_repo.git.custom_environment(GIT_AUTHOR_DATE=old, GIT_COMMITTER_DATE=old):
        git_repo.git.commit("--allow-empty", "-m", "abandoned")
    git_repo.git.push("origin", "feature/old")
    git_repo.git.checkout("main")
    monkeypatch.chdir(git_repo.working_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gbk",
            "--cli",
            "--delete",
            "--force",
            "--remote",
            "--filter",
            "stale",
            "--stale-days",
            "1",
        ],
    )

    assert cli_main.main() == 0

    assert "feature/old" not in [h.name for h in git_repo.heads]
    assert "origin/feature/old" in [r.name for r in git_repo.remote("origin").refs]


def test_cli_force_never_discards_uncommitted_worktree_files(git_repo, monkeypatch, tmp_path):
    """--force skips the prompt; it used to also `git worktree remove --force` dirty trees.

    `undo` restores branches, not working files, so that loss was permanent.
    """
    _merged_branch_repo(git_repo, monkeypatch)
    wt = tmp_path / "wt-done"
    git_repo.git.worktree("add", str(wt), "feature/done")
    (wt / "notes.txt").write_text("uncommitted follow-up\n")
    git_repo.git.checkout("-b", "feature/clean")
    git_repo.git.commit("--allow-empty", "-m", "clean work")
    git_repo.git.checkout("main")
    git_repo.git.merge("--no-ff", "feature/clean", "-m", "merge clean")
    monkeypatch.setattr(sys, "argv", ["gbk", "--cli", "--delete", "--force", "--filter", "merged"])

    cli_main.main()

    assert (wt / "notes.txt").read_text() == "uncommitted follow-up\n"
    assert "feature/done" in [h.name for h in git_repo.heads]
    assert "feature/clean" not in [h.name for h in git_repo.heads]  # clean work still goes
