"""Tests for branches that exist on the remote with no local head.

GBK used to enumerate `refs/heads/*` only. On a real repo that meant analysing 5
branches while 41 existed on origin, and reporting "nothing to clean up" for a repo
carrying dozens of prunable remote branches. Branch accumulation is a *remote*
problem - local branches are self-limiting because you notice them.

These tests pin the three things that make remote-only analysis safe:

1. Every git call resolves the name to a rev. `GIT_ERRORS` includes `LookupError`,
   and `IndexError` subclasses it, so `repo.refs["remote-only-name"]` fails
   *silently* and degrades a row to age 0 / not-merged rather than erroring.
2. Remote-only branches are never deletable. Deleting one means
   `git push origin --delete`, which the deletion journal cannot undo.
3. The temp-worktree probe is skipped. It costs a checkout per branch; running it
   for 38 remote branches would make GBK unusable.
"""

import os
from unittest.mock import patch

import git
import pytest

from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.models.branch import BranchDetails, BranchStatus, SyncStatus
from git_branch_keeper.services.branch_validation_service import BranchValidationService
from git_branch_keeper.services.git.refs import BranchRefResolver


def _commit(repo, path, fname, msg, content):
    with open(os.path.join(path, fname), "w") as f:
        f.write(content)
    repo.index.add([fname])
    repo.index.commit(msg)


@pytest.fixture
def repo_with_remote(temp_dir):
    """A working repo whose `origin` is a real bare repo on disk.

    Real refs matter here: the whole point is what happens when
    `refs/remotes/origin/<name>` exists and `refs/heads/<name>` does not.
    """
    origin_path = temp_dir / "origin.git"
    git.Repo.init(origin_path, bare=True)

    path = temp_dir / "work"
    path.mkdir()
    repo = git.Repo.init(path)
    repo.config_writer().set_value("user", "name", "T").release()
    repo.config_writer().set_value("user", "email", "t@t.co").release()
    _commit(repo, str(path), "readme.md", "init", "hello\n")
    repo.git.branch("-M", "main")
    repo.create_remote("origin", str(origin_path))
    repo.git.push("-u", "origin", "main")

    yield str(path), repo

    repo.close()


def _push_and_drop_local(repo, branch: str):
    """Push a branch, then delete the local head - leaving it remote-only."""
    repo.git.push("origin", branch)
    repo.git.checkout("main")
    repo.git.branch("-D", branch)


BODY = "\n".join(f"line {i} of the feature" for i in range(20)) + "\n"


def _keeper(path, config_overrides=None):
    config = {
        "verbose": False,
        "debug": False,
        "stale_days": 30,
        "protected_branches": ["main", "master"],
        "ignore_patterns": [],
        "status_filter": "all",
        "interactive": False,
        "dry_run": True,
        "force": False,
        "main_branch": "main",
        "github_token": None,
        "max_prs_to_fetch": 500,
        "include_remote_branches": True,
    }
    config.update(config_overrides or {})
    return BranchKeeper(path, config, tui_mode=True)


# --- resolver -------------------------------------------------------------


def test_resolver_maps_remote_only_name_to_remote_rev(repo_with_remote):
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")

    refs = BranchRefResolver(path)

    assert refs.has_local("feat/gone") is False
    assert refs.has_remote("feat/gone") is True
    assert refs.is_remote_only("feat/gone") is True
    assert refs.resolve("feat/gone") == "origin/feat/gone"


def test_resolver_prefers_local_head_when_both_exist(repo_with_remote):
    """The local ref is the one the user can lose work on, so it wins."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/both")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/both")
    repo.git.checkout("main")

    refs = BranchRefResolver(path)

    assert refs.has_local("feat/both") is True
    assert refs.has_remote("feat/both") is True
    assert refs.resolve("feat/both") == "feat/both"


def test_resolver_excludes_head_pointer(repo_with_remote):
    """origin/HEAD is a symbolic pointer; listing it produces a phantom 'HEAD' row."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")
    repo.git.remote("set-head", "origin", "main")

    names = BranchRefResolver(path).remote_only_branch_names()

    assert "HEAD" not in names
    assert "feat/gone" in names
    # main has a local head, so it is not remote-only even though origin/main exists.
    assert "main" not in names


