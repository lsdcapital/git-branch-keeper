"""Accuracy regression tests: merge detection across every merge style.

Each test builds a real repo exercising one way a branch can end up "merged" and
asserts is_branch_merged() agrees, plus which method caught it. The rebase-merge
case is the one the previous diff-based implementation missed.
"""

import os

import git
import pytest

from git_branch_keeper.services.git.merge_detector import MergeDetector


def _commit(repo, path, fname, content, msg):
    with open(os.path.join(path, fname), "w") as f:
        f.write(content)
    repo.index.add([fname])
    repo.index.commit(msg)


@pytest.fixture
def repo(temp_dir):
    path = temp_dir / "repo"
    path.mkdir()
    r = git.Repo.init(path)
    r.config_writer().set_value("user", "name", "T").release()
    r.config_writer().set_value("user", "email", "t@t.co").release()
    _commit(r, str(path), "base.txt", "base\n", "init")
    r.git.branch("-M", "main")
    return r, str(path)


def _detector(path):
    return MergeDetector(path, {"debug": False})


class TestMergeStyles:
    def test_merge_commit_no_ff(self, repo):
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f.txt", "x\n" * 20, "feat")
        r.git.checkout("main")
        r.git.merge("feature/x", "--no-ff", "-m", "Merge branch 'feature/x'")
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is True
        assert md.merge_detection_stats["reachable"] == 1

    def test_fast_forward(self, repo):
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f.txt", "x\n" * 20, "feat")
        r.git.checkout("main")
        r.git.merge("feature/x")  # fast-forward
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is True
        assert md.merge_detection_stats["reachable"] == 1

    def test_squash_single_commit(self, repo):
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f.txt", "content\n" * 20, "feat")
        r.git.checkout("main")
        r.git.merge("feature/x", "--squash")
        r.git.commit("-m", "squashed (#1)")
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is True
        # A single-commit squash is patch-identical -> caught by git cherry.
        assert md.merge_detection_stats["patch_equivalent"] == 1

    def test_squash_multi_commit(self, repo):
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f1.txt", "a\n" * 15, "a")
        _commit(r, path, "f2.txt", "b\n" * 15, "b")
        r.git.checkout("main")
        r.git.merge("feature/x", "--squash")
        r.git.commit("-m", "squashed (#2)")
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is True
        # N->1 squash has no per-commit patch match -> only combined-diff catches it.
        assert md.merge_detection_stats["squash_diff"] == 1

    def test_rebase_merge_multi_commit(self, repo):
        """Regression: this was MISSED by the old diff-only implementation."""
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f1.txt", "one\n" * 15, "r1")
        _commit(r, path, "f2.txt", "two\n" * 15, "r2")
        orig = r.refs["feature/x"].commit.hexsha
        # main diverges so the replayed commits get different SHAs (faithful rebase-merge)
        r.git.checkout("main")
        _commit(r, path, "main_only.txt", "moved\n", "main advances")
        r.git.cherry_pick("feature/x~1")
        r.git.cherry_pick("feature/x")
        assert r.refs["feature/x"].commit.hexsha == orig  # branch ref untouched
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is True
        assert md.merge_detection_stats["patch_equivalent"] == 1

    def test_cherry_pick_single(self, repo):
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "fix.txt", "the fix\n" * 15, "fix")
        orig = r.refs["feature/x"].commit.hexsha
        r.git.checkout("main")
        _commit(r, path, "main_only.txt", "moved\n", "main advances")
        r.git.cherry_pick("feature/x")
        assert r.refs["feature/x"].commit.hexsha == orig
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is True
        assert md.merge_detection_stats["patch_equivalent"] == 1

    def test_unmerged_branch_is_not_merged(self, repo):
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f.txt", "wip\n" * 20, "wip")
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is False
        assert sum(md.merge_detection_stats.values()) == 0

    def test_partially_applied_branch_is_not_merged(self, repo):
        """Only one of two branch commits is cherry-picked -> still has unmerged work."""
        r, path = repo
        r.git.checkout("-b", "feature/x")
        _commit(r, path, "f1.txt", "applied\n" * 15, "c1")
        _commit(r, path, "f2.txt", "still pending\n" * 15, "c2")
        r.git.checkout("main")
        _commit(r, path, "main_only.txt", "moved\n", "main advances")
        r.git.cherry_pick("feature/x~1")  # only the first commit
        md = _detector(path)
        assert md.is_branch_merged("feature/x", "main") is False
