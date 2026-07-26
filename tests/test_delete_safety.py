"""Tests for the guards that stand between analysis and an irreversible delete.

Every case here is the same shape: GBK decided a branch was merged at some earlier
point, the branch changed afterwards, and the deletion must not go ahead on the
strength of the older answer.
"""

from pathlib import Path

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.services.cache_service import CacheService


@pytest.fixture
def merged_repo(git_repo):
    """Repo on main with feature/done merged into it."""
    repo = git_repo
    repo_path = Path(repo.working_dir)

    repo.git.checkout("-b", "feature/done")
    (repo_path / "done.txt").write_text("work\n")
    repo.index.add(["done.txt"])
    repo.index.commit("Add done")
    repo.git.checkout("main")
    repo.git.merge("feature/done", "--no-ff", "-m", "Merge feature/done")

    return repo


def _keeper(repo, dry_run=False) -> BranchKeeper:
    return BranchKeeper(
        repo.working_dir,
        Config(
            dry_run=dry_run,
            interactive=False,
            main_branch="main",
            protected_branches=["main"],
        ),
    )


def _commit_onto(repo, branch_name: str, filename: str) -> str:
    """Add a commit to branch_name and return to main. Returns the new tip SHA."""
    repo_path = Path(repo.working_dir)
    repo.git.checkout(branch_name)
    (repo_path / filename).write_text("later work\n")
    repo.index.add([filename])
    sha = repo.index.commit(f"Add {filename}").hexsha
    repo.git.checkout("main")
    return sha


def test_cache_entry_is_invalidated_when_the_branch_tip_moves(merged_repo, monkeypatch, tmp_path):
    """A cached "merged" row must not outlive the commit it described."""
    repo = merged_repo
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # First run caches feature/done as merged and therefore "stable".
    first = _keeper(repo).analyze_branches()
    assert next(b.status for b in first.branches if b.name == "feature/done").value == "merged"

    cache = CacheService(repo.working_dir)
    assert cache.load_cache()["feature/done"]["tip_sha"] == repo.heads["feature/done"].commit.hexsha
    assert "feature/done" not in cache.get_stale_branches(["feature/done"], "main")

    # Work continues on the already-merged branch.
    _commit_onto(repo, "feature/done", "more.txt")

    assert "feature/done" in cache.get_stale_branches(["feature/done"], "main")

    second = _keeper(repo).analyze_branches()
    assert next(b.status for b in second.branches if b.name == "feature/done").value != "merged"
    assert "feature/done" not in [b.name for b in second.deletable_branches]


def _strip_tip_sha(cache: CacheService, branch_name: str) -> None:
    """Rewrite a cache entry as an older version would have written it."""
    import json

    with open(cache.cache_file) as f:
        full = json.load(f)
    del full["branches"][branch_name]["tip_sha"]
    with open(cache.cache_file, "w") as f:
        json.dump(full, f)


def test_legacy_cache_entry_is_kept_when_git_still_confirms_it(merged_repo, monkeypatch, tmp_path):
    """Pre-tip_sha entries re-validate cheaply rather than forcing a full re-analysis."""
    repo = merged_repo
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    _keeper(repo).analyze_branches()
    cache = CacheService(repo.working_dir)
    _strip_tip_sha(cache, "feature/done")

    # Still reachable from main, so the cached "merged" verdict is confirmed as-is.
    assert "feature/done" not in cache.get_stale_branches(["feature/done"], "main")


def test_legacy_cache_entry_is_refreshed_when_git_cannot_confirm_it(
    merged_repo, monkeypatch, tmp_path
):
    """An unverifiable legacy entry must never be trusted."""
    repo = merged_repo
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    _keeper(repo).analyze_branches()
    cache = CacheService(repo.working_dir)
    _strip_tip_sha(cache, "feature/done")

    # New commits take the branch back out of main's history.
    _commit_onto(repo, "feature/done", "after.txt")

    assert "feature/done" in cache.get_stale_branches(["feature/done"], "main")