def test_resolver_reuses_one_snapshot_until_refresh(repo_with_remote):
    """Per-branch ref resolution must not rescan every remote ref."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")
    refs = BranchRefResolver(path)

    with patch.object(refs, "_scan", wraps=refs._scan) as scan:
        assert refs.resolve("feat/gone") == "origin/feat/gone"
        assert refs.has_remote("feat/gone") is True
        assert refs.has_local("feat/gone") is False
        assert refs.resolve("feat/gone") == "origin/feat/gone"

    assert scan.call_count == 1


def test_unknown_name_is_returned_unchanged(repo_with_remote):
    """No ref either side: hand git the plain name and let it produce the error."""
    path, _ = repo_with_remote
    assert BranchRefResolver(path).resolve("never/existed") == "never/existed"


# --- enumeration and analysis --------------------------------------------


def test_remote_only_branch_is_enumerated_and_analysed(repo_with_remote):
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")

    result = _keeper(path).analyze_branches(show_progress=False)
    by_name = {b.name: b for b in result.branches}

    assert "feat/gone" in by_name
    branch = by_name["feat/gone"]
    assert branch.has_remote is True
    assert branch.has_local is False
    assert branch.sync_status == SyncStatus.REMOTE_ONLY.value
    # The silent-failure symptom: a name that failed to resolve reports age 0.
    assert branch.age_days is not None


def test_opting_out_restores_the_local_only_view(repo_with_remote):
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")

    keeper = _keeper(path, {"include_remote_branches": False})
    result = keeper.analyze_branches(show_progress=False)

    assert "feat/gone" not in {b.name for b in result.branches}


def test_remote_only_merge_detection_uses_the_remote_rev(repo_with_remote):
    """Merged work on a remote-only branch is still recognised as merged."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/merged")
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")
    repo.git.branch("-D", "feat/merged")

    result = _keeper(path).analyze_branches(show_progress=False)
    branch = {b.name: b for b in result.branches}["feat/merged"]

    assert branch.status == BranchStatus.MERGED


# --- the ambiguous zero-unique-commits case -------------------------------


def test_remote_only_with_no_unique_commits_is_unstarted_not_merged(repo_with_remote):
    """Never-started and fast-forward-merged are indistinguishable without a reflog.

    `git reflog show origin/foo` records *fetches*, not the branch's own history, so
    the positive proof `is_unstarted_branch()` normally demands is unobtainable.
    UNSTARTED is the conservative answer: it is excluded from every [STALE, MERGED]
    check, so it can never make the branch deletable.
    """
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/empty")
    repo.git.push("origin", "feat/empty")
    repo.git.checkout("main")
    repo.git.branch("-D", "feat/empty")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    branch = {b.name: b for b in result.branches}["feat/empty"]

    assert branch.status == BranchStatus.UNSTARTED
    assert keeper.git_service.unstarted_is_unverifiable("feat/empty") is True
    assert branch.notes and "no local reflog" in branch.notes


def test_remote_only_merge_commit_is_merged_not_unstarted(repo_with_remote):
    """A merge commit leaves the tip *off* main's first-parent line - that is a merge.

    Without this distinction every merge-commit repo would report its merged remote
    branches as never started, since they too have zero commits of their own.
    """
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/mc")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/mc")
    repo.git.checkout("main")
    repo.git.merge("feat/mc", "--no-ff", "-m", "Merge feat/mc")
    repo.git.branch("-D", "feat/mc")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    branch = {b.name: b for b in result.branches}["feat/mc"]

    assert branch.status == BranchStatus.MERGED
    assert keeper.git_service.unstarted_is_unverifiable("feat/mc") is False
    assert keeper.git_service.remote_history_is_unverifiable("feat/mc") is True
    assert branch.notes and "branch name" in branch.notes


def test_remote_only_reachable_tip_does_not_overstate_branch_provenance(repo_with_remote):
    """Reachability proves content landed, not when the remote branch name was created."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "source")
    _commit(repo, path, "feat.md", "feature", BODY)
    source_sha = repo.head.commit.hexsha
    repo.git.checkout("main")
    repo.git.merge("source", "--no-ff", "-m", "Merge source")
    repo.git.branch("-D", "source")

    # This branch name is created only after the source commit is already on main.
    repo.create_head("bookmark/after-merge", source_sha)
    _push_and_drop_local(repo, "bookmark/after-merge")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    branch = {b.name: b for b in result.branches}["bookmark/after-merge"]

    assert branch.status == BranchStatus.MERGED
    assert keeper.git_service.remote_history_is_unverifiable("bookmark/after-merge") is True
    assert branch.notes and "branch name" in branch.notes


def test_remote_only_fast_forward_merge_stays_ambiguous(repo_with_remote):
    """A fast-forward leaves no trace, so this one genuinely cannot be told apart."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/ff")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/ff")
    repo.git.checkout("main")
    repo.git.merge("feat/ff", "--ff-only")
    repo.git.branch("-D", "feat/ff")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    branch = {b.name: b for b in result.branches}["feat/ff"]

    assert branch.status == BranchStatus.UNSTARTED
    assert keeper.git_service.unstarted_is_unverifiable("feat/ff") is True


