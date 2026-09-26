"""Unit tests for the TUI's branch-marking logic (safety-critical).

These exercise the pure marking methods directly (no running event loop needed),
covering protected-branch rejection, the uncommitted-changes gate and force
override, worktree hierarchy marking, and unmarking. The TUI previously had no
test coverage at all.
"""

import pytest
from textual.widgets import Static

from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.formatters import format_display_status
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.ui.app import BranchKeeperApp
from git_branch_keeper.ui.screens import TabbedInfoScreen


def _branch(
    name,
    *,
    status=BranchStatus.MERGED,
    modified=False,
    untracked=False,
    staged=False,
    is_worktree=False,
    in_worktree=False,
    worktree_path=None,
    has_remote=False,
    has_local=True,
):
    return BranchDetails(
        name=name,
        last_commit_date="2024-01-01",
        age_days=10,
        status=status,
        modified_files=modified,
        untracked_files=untracked,
        staged_files=staged,
        has_remote=has_remote,
        has_local=has_local,
        sync_status="local-only",
        is_worktree=is_worktree,
        in_worktree=in_worktree,
        worktree_path=worktree_path,
    )


@pytest.fixture
def app(git_repo):
    """A BranchKeeperApp over a real (local) repo, not run - just constructed."""
    keeper = BranchKeeper(
        git_repo.working_dir,
        Config(interactive=True, dry_run=True, main_branch="main"),
    )
    return BranchKeeperApp(keeper, branches=[], cleanup_mode=False, view="all")


class TestMarkWithHierarchy:
    def test_mark_normal_branch_succeeds(self, app):
        app.branches = [_branch("feature/clean")]
        ok, err = app._mark_with_hierarchy("feature/clean", app.marked_branches)
        assert ok is True and err is None
        assert "feature/clean" in app.marked_branches

    def test_protected_branch_is_rejected(self, app):
        app.branches = [_branch("main", status=BranchStatus.ACTIVE)]
        ok, err = app._mark_with_hierarchy("main", app.marked_branches)
        assert ok is False
        assert "protected" in err.lower()
        assert app.marked_branches == set()

    def test_uncommitted_changes_rejected_without_force(self, app):
        app.branches = [_branch("feature/dirty", modified=True)]
        ok, err = app._mark_with_hierarchy("feature/dirty", app.marked_branches)
        assert ok is False
        assert "uncommitted" in err.lower()
        assert "force-mark" in err.lower()
        assert app.marked_branches == set()

    def test_force_mark_overrides_uncommitted(self, app):
        app.branches = [_branch("feature/dirty", staged=True)]
        ok, err = app._mark_with_hierarchy(
            "feature/dirty", app.force_marked_branches, is_force=True
        )
        assert ok is True and err is None
        assert "feature/dirty" in app.force_marked_branches

    @pytest.mark.parametrize("is_force", [False, True])
    def test_merged_remote_only_branch_can_be_marked(self, app, is_force):
        app.branches = [_branch("feature/remote-only", has_remote=True, has_local=False)]
        app.keeper.delete_remote = True
        mark_set = app.force_marked_branches if is_force else app.marked_branches

        ok, err = app._mark_with_hierarchy("feature/remote-only", mark_set, is_force=is_force)

        assert ok is True
        assert err is None
        assert mark_set == {"feature/remote-only"}

    def test_stale_remote_only_branch_cannot_be_marked(self, app):
        app.keeper.delete_remote = True
        app.branches = [
            _branch(
                "feature/remote-only",
                status=BranchStatus.STALE,
                has_remote=True,
                has_local=False,
            )
        ]

        ok, err = app._mark_with_hierarchy("feature/remote-only", app.marked_branches)

        assert ok is False
        assert "unless it is merged" in err.lower()
        assert app.marked_branches == set()

    def test_unknown_branch_returns_error(self, app):
        app.branches = [_branch("feature/x")]
        ok, err = app._mark_with_hierarchy("does/not/exist", app.marked_branches)
        assert ok is False
        assert "not found" in err.lower()

    def test_branch_and_worktree_marked_together(self, app):
        # A branch entry and a worktree entry sharing the same name are marked as a unit.
        app.branches = [
            _branch("feature/wt"),
            _branch("feature/wt", is_worktree=True, worktree_path="/tmp/wt"),
        ]
        ok, err = app._mark_with_hierarchy("feature/wt", app.marked_branches)
        assert ok is True and err is None
        # Both entries share the name; the single name is recorded once.
        assert "feature/wt" in app.marked_branches

    def test_worktree_uncommitted_blocks_the_pair(self, app):
        # If the worktree sibling has uncommitted changes, the whole mark is blocked.
        app.branches = [
            _branch("feature/wt"),
            _branch("feature/wt", is_worktree=True, worktree_path="/tmp/wt", modified=True),
        ]
        ok, err = app._mark_with_hierarchy("feature/wt", app.marked_branches)
        assert ok is False
        assert "worktree" in err.lower()
        assert app.marked_branches == set()


