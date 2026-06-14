"""Tests for branch processing worker-count selection."""

from typing import Any, Optional, cast

from git_branch_keeper.core.branch_keeper import BranchKeeper, GITHUB_ENABLED_WORKER_CAP


class DummyGitHubService:
    """Small test double exposing the GitHub enabled flag."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled


def _keeper_with_workers(workers: Optional[int], github_enabled: bool) -> Any:
    keeper = cast(Any, object.__new__(BranchKeeper))
    keeper.config = {"workers": workers}
    keeper.github_service = DummyGitHubService(github_enabled)
    return keeper


def test_worker_count_caps_to_branch_count() -> None:
    keeper = _keeper_with_workers(32, github_enabled=False)

    assert keeper._get_worker_count_for_branches(3) == 3


def test_worker_count_caps_github_enabled_processing_below_api_pool() -> None:
    keeper = _keeper_with_workers(32, github_enabled=True)

    assert keeper._get_worker_count_for_branches(32) == GITHUB_ENABLED_WORKER_CAP


def test_worker_count_does_not_exceed_small_branch_count_when_github_enabled() -> None:
    keeper = _keeper_with_workers(32, github_enabled=True)

    assert keeper._get_worker_count_for_branches(3) == 3