def test_legacy_squash_merged_entry_is_refreshed(git_repo, monkeypatch, tmp_path):
    """Squash merges are not reachable, so they fall back to a full re-analysis."""
    repo = git_repo
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo_path = Path(repo.working_dir)

    repo.git.checkout("-b", "feature/sq")
    (repo_path / "s1.txt").write_text("1\n")
    repo.index.add(["s1.txt"])
    repo.index.commit("s1")
    (repo_path / "s2.txt").write_text("2\n")
    repo.index.add(["s2.txt"])
    repo.index.commit("s2")
    repo.git.checkout("main")
    repo.git.merge("--squash", "feature/sq")
    repo.git.commit("-m", "Squashed")

    _keeper(repo).analyze_branches()
    cache = CacheService(repo.working_dir)
    _strip_tip_sha(cache, "feature/sq")

    assert "feature/sq" in cache.get_stale_branches(["feature/sq"], "main")


def test_delete_refuses_a_branch_that_gained_commits_after_analysis(merged_repo):
    """Defence in depth: even a stale in-memory decision is re-checked at delete time."""
    repo = merged_repo
    keeper = _keeper(repo)

    result = keeper.analyze_branches()
    assert "feature/done" in [b.name for b in result.deletable_branches]

    new_sha = _commit_onto(repo, "feature/done", "later.txt")

    success, error = keeper.delete_branch("feature/done", "merged")

    assert success is False
    assert "No longer merged" in error
    assert "feature/done" in [h.name for h in repo.heads]
    assert repo.heads["feature/done"].commit.hexsha == new_sha


def test_reachable_branch_is_deleted_with_the_safe_flag(merged_repo):
    """`-d` is used where git can verify the branch itself."""
    repo = merged_repo
    keeper = _keeper(repo)

    assert keeper.git_service._git_can_verify_deletion(repo, "feature/done") is True

    success, error = keeper.delete_branch("feature/done", "merged")
    assert success is True, error
    assert "feature/done" not in [h.name for h in repo.heads]


def test_squash_merged_branch_still_deletes(git_repo):
    """`-d` cannot vouch for squash merges, so `-D` must still be used for them."""
    repo = git_repo
    repo_path = Path(repo.working_dir)

    repo.git.checkout("-b", "feature/squashed")
    (repo_path / "one.txt").write_text("1\n")
    repo.index.add(["one.txt"])
    repo.index.commit("one")
    (repo_path / "two.txt").write_text("2\n")
    repo.index.add(["two.txt"])
    repo.index.commit("two")

    repo.git.checkout("main")
    repo.git.merge("--squash", "feature/squashed")
    repo.git.commit("-m", "Squashed feature")

    keeper = _keeper(repo)
    # git itself would refuse `-d` here...
    assert keeper.git_service._git_can_verify_deletion(repo, "feature/squashed") is False

    # ...but GBK's own detection recognises the squash, so cleanup still works.
    assert keeper.git_service.is_branch_merged("feature/squashed", "main") is True
    success, error = keeper.delete_branch("feature/squashed", "merged")
    assert success is True, error
    assert "feature/squashed" not in [h.name for h in repo.heads]


def test_stale_unmerged_branch_still_deletes(git_repo):
    """Stale cleanup is unmerged by definition and must not be blocked by the guards."""
    repo = git_repo
    repo_path = Path(repo.working_dir)

    repo.git.checkout("-b", "feature/abandoned")
    (repo_path / "abandoned.txt").write_text("never merged\n")
    repo.index.add(["abandoned.txt"])
    repo.index.commit("Abandoned work")
    repo.git.checkout("main")

    keeper = _keeper(repo)
    assert keeper.git_service._git_can_verify_deletion(repo, "feature/abandoned") is False

    success, error = keeper.delete_branch("feature/abandoned", "stale")
    assert success is True, error
    assert "feature/abandoned" not in [h.name for h in repo.heads]
