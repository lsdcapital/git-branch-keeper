"""Tests for partial-merge reporting: some commits landed in main, some did not.

The motivating case is a squash-merge repo where a commit is pushed to the branch
moments after its PR merges - the PR's work is in main, the straggler is not. Merge
detection correctly says "not merged", but reporting that as a bare `active` branch
makes it indistinguishable from one being worked on, and the orphaned commit is
invisible unless GitHub auth happens to be configured.

`git cherry` already computes the per-commit answer; these tests pin that it is
surfaced rather than collapsed to a boolean, and that it stays advisory.
"""

import os

import git
import pytest

from git_branch_keeper.services.git.merge_detector import MergeDetector


def _commit(repo, path, fname, msg, content):
    with open(os.path.join(path, fname), "w") as f:
        f.write(content)
    repo.index.add([fname])
    repo.index.commit(msg)


@pytest.fixture
def repo_path(temp_dir):
    path = temp_dir / "repo"
    path.mkdir()
    repo = git.Repo.init(path)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.co").release()
    _commit(repo, str(path), "readme.md", "init", "hello\n")
    repo.git.branch("-M", "main")
    return str(path), repo


DOCS = "\n".join(f"docs line {i} about coverage limits" for i in range(20)) + "\n"


def test_commit_orphaned_by_squash_merge_is_reported_partial(repo_path):
    """The ficta case: PR squash-merges the docs commit, changeset lands 73s later."""
    path, repo = repo_path

    repo.git.checkout("-b", "docs/coverage-limits")
    _commit(repo, path, "docs.md", "Document coverage limits", DOCS)
    _commit(repo, path, "changeset.md", "Add changeset for the docs", "patch: bump\n")

    # Only the docs commit reaches main, under a new SHA.
    repo.git.checkout("main")
    _commit(repo, path, "docs.md", "Document coverage limits (#89)", DOCS)

    md = MergeDetector(path, {"debug": False})

    # Still not merged - the changeset is not in main, so deleting would lose it.
    assert md.is_branch_merged("docs/coverage-limits", "main") is False
    # ...but we now say *how much* landed, instead of nothing at all.
    assert md.get_partial_merge("docs/coverage-limits") == (1, 2)


def test_fully_merged_branch_is_not_partial(repo_path):
    """All commits applied is a merge, handled by the caller - not a partial."""
    path, repo = repo_path

    repo.git.checkout("-b", "feature/done")
    _commit(repo, path, "a.md", "first", DOCS)
    _commit(repo, path, "b.md", "second", "second body\n")

    repo.git.checkout("main")
    _commit(repo, path, "a.md", "first (#1)", DOCS)
    _commit(repo, path, "b.md", "second (#2)", "second body\n")

    md = MergeDetector(path, {"debug": False})

    assert md.is_branch_merged("feature/done", "main") is True
    assert md.get_partial_merge("feature/done") is None


def test_branch_with_nothing_landed_is_not_partial(repo_path):
    """An ordinary in-progress branch must not pick up a note.

    Reporting "0/2 commits in main" is technically true of every active branch in
    every repo, so it would be noise on the one column that has to stay scannable.
    """
    path, repo = repo_path

    repo.git.checkout("-b", "feature/wip")
    _commit(repo, path, "wip.md", "wip one", "wip one\n")
    _commit(repo, path, "wip2.md", "wip two", "wip two\n")

    md = MergeDetector(path, {"debug": False})

    assert md.is_branch_merged("feature/wip", "main") is False
    assert md.get_partial_merge("feature/wip") is None


def test_partial_state_clears_once_the_straggler_lands(repo_path):
    """Recheck after the missing commit reaches main: partial gone, branch merged."""
    path, repo = repo_path

    repo.git.checkout("-b", "docs/coverage-limits")
    _commit(repo, path, "docs.md", "Document coverage limits", DOCS)
    _commit(repo, path, "changeset.md", "Add changeset for the docs", "patch: bump\n")

    repo.git.checkout("main")
    _commit(repo, path, "docs.md", "Document coverage limits (#89)", DOCS)

    md = MergeDetector(path, {"debug": False})
    assert md.is_branch_merged("docs/coverage-limits", "main") is False
    assert md.get_partial_merge("docs/coverage-limits") == (1, 2)

    # The rescue PR lands the changeset.
    _commit(repo, path, "changeset.md", "Recover orphaned changeset (#93)", "patch: bump\n")

    assert md.is_branch_merged("docs/coverage-limits", "main", force_refresh=True) is True
    assert md.get_partial_merge("docs/coverage-limits") is None


def test_partial_merge_is_unknown_before_detection_runs(repo_path):
    """Mirrors is_likely_squash_merged: only meaningful after is_branch_merged()."""
    path, _ = repo_path
    md = MergeDetector(path, {"debug": False})
    assert md.get_partial_merge("never/checked") is None