class TestOnlyCleanupCandidatesAreMarkable:
    """Space on a non-candidate used to mark it; confirm then dropped the branch but
    still removed its worktree - with any ignored files such as .env."""

    @pytest.mark.parametrize("status", [BranchStatus.ACTIVE, BranchStatus.UNSTARTED])
    def test_normal_mark_rejects_non_candidates(self, app, status):
        app.branches = [
            _branch("feature/live", status=status, in_worktree=True, worktree_path="/tmp/live"),
            _branch("feature/live", status=status, is_worktree=True, worktree_path="/tmp/live"),
        ]
        ok, err = app._mark_with_hierarchy("feature/live", app.marked_branches)
        assert ok is False
        assert "stale or merged" in err
        assert app.marked_branches == set()

    def test_confirm_never_removes_worktree_of_active_branch(self, app, monkeypatch):
        app.branches = [
            _branch(
                "feature/live",
                status=BranchStatus.ACTIVE,
                in_worktree=True,
                worktree_path="/tmp/live",
            ),
            _branch(
                "feature/live",
                status=BranchStatus.ACTIVE,
                is_worktree=True,
                worktree_path="/tmp/live",
            ),
        ]
        app.marked_branches.add("feature/live")  # e.g. a mark that predates a refresh
        captured = {}
        monkeypatch.setattr(app, "_set_refreshing", lambda *a, **k: None)
        monkeypatch.setattr(
            app, "delete_marked_items", lambda *args: captured.setdefault("args", args)
        )

        app._handle_delete_confirmation(True)

        if "args" in captured:
            _, _, normal_branches, normal_worktrees, _ = captured["args"]
            assert normal_worktrees == [] and normal_branches == []


class TestWorktreeCleanupPlanning:
    def test_clean_worktree_removal_unblocks_parent_branch_deletion(self, app):
        parent = _branch("feature/wt", in_worktree=True, worktree_path="/tmp/wt")
        worktree = _branch("feature/wt", is_worktree=True, worktree_path="/tmp/wt")

        unblocked = app.keeper.get_branches_unblocked_by_worktree_removal(
            [parent, worktree],
            branches_to_delete=[],
            worktrees_to_remove=[worktree],
            force_mode=False,
        )

        assert unblocked == [parent]

    def test_dirty_worktree_parent_requires_force_to_delete_after_removal(self, app):
        parent = _branch(
            "feature/wt",
            modified=True,
            in_worktree=True,
            worktree_path="/tmp/wt",
        )
        worktree = _branch(
            "feature/wt",
            modified=True,
            is_worktree=True,
            worktree_path="/tmp/wt",
        )

        normal = app.keeper.get_branches_unblocked_by_worktree_removal(
            [parent, worktree],
            branches_to_delete=[],
            worktrees_to_remove=[worktree],
            force_mode=False,
        )
        forced = app.keeper.get_branches_unblocked_by_worktree_removal(
            [parent, worktree],
            branches_to_delete=[],
            worktrees_to_remove=[worktree],
            force_mode=True,
        )

        assert normal == []
        assert forced == [parent]


