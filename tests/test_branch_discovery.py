"""Tests for discovering real branches separately from custom refs/worktree metadata."""

import json
from pathlib import Path

import pytest

from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.services.cache_service import CacheService


@pytest.fixture
def isolated_home(temp_dir, monkeypatch):
    """Redirect branch-keeper cache/journal files under the test temp directory."""
    fake_home = temp_dir / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def _cache_entry(name: str) -> dict:
    return {
        "name": name,
        "last_commit_date": "2026-05-14",
        "age_days": 29,
        "status": "active",
        "modified_files": False,
        "untracked_files": False,
        "staged_files": False,
        "has_remote": False,
        "sync_status": "local-only",
        "pr_status": None,
        "notes": None,
        "stable": False,
        "cached_at": "2026-06-12T00:00:00",
    }


def test_custom_refs_are_not_listed_as_branches(git_repo, mock_config, isolated_home):
    """Only refs/heads/* should produce branch rows; custom refs are not branches."""
    custom_ref_name = "session-2fda9faf-turn-e75cda7e-end"
    git_repo.git.update_ref(
        f"refs/conductor-checkpoints/{custom_ref_name}", git_repo.head.commit.hexsha
    )

    config = mock_config.copy()
    config["refresh"] = True
    keeper = BranchKeeper(git_repo.working_dir, config)
    keeper.cache_service.clear_cache()

    branch_names = keeper._get_filtered_branches()
    assert "main" in branch_names
    assert custom_ref_name not in branch_names
    assert f"refs/conductor-checkpoints/{custom_ref_name}" not in branch_names

    branch_details = keeper.get_branch_details(show_progress=False)
    displayed_names = {branch.name for branch in branch_details}
    assert "main" in displayed_names
    assert custom_ref_name not in displayed_names


def test_cache_save_prunes_entries_that_are_not_local_branches(git_repo, isolated_home):
    """Refresh should not keep stale cache entries for custom refs/non-branches."""
    service = CacheService(git_repo.working_dir)
    custom_ref_name = "session-2fda9faf-turn-e75cda7e-end"

    cache_data = {
        "repo_path": git_repo.working_dir,
        "main_branch": "main",
        "last_updated": "2026-06-12T00:00:00",
        "branches": {
            "main": _cache_entry("main"),
            custom_ref_name: _cache_entry(custom_ref_name),
        },
    }
    service.cache_file.write_text(json.dumps(cache_data))

    assert custom_ref_name in service.load_cache()

    service.save_cache([], "main")

    cache_after_save = service.load_cache()
    assert "main" in cache_after_save
    assert custom_ref_name not in cache_after_save
