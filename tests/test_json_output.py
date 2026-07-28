"""Tests for machine-readable JSON output."""

import json
import sys
from pathlib import Path

from git_branch_keeper.cli.main import main
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.formatters.json_output import _pr_status, analysis_to_dict, schema_to_dict
from git_branch_keeper.models.branch import BranchDetails, BranchStatus


def test_analysis_to_dict_is_json_serializable(git_repo, mock_config, temp_dir, monkeypatch):
    """The shared analysis model should serialize to stable JSON data."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    keeper = BranchKeeper(git_repo.working_dir, mock_config, tui_mode=True)
    analysis = keeper.analyze_branches(show_progress=False)
    payload = analysis_to_dict(keeper, analysis)

    encoded = json.dumps(payload)
    decoded = json.loads(encoded)

    assert decoded["ok"] is True
    assert decoded["operation"] == "branch.scan"
    assert decoded["mode"] == "read-only"
    assert decoded["schema_version"] == 4
    assert decoded["branches"]
    branch = decoded["branches"][0]
    assert "is_current_branch" in branch
    assert "last_commit_at" in branch
    assert "last_commit_date" not in branch
    assert "pr" in branch
    assert "pr_status" not in branch
    assert "comparison_to_main" in branch
    assert "merge_detection" in branch
    assert branch["deletion"]["blockers"] is not None
    assert all(
        "code" in blocker and "message" in blocker for blocker in branch["deletion"]["blockers"]
    )


def test_pr_status_includes_provider_metadata():
    """Structured JSON PR output includes PR head/merge metadata when available."""
    branch = BranchDetails(
        name="feature/pr",
        last_commit_date="2024-01-01",
        age_days=1,
        status=BranchStatus.MERGED,
        modified_files=False,
        untracked_files=False,
        staged_files=False,
        has_remote=True,
        sync_status="merged-pr",
        pr_details={
            "number": 35,
            "url": "https://github.com/acme/repo/pull/35",
            "head_sha": "abc123",
            "merge_commit_sha": "def456",
            "head_matches_local": True,
            "local_head_sha": "abc123",
        },
    )

    pr = _pr_status(branch, github_enabled=True, github_disabled_reason="")

    assert pr["status"] == "merged"
    assert pr["number"] == 35
    assert pr["head_sha"] == "abc123"
    assert pr["merge_commit_sha"] == "def456"
    assert pr["head_matches_local"] is True


def test_schema_to_dict_is_json_serializable():
    """Agents can inspect command capabilities and schema shape."""
    payload = schema_to_dict()
    decoded = json.loads(json.dumps(payload))

    assert decoded["ok"] is True
    assert decoded["schema_version"] == 4
    assert decoded["application"] == "git-branch-keeper"
    assert "branch.scan" in {command["name"] for command in decoded["capabilities"]["commands"]}
    assert "is_current_branch" in decoded["branch_scan_result"]["branch_fields"]


def test_cli_json_scan_outputs_clean_json(git_repo, temp_dir, monkeypatch, capsys):
    """--output json should print machine-readable scan results to stdout."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.chdir(git_repo.working_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        ["git-branch-keeper", "--output", "json", "--no-interactive"],
    )

    assert main() == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert captured.err == ""
    assert payload["ok"] is True
    assert payload["operation"] == "branch.scan"
    assert payload["repo"]["path"] == git_repo.working_dir


def test_cli_json_scan_is_read_only(git_repo_with_branches, temp_dir, monkeypatch, capsys):
    """JSON scan mode should not run cleanup, even with cleanup-related CLI defaults."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.chdir(git_repo_with_branches.working_dir)

    before = {head.name for head in git_repo_with_branches.heads}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "git-branch-keeper",
            "--output",
            "json",
            "--no-interactive",
            "--filter",
            "merged",
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    after = {head.name for head in git_repo_with_branches.heads}

    assert payload["mode"] == "read-only"
    assert before == after


def test_cli_schema_command_outputs_json(monkeypatch, capsys):
    """schema command should be machine-readable."""
    monkeypatch.setattr(sys, "argv", ["git-branch-keeper", "schema", "--output", "json"])

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["application"] == "git-branch-keeper"


def test_unsupported_json_command_returns_json_error(monkeypatch, capsys):
    """Commands without JSON support should still honor the JSON contract."""
    monkeypatch.setattr(sys, "argv", ["git-branch-keeper", "undo", "--output", "json"])

    assert main() == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "JSON_UNSUPPORTED_FOR_COMMAND"