def test_local_unstarted_branch_carries_no_unverifiable_note(repo_with_remote):
    """A local branch has a reflog, so its UNSTARTED label is a confident claim."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/fresh")
    repo.git.checkout("main")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    branch = {b.name: b for b in result.branches}["feat/fresh"]

    assert branch.status == BranchStatus.UNSTARTED
    assert keeper.git_service.unstarted_is_unverifiable("feat/fresh") is False
    assert not (branch.notes and "no local reflog" in branch.notes)


# --- deletability ---------------------------------------------------------


def _remote_only(status: BranchStatus) -> BranchDetails:
    return BranchDetails(
        name="feat/gone",
        last_commit_date="2024-01-01",
        age_days=400,
        status=status,
        modified_files=False,
        untracked_files=False,
        staged_files=False,
        has_remote=True,
        sync_status=SyncStatus.REMOTE_ONLY.value,
        has_local=False,
    )


@pytest.mark.parametrize("status", [BranchStatus.MERGED, BranchStatus.STALE])
def test_remote_only_branch_is_never_deletable(status):
    assert BranchValidationService.is_deletable(_remote_only(status), ["main"]) is False


def test_local_counterpart_of_the_same_row_stays_deletable():
    """The guard must key on location, not on anything else about the row."""
    branch = _remote_only(BranchStatus.MERGED)
    branch.has_local = True

    assert BranchValidationService.is_deletable(branch, ["main"]) is True


def test_remote_only_merged_branch_is_absent_from_deletable_branches(repo_with_remote):
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/merged")
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")
    repo.git.branch("-D", "feat/merged")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)

    assert "feat/merged" in {b.name for b in result.branches}
    assert "feat/merged" not in {b.name for b in result.deletable_branches}


def test_force_mode_does_not_bypass_the_remote_only_guard(repo_with_remote):
    """force_mode skips is_deletable() entirely, so the guard is duplicated in the loop."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/merged")
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")
    repo.git.branch("-D", "feat/merged")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    forced = keeper.get_deletable_branches(result.branches, force_mode=True)

    assert "feat/merged" not in {b.name for b in forced}


def test_delete_boundary_refuses_remote_only_branch_even_in_dry_run(repo_with_remote):
    """A stale row must not become a successful dry-run deletion plan."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/merged")
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")
    repo.git.branch("-D", "feat/merged")
    keeper = _keeper(path)

    success, error = keeper.delete_branch("feat/merged", "merged")

    assert success is False
    assert error and "remote-only" in error
    assert "origin/feat/merged" in [ref.name for ref in repo.remote("origin").refs]


def test_git_operation_refuses_remote_only_branch_before_remote_delete(repo_with_remote):
    """The low-level mutation boundary independently enforces the invariant."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")
    keeper = _keeper(path)

    assert keeper.git_service.delete_branch("feat/gone", dry_run=True, delete_remote=True) is False
    assert "origin/feat/gone" in [ref.name for ref in repo.remote("origin").refs]


def test_local_only_branch_is_unaffected(repo_with_remote):
    """No regression: a branch that was never pushed is still local-only and deletable."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/local")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.checkout("main")
    repo.git.merge("feat/local", "--no-ff", "-m", "Merge feat/local")

    # Unpushed and unmerged, so nothing overwrites its sync status.
    repo.git.checkout("-b", "feat/local-wip")
    _commit(repo, path, "wip.md", "wip", "wip\n")
    repo.git.checkout("main")

    keeper = _keeper(path)
    result = keeper.analyze_branches(show_progress=False)
    by_name = {b.name: b for b in result.branches}

    assert by_name["feat/local-wip"].has_local is True
    assert by_name["feat/local-wip"].has_remote is False
    assert by_name["feat/local-wip"].sync_status == SyncStatus.LOCAL_ONLY.value

    assert by_name["feat/local"].status == BranchStatus.MERGED
    assert "feat/local" in {b.name for b in result.deletable_branches}


# --- performance guard ----------------------------------------------------


def test_remote_only_branch_never_builds_a_temp_worktree(repo_with_remote):
    """The probe costs a checkout per branch; 38 remote branches would be unusable."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")

    keeper = _keeper(path)
    queries = keeper.git_service.branch_queries

    with patch.object(
        queries.worktree_service,
        "create_temporary_worktree",
        side_effect=AssertionError("probed a remote-only branch"),
    ) as probe:
        details = queries.get_branch_status_details("feat/gone")

    assert probe.call_count == 0
    assert details == {"modified": False, "untracked": False, "staged": False}


