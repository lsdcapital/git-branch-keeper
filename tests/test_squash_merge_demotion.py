"""Tests that fuzzy squash-merge matches are advisory, not deletion triggers.

Exact diff matches still count as merged (the normal clean squash-merge case),
but a high-similarity *substring* match must never mark a branch as merged,
because diff-text containment does not prove the work is actually in main.
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


def test_exact_diff_match_still_counts_as_merged(repo_path):
    path, repo = repo_path
    body = "\n".join(f"line {i} of work" for i in range(40)) + "\n"

    # Branch makes a change, then main gets that exact change as a squash commit.
    repo.git.checkout("-b", "feature/x")
    _commit(repo, path, "feature.py", "feat", body)
    repo.git.checkout("main")
    _commit(repo, path, "feature.py", "squash of feature/x", body)

    md = MergeDetector(path, {"debug": False})
    assert md.is_branch_merged("feature/x", "main") is True
    assert md.is_likely_squash_merged("feature/x") is False  # exact, not fuzzy


def test_fuzzy_match_is_advisory_not_merged(repo_path):
    """A reverted squash commit: content was added then removed from main.

    Reachability methods correctly say 'not merged'. The diff still matches a
    historical commit, but the branch must NOT be declared merged - only flagged.
    """
    path, repo = repo_path
    body = "\n".join(f"line {i} of important feature work here" for i in range(40)) + "\n"

    repo.git.checkout("-b", "feature/x")
    _commit(repo, path, "feature.py", "feat", body)

    # main: a commit whose diff CONTAINS the branch diff as a >90% prefix substring
    # (same feature.py plus a tiny extra file), then a revert of feature.py so the
    # branch's content is NOT in main's current tree.
    repo.git.checkout("main")
    for fn, content in {"feature.py": body, "z.txt": "x\n"}.items():
        with open(os.path.join(path, fn), "w") as f:
            f.write(content)
    repo.index.add(["feature.py", "z.txt"])
    repo.index.commit("bigger change containing branch work")
    os.remove(os.path.join(path, "feature.py"))
    repo.index.remove(["feature.py"])
    repo.index.commit("revert: remove feature.py")

    md = MergeDetector(path, {"debug": False})

    # The reliable checks agree it is not merged
    assert md._check_reachable("feature/x", "main") is False
    assert md._check_patch_equivalent("feature/x", "main") is False

    # Overall: NOT merged (fuzzy match no longer promotes to merged)...
    assert md.is_branch_merged("feature/x", "main") is False
    # ...but it IS flagged as a possible squash-merge for the user to verify
    assert md.is_likely_squash_merged("feature/x") is True

    # And the content really is gone from main's tree (would be data loss if deleted)
    assert "feature.py" not in repo.git.ls_files().split()


def test_unrelated_branch_not_flagged(repo_path):
    path, repo = repo_path
    repo.git.checkout("-b", "feature/y")
    _commit(repo, path, "y.py", "unrelated", "totally different content\n" * 10)

    md = MergeDetector(path, {"debug": False})
    assert md.is_branch_merged("feature/y", "main") is False
    assert md.is_likely_squash_merged("feature/y") is False
