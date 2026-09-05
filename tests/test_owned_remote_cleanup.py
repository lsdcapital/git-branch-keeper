"""Owned remote cleanup requires positive, live PR and tip evidence."""

from unittest.mock import Mock

import pytest

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import BranchDetails, BranchStatus


@pytest.fixture
def owned_case(git_repo):
    keeper = BranchKeeper(git_repo.working_dir, Config(dry_run=False))
    name = "feature/owned"
    git_repo.create_head(name)
    sha = git_repo.head.commit.hexsha
    git_repo.git.update_ref(f"refs/remotes/origin/{name}", sha)
    row = BranchDetails(
        name=name,
        last_commit_date="2026-09-05",
        age_days=0,
        status=BranchStatus.MERGED,
        sync_status="merged-git",
        has_remote=True,
        tip_sha=sha,
        modified_files=False,
        untracked_files=False,
        staged_files=False,
    )
    info = {
        "author_login": "alice",
        "head_repo": "team/project",
        "merged": True,
        "count": 0,
        "head_sha": sha,
        "head_ref": name,
        "base_ref": "main",
    }
    keeper.github_service.github_repo = "team/project"
    keeper.github_service.get_authenticated_login = Mock(return_value="Alice")
    keeper.github_service.get_pr_data_for_branch = Mock(side_effect=lambda name: {name: info})
    keeper.github_service.has_open_pr = Mock(return_value=False)
    return keeper, row, info


@pytest.mark.parametrize(
    "change",
    [
        {"author_login": "bob"},
        {"author_login": None},
        {"head_repo": "fork/project"},
        {"merged": False},
        {"count": 1},
        {"head_sha": "different"},
        {"base_ref": "release"},
        {"error": "API unavailable"},
    ],
)
def test_scope_excludes_unverified_remote_branches(owned_case, change):
    keeper, row, info = owned_case
    info.update(change)
    assert keeper.prepare_owned_remote_cleanup([row]) == {}


def test_scope_requires_authenticated_identity(owned_case):
    keeper, row, _ = owned_case
    keeper.github_service.get_authenticated_login.return_value = None
    with pytest.raises(RuntimeError, match="GitHub account"):
        keeper.prepare_owned_remote_cleanup([row])
    assert not keeper.delete_remote


def test_scope_matches_remote_tip_and_does_not_enable_itself(owned_case, git_repo):
    keeper, row, _ = owned_case
    assert keeper.prepare_owned_remote_cleanup([row]) == {row.name: row.tip_sha}
    assert not keeper.delete_remote
    git_repo.index.commit("Remote advanced")
    git_repo.git.update_ref(f"refs/remotes/origin/{row.name}", git_repo.head.commit.hexsha)
    assert keeper.prepare_owned_remote_cleanup([row]) == {}


def test_unselected_remote_is_kept_when_deleting_local(owned_case):
    keeper, row, _ = owned_case
    keeper.delete_remote = keeper.owned_remote_only = True
    keeper.git_service.delete_branch = Mock(return_value=True)
    assert keeper.delete_branch(row.name, "merged", expected_tip_sha=row.tip_sha)[0]
    assert keeper.git_service.delete_branch.call_args.kwargs["delete_remote"] is False


@pytest.mark.parametrize("has_local", [True, False])
def test_changed_ownership_blocks_even_force_selection(owned_case, git_repo, has_local):
    keeper, row, info = owned_case
    keeper.owned_remote_tips = keeper.prepare_owned_remote_cleanup([row])
    keeper.delete_remote = keeper.owned_remote_only = True
    if not has_local:
        git_repo.delete_head(row.name)
    info["author_login"] = "bob"
    keeper.git_service.delete_branch = Mock()
    keeper.git_service.delete_remote_only_branch = Mock()
    ok, error = keeper.delete_branch(
        row.name,
        "merged",
        force_mode=True,
        expected_tip_sha=row.tip_sha,
        allow_unmerged_remote=True,
    )
    assert not ok
    assert error
    keeper.git_service.delete_branch.assert_not_called()
    keeper.git_service.delete_remote_only_branch.assert_not_called()


def test_owned_remote_local_deletion_uses_live_verifier_and_lease(owned_case, mocker):
    keeper, row, info = owned_case
    keeper.owned_remote_tips = keeper.prepare_owned_remote_cleanup([row])
    keeper.delete_remote = keeper.owned_remote_only = True
    ops = keeper.git_service
    repo = Mock()
    repo.heads = {row.name: Mock(commit=Mock(hexsha=row.tip_sha))}
    mocker.patch.object(ops, "_get_repo", return_value=repo)
    mocker.patch.object(ops, "has_remote_branch", return_value=True)
    mocker.patch.object(ops, "get_live_remote_branch_sha", return_value=(row.tip_sha, None))
    mocker.patch.object(ops, "_git_can_verify_deletion", return_value=True)
    # A valid merged PR can prove a squash merge even without local Git proof.
    mocker.patch.object(ops, "is_branch_merged", return_value=False)
    assert ops.delete_branch(
        row.name,
        delete_remote=True,
        verify_remote=lambda sha: keeper._verify_owned_remote(row.name, sha),
    )
    repo.git.push.assert_called_once_with(
        "origin", f":refs/heads/{row.name}", force_with_lease=f"refs/heads/{row.name}:{row.tip_sha}"
    )
    repo.reset_mock()
    info["count"] = 1
    assert not ops.delete_branch(
        row.name,
        delete_remote=True,
        verify_remote=lambda sha: keeper._verify_owned_remote(row.name, sha),
    )
    repo.delete_head.assert_not_called()
    repo.git.push.assert_not_called()