def test_detail_pane_queries_never_build_a_temp_worktree(repo_with_remote):
    """The TUI's Files and Diff tabs call these directly, bypassing the analysis path."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")

    keeper = _keeper(path)
    queries = keeper.git_service.branch_queries

    with patch.object(
        queries.worktree_service,
        "create_temporary_worktree",
        side_effect=AssertionError("probed a remote-only branch"),
    ) as probe:
        files = queries.get_file_status_detailed(branch_name="feat/gone")
        diff = queries.get_diff(branch_name="feat/gone")

    assert probe.call_count == 0
    assert files == {"modified": [], "untracked": [], "staged": []}
    assert "only on the remote" in diff


# --- graph comparison -----------------------------------------------------


def test_comparison_to_main_resolves_the_remote_rev(repo_with_remote):
    """`git rev-list main..feat/gone` is a *bad revision* with no local head.

    GIT_ERRORS swallows it into `checked: False`, so the failure is quiet: every
    remote-only row loses its ahead/behind counts and drops to medium confidence
    while still looking like a complete answer.
    """
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "one.md", "one", BODY)
    _commit(repo, path, "two.md", "two", BODY)
    _push_and_drop_local(repo, "feat/gone")

    comparison = _keeper(path).git_service.branch_queries.get_comparison_to_main(
        "feat/gone", "main"
    )

    assert comparison["checked"] is True
    assert comparison["ahead"] == 2
    assert comparison["behind"] == 0
    assert comparison["tip_reachable_from_main"] is False
    assert comparison["merge_base"]


def test_branch_commits_and_divergence_resolve_the_remote_rev(repo_with_remote):
    """Both feed the TUI detail pane, and both name the branch as a rev."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "one.md", "one", BODY)
    _commit(repo, path, "two.md", "two", BODY)
    _push_and_drop_local(repo, "feat/gone")

    queries = _keeper(path).git_service.branch_queries

    commits = queries.get_branch_commits("feat/gone", "main")
    assert [c["message"] for c in commits] == ["two", "one"]

    divergence = queries.get_divergence_info("feat/gone", "main")
    assert divergence["ahead"] == 2
    assert divergence["behind"] == 0
    assert [c["message"] for c in divergence["ahead_commits"]] == ["two", "one"]


# --- accounting for what was shown but not offered ------------------------


def test_blocked_summary_names_the_reason(repo_with_remote):
    """36 merged rows then "No branches to clean up!" reads as a contradiction."""
    path, repo = repo_with_remote
    for name in ("feat/one", "feat/two"):
        repo.git.checkout("-b", name)
        _commit(repo, path, f"{name.replace('/', '-')}.md", name, BODY)
        repo.git.push("origin", name)
        repo.git.checkout("main")
        repo.git.merge("--no-ff", "-m", f"merge {name}", name)
        repo.git.branch("-D", name)

    keeper = _keeper(path)
    printed = []
    keeper._console_print = printed.append

    keeper._perform_cleanup(keeper.analyze_branches().branches)

    output = "\n".join(printed)
    assert "No branches or worktrees to clean up!" in output
    assert "2 merged/stale branches shown above, none deletable" in output
    assert "2 remote-only" in output