class TestDisplayStatus:
    def test_dirty_merged_branch_keeps_status_and_explains_cleanup(self, app):
        branch = _branch("feature/dirty", modified=True)

        assert format_display_status(branch, app.keeper.protected_branches) == "merged"
        from git_branch_keeper.formatters.status import format_cleanup_reason

        assert (
            format_cleanup_reason(branch, app.keeper.protected_branches)
            == "has uncommitted changes"
        )

    def test_clean_worktree_parent_stays_merged_when_cleanup_can_remove_worktree(self, app):
        branch = _branch("feature/wt", in_worktree=True, worktree_path="/tmp/wt")

        assert format_display_status(branch, app.keeper.protected_branches) == "merged"


class TestInfoDeletionBlockers:
    def test_info_screen_lists_worktree_and_dirty_blockers(self, app):
        branch = _branch(
            "feature/wt",
            modified=True,
            untracked=True,
            in_worktree=True,
            worktree_path="/tmp/feature-wt",
        )
        screen = TabbedInfoScreen(branch, app.keeper, "main")

        blockers = screen._build_deletion_blockers()

        assert "Branch is checked out in worktree: /tmp/feature-wt" in blockers
        assert "Worktree/branch has modified files" in blockers
        assert "Worktree/branch has untracked files" in blockers


HOSTILE = [
    "[@click=app.delete_marked]CLICK ME[/]",  # would render a link that runs an action
    're.split(r"[/\\]", p)',  # `[/]` closing nothing crashed the whole TUI
    "chore: [skip ci] bump",  # a valid-looking tag silently vanished
    "trailing backslash \\",  # defeats string escaping: escapes the next closing tag
]


class TestInfoScreenRendersUntrustedTextLiterally:
    """Commit messages, diffs, file contents, paths and notes are data, never markup."""

    @pytest.fixture
    def screen(self, app, monkeypatch):
        git = app.keeper.git_service
        commits = [
            {"sha": "abc1234", "date": "2024-01-01", "author": h, "message": h} for h in HOSTILE
        ]
        monkeypatch.setattr(git, "get_branch_commits", lambda *a, **k: commits)
        monkeypatch.setattr(
            git,
            "get_divergence_info",
            lambda *a, **k: {
                "ahead": 4,
                "behind": 0,
                "ahead_commits": commits,
                "behind_commits": [],
            },
        )
        monkeypatch.setattr(git, "get_diff", lambda **k: "\n".join(HOSTILE))
        monkeypatch.setattr(
            git,
            "get_file_status_detailed",
            lambda **k: {"staged": HOSTILE, "modified": [], "untracked": []},
        )
        monkeypatch.setattr(
            git,
            "get_merge_details",
            lambda *a: {
                "found": True,
                "merge_sha": "abc1234",
                "merge_message": HOSTILE[0],
                "merge_author": HOSTILE[3],
                "merge_date": "2024-01-01",
            },
        )
        branch = _branch("feature/x", modified=True)
        branch.notes = "[ERROR] " + " ".join(HOSTILE)
        return TabbedInfoScreen(branch, app.keeper, "main")

    @pytest.mark.parametrize(
        "builder",
        [
            "_build_info_tab",
            "_build_files_tab",
            "_build_diff_tab",
            "_build_merge_tab",
            "_build_commits_tab",
            "_build_divergence_tab",
            "_build_history_tab",
        ],
    )
    def test_tab_keeps_hostile_text_verbatim(self, screen, builder):
        widget = getattr(screen, builder)()
        # Tabs other than Info wrap their Static in an unmounted ScrollableContainer.
        static = widget if isinstance(widget, Static) else widget._pending_children[0]
        content = static.content

        expected = [HOSTILE[0], HOSTILE[3]] if builder == "_build_merge_tab" else HOSTILE
        for text in expected:
            assert text in content.plain, f"{builder} altered {text!r}"
        assert not any("click" in str(span.style) for span in content.spans)

    def test_error_label_still_styled(self, screen):
        content = screen._build_info_tab().content
        assert "ERROR:" in content.plain
        assert "[ERROR]" not in content.plain
        start = content.plain.index("ERROR:")
        assert any(
            span.start <= start < span.end and "red" in str(span.style) for span in content.spans
        )


class TestUnmark:
    def test_unmark_clears_both_sets(self, app):
        app.marked_branches.add("feature/a")
        app.force_marked_branches.add("feature/a")
        app._unmark_with_hierarchy("feature/a")
        assert "feature/a" not in app.marked_branches
        assert "feature/a" not in app.force_marked_branches
