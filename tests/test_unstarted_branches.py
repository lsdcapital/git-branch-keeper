"""Tests for branches that were created from main and never committed to.

Such a branch has no commits of its own, so its tip is trivially an ancestor of
main and the reachability check calls it merged - asserting a merge that never
happened, and making a freshly-cut branch a cleanup candidate.

The trap here, and the reason these tests exist, is that "no commits of its own"
does *not* on its own mean "unstarted": a fast-forward-merged branch has none
either, because its commits became main's. `test_fast_forward_merged_branch_*` is
the regression guard for that - an earlier version of this check used only
`rev-list --count` and reclassified genuinely merged branches as unstarted.
"""

from pathlib import Path

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import BranchStatus, SyncStatus


def _keeper(repo) -> BranchKeeper:
    return BranchKeeper(
        repo.working_dir,
        Config(
            dry_run=False,
            interactive=False,
            main_branch="main",
            protected_branches=["main"],
        ),
    )


def _row(repo, branch_name: str):
    """Analyse the repo and return the row for branch_name."""
    result = _keeper(repo).analyze_branches()
    return next(b for b in result.branches if b.name == branch_name), result


def _commit_on(repo, branch_name: str, filename: str) -> None:
    """Create branch_name off the current branch, commit to it, return to main."""
    repo_path = Path(repo.working_dir)
    repo.git.checkout("-b", branch_name)
    (repo_path / filename).write_text("work\n")
    repo.index.add([filename])
    repo.index.commit(f"Add {filename}")
    repo.git.checkout("main")


@pytest.fixture
def unstarted_repo(git_repo):
    """Repo with feature/not-started created from main and never committed to."""
    git_repo.git.branch("feature/not-started")
    return git_repo


@pytest.fixture
def ff_merged_repo(git_repo):
    """Repo with feature/done fast-forward merged into main.

    The distinguishing case: like an unstarted branch, this has zero commits
    relative to main, because the fast-forward moved main to the branch's tip.
    """
    _commit_on(git_repo, "feature/done", "done.txt")
    git_repo.git.merge("feature/done", "--ff")
    return git_repo


class TestUnstartedDetection:
    def test_branch_never_committed_to_is_unstarted(self, unstarted_repo):
        branch, _ = _row(unstarted_repo, "feature/not-started")

        assert branch.status == BranchStatus.UNSTARTED
        assert branch.sync_status == SyncStatus.NO_COMMITS.value

    def test_unstarted_branch_is_not_a_cleanup_candidate(self, unstarted_repo):
        """The whole point: it must never be offered for deletion."""
        branch, result = _row(unstarted_repo, "feature/not-started")

        assert branch.status not in (BranchStatus.STALE, BranchStatus.MERGED)
        assert "feature/not-started" not in [b.name for b in result.deletable_branches]

    def test_branch_created_by_worktree_add_is_unstarted(self, git_repo, tmp_path):
        """The motivating case: a Conductor-style `git worktree add -b` workspace.

        The branch is cut for a task, the work is still uncommitted in the worktree,
        and deleting it (or even labelling it merged) is wrong.
        """
        git_repo.git.worktree("add", "-b", "conductor-task", str(tmp_path / "wt"))

        branch, result = _row(git_repo, "conductor-task")

        assert branch.status == BranchStatus.UNSTARTED
        assert "conductor-task" not in [b.name for b in result.deletable_branches]

    def test_unstarted_branch_is_not_stale_regardless_of_age(self, unstarted_repo, monkeypatch):
        """Age is main's commit date here, not the branch's, so staleness is meaningless."""
        from git_branch_keeper.services.git.operations import GitOperations

        monkeypatch.setattr(GitOperations, "get_branch_age", lambda self, name: 9999)

        branch, _ = _row(unstarted_repo, "feature/not-started")

        assert branch.status == BranchStatus.UNSTARTED


class TestMergedBranchesStayMerged:
    """Guards against the unstarted check swallowing genuinely merged branches."""

    def test_fast_forward_merged_branch_is_still_merged(self, ff_merged_repo):
        branch, _ = _row(ff_merged_repo, "feature/done")

        assert branch.status == BranchStatus.MERGED
        assert branch.sync_status == SyncStatus.MERGED_GIT.value

    def test_fast_forward_merged_branch_is_still_deletable(self, ff_merged_repo):
        _, result = _row(ff_merged_repo, "feature/done")

        assert "feature/done" in [b.name for b in result.deletable_branches]

    def test_merge_commit_branch_is_still_merged(self, git_repo):
        _commit_on(git_repo, "feature/done", "done.txt")
        git_repo.git.merge("feature/done", "--no-ff", "-m", "Merge feature/done")

        branch, result = _row(git_repo, "feature/done")

        assert branch.status == BranchStatus.MERGED
        assert "feature/done" in [b.name for b in result.deletable_branches]

    def test_unmerged_branch_with_commits_is_unaffected(self, git_repo):
        _commit_on(git_repo, "feature/wip", "wip.txt")

        branch, result = _row(git_repo, "feature/wip")

        assert branch.status == BranchStatus.ACTIVE
        assert "feature/wip" not in [b.name for b in result.deletable_branches]


class TestFallsBackWhenItCannotProve:
    """Unstarted requires positive evidence; without it, ordinary detection wins."""

    def test_branch_without_a_reflog_is_not_claimed_unstarted(self, unstarted_repo):
        """Reflogs expire, are disabled, or never exist on a fresh clone.

        Absent that evidence the branch is indistinguishable from a fast-forward
        merge, so it must fall through to merge detection rather than be labelled
        unstarted - the label is never a cleanup candidate and would hide a
        genuinely merged branch.
        """
        reflog = (
            Path(unstarted_repo.git_dir) / "logs" / "refs" / "heads" / "feature" / "not-started"
        )
        assert reflog.exists()
        reflog.unlink()

        branch, _ = _row(unstarted_repo, "feature/not-started")

        assert branch.status != BranchStatus.UNSTARTED

    def test_branch_reset_back_onto_main_is_not_claimed_unstarted(self, git_repo):
        """The branch moved, so we cannot prove it never held work."""
        _commit_on(git_repo, "feature/reset", "gone.txt")
        git_repo.git.branch("-f", "feature/reset", "main")

        branch, _ = _row(git_repo, "feature/reset")

        assert branch.status != BranchStatus.UNSTARTED

    def test_main_branch_is_never_unstarted(self, unstarted_repo):
        branch, _ = _row(unstarted_repo, "main")

        assert branch.status != BranchStatus.UNSTARTED
