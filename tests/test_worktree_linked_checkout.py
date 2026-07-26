"""Tests for running git-branch-keeper from inside a linked worktree.

`is_main` is not a safe stand-in for "not the worktree we are running in": when
GBK is invoked from a linked worktree, the *main* working tree holds a different
branch that must still be protected, and the linked worktree is the one GBK must
never remove.
"""

from pathlib import Path

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.services.git.worktrees import WorktreeService


def _merge_into_main(repo, branch_name: str) -> None:
    """Create branch_name off main with one commit and merge it back."""
    repo_path = Path(repo.working_dir)
    repo.git.checkout("main")
    repo.git.checkout("-b", branch_name)
    work_file = repo_path / f"{branch_name.replace('/', '-')}.txt"
    work_file.write_text("work\n")
    repo.index.add([work_file.name])
    repo.index.commit(f"Add {branch_name}")
    repo.git.checkout("main")
    repo.git.merge(branch_name, "--no-ff", "-m", f"Merge {branch_name}")


def _keeper(path) -> BranchKeeper:
    return BranchKeeper(
        str(path),
        Config(
            dry_run=True,
            interactive=False,
            main_branch="main",
            protected_branches=["main"],
        ),
    )


@pytest.fixture
def linked_worktree(git_repo, temp_dir):
    """Repo whose main working tree and a linked worktree each hold a merged branch.

    Yields (repo, linked_path). GBK is expected to be run from linked_path.
    """
    repo = git_repo

    _merge_into_main(repo, "feature/in-main-worktree")
    _merge_into_main(repo, "feature/in-linked-worktree")

    # Park the main working tree on a merged feature branch, so "checked out in
    # the main worktree" is distinguishable from "protected because it is main".
    repo.git.checkout("feature/in-main-worktree")

    linked_path = temp_dir / "linked"
    repo.git.worktree("add", str(linked_path), "feature/in-linked-worktree")

    yield repo, linked_path

    try:
        repo.git.worktree("remove", str(linked_path), "--force")
    except GIT_ERRORS:
        pass
    repo.git.worktree("prune", "--expire=now")


def test_current_worktree_is_identified(linked_worktree):
    repo, linked_path = linked_worktree
    service = WorktreeService(str(linked_path))

    assert service.is_current_worktree(str(linked_path))
    assert not service.is_current_worktree(repo.working_dir)

    other_paths = {Path(wt.path).resolve() for wt in service.get_other_worktrees()}
    assert Path(repo.working_dir).resolve() in other_paths
    assert linked_path.resolve() not in other_paths


def test_branch_in_main_worktree_is_protected_from_linked_worktree(linked_worktree):
    _repo, linked_path = linked_worktree
    result = _keeper(linked_path).analyze_branches()

    in_main = next(b for b in result.branches if b.name == "feature/in-main-worktree")

    assert in_main.in_worktree is True
    assert in_main.worktree_path is not None
    assert "feature/in-main-worktree" not in [b.name for b in result.deletable_branches]


def test_branch_status_reads_existing_main_worktree_instead_of_erroring(linked_worktree):
    """A branch checked out elsewhere cannot get a temp worktree - read it in place."""
    _repo, linked_path = linked_worktree
    keeper = _keeper(linked_path)

    status = keeper.git_service.get_branch_status_details("feature/in-main-worktree")

    assert "error" not in status
    assert status["in_worktree"] is True


def test_current_worktree_is_never_removable(linked_worktree):
    _repo, linked_path = linked_worktree
    result = _keeper(linked_path).analyze_branches()

    removable = [wt.worktree_path for wt in result.removable_worktrees]
    assert str(linked_path) not in removable
    assert not any(Path(p).resolve() == linked_path.resolve() for p in removable)


def test_remove_worktree_refuses_the_running_worktree(linked_worktree):
    """Git will happily remove the worktree you are standing in; GBK must not."""
    _repo, linked_path = linked_worktree
    service = WorktreeService(str(linked_path))

    success, error = service.remove_worktree(str(linked_path), force=True)

    assert success is False
    assert "running in" in error
    assert linked_path.exists()


def test_current_branch_is_not_offered_for_deletion(linked_worktree):
    """The linked worktree's own branch is merged, but it is checked out here."""
    _repo, linked_path = linked_worktree
    result = _keeper(linked_path).analyze_branches()

    assert result.current_branch == "feature/in-linked-worktree"
    assert "feature/in-linked-worktree" not in [b.name for b in result.deletable_branches]