def test_blocked_summary_is_silent_when_nothing_was_held_back(repo_with_remote):
    """A repo with no cleanup candidates has nothing to account for."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/active")
    _commit(repo, path, "wip.md", "wip", BODY)

    keeper = _keeper(path)
    printed = []
    keeper._console_print = printed.append

    keeper._perform_cleanup(keeper.analyze_branches().branches)

    output = "\n".join(printed)
    assert "No branches or worktrees to clean up!" in output
    assert "none deletable" not in output


def test_blocking_reason_ignores_branches_that_were_never_candidates():
    """Active and unstarted branches are not "held back" - they have no signal."""
    for status in (BranchStatus.ACTIVE, BranchStatus.UNSTARTED):
        branch = _remote_only(status)
        assert BranchValidationService.blocking_reason(branch, []) is None


# --- cache ----------------------------------------------------------------


def test_remote_rows_survive_a_second_run(repo_with_remote):
    """Cache pruning must use the same namespace as branch discovery.

    `_get_current_branch_tips()` returning `repo.heads` alone would prune every
    remote row on each run, so the second launch would silently drop them.
    """
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/gone")
    _commit(repo, path, "feat.md", "feature", BODY)
    _push_and_drop_local(repo, "feat/gone")

    first = _keeper(path).analyze_branches(show_progress=False)
    assert "feat/gone" in {b.name for b in first.branches}

    second = _keeper(path).analyze_branches(show_progress=False)
    by_name = {b.name: b for b in second.branches}

    assert "feat/gone" in by_name
    assert by_name["feat/gone"].has_local is False


def test_cache_invalidates_when_local_branch_becomes_remote_only(repo_with_remote):
    """The same tip SHA must not hide a local -> remote-only transition."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/merged")
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")

    first_keeper = _keeper(path)
    first_keeper.cache_service.clear_cache()
    first = first_keeper.analyze_branches(show_progress=False)
    assert {b.name: b for b in first.branches}["feat/merged"].has_local is True

    repo.git.branch("-D", "feat/merged")
    second = _keeper(path).analyze_branches(show_progress=False)
    branch = {b.name: b for b in second.branches}["feat/merged"]

    assert branch.has_local is False
    assert "feat/merged" in second.branches_to_process
    assert "feat/merged" not in {b.name for b in second.deletable_branches}


def test_cache_invalidates_when_remote_only_branch_becomes_local(repo_with_remote):
    """The reverse location transition must expose the new local cleanup candidate."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.push("origin", "feat/merged")
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")
    repo.git.branch("-D", "feat/merged")

    first_keeper = _keeper(path)
    first_keeper.cache_service.clear_cache()
    first = first_keeper.analyze_branches(show_progress=False)
    assert {b.name: b for b in first.branches}["feat/merged"].has_local is False

    repo.create_head("feat/merged", repo.refs["origin/feat/merged"].commit)
    second = _keeper(path).analyze_branches(show_progress=False)
    branch = {b.name: b for b in second.branches}["feat/merged"]

    assert branch.has_local is True
    assert "feat/merged" in second.branches_to_process
    assert "feat/merged" in {b.name for b in second.deletable_branches}


def test_cache_invalidates_when_remote_counterpart_appears(repo_with_remote):
    """Remote presence is location state too, even when the effective tip is unchanged."""
    path, repo = repo_with_remote
    repo.git.checkout("-b", "feat/merged")
    _commit(repo, path, "feat.md", "feature", BODY)
    repo.git.checkout("main")
    repo.git.merge("feat/merged", "--no-ff", "-m", "Merge feat/merged")

    first_keeper = _keeper(path)
    first_keeper.cache_service.clear_cache()
    first = first_keeper.analyze_branches(show_progress=False)
    assert {b.name: b for b in first.branches}["feat/merged"].has_remote is False

    repo.git.push("origin", "feat/merged")
    second = _keeper(path).analyze_branches(show_progress=False)
    branch = {b.name: b for b in second.branches}["feat/merged"]

    assert branch.has_remote is True
    assert "feat/merged" in second.branches_to_process


def test_cache_round_trips_has_local(temp_dir, repo_with_remote):
    path, _ = repo_with_remote
    from git_branch_keeper.services.cache_service import CacheService

    cache = CacheService(path)
    branch = _remote_only(BranchStatus.MERGED)

    restored = cache.deserialize_branch(cache._serialize_branch(branch, tip_sha="abc123"))

    assert restored is not None
    assert restored.has_local is False


def test_legacy_cache_entries_default_to_local(repo_with_remote):
    """Entries written before remote enumeration described local branches only."""
    path, _ = repo_with_remote
    from git_branch_keeper.services.cache_service import CacheService

    cache = CacheService(path)
    data = cache._serialize_branch(_remote_only(BranchStatus.MERGED), tip_sha="abc123")
    del data["has_local"]

    restored = cache.deserialize_branch(data)

    assert restored is not None
    assert restored.has_local is True
