"""Main TUI application for git-branch-keeper."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, ClassVar

import git
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.coordinate import Coordinate
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Static

from git_branch_keeper.constants import (
    COLUMNS,
    LEGEND_TEXT,
    SYMBOL_MARKED,
    SYMBOL_UNMARKED,
    TUI_COLORS,
    BranchStyleType,
)
from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.formatters import (
    format_age,
    format_branch_name_with_indent,
    format_changes,
    format_date,
    format_deletion_confirmation_items,
    format_display_status,
    format_pr_link,
    format_remote_status,
    get_branch_style_type,
)
from git_branch_keeper.models.branch import (
    BranchAnalysisProgress,
    BranchAnalysisResult,
    BranchDetails,
    BranchStatus,
)
from git_branch_keeper.services.branch_validation_service import BranchValidationService
from git_branch_keeper.services.undo_service import pick_latest_batch, restore_entries
from git_branch_keeper.ui.screens import ConfirmScreen, InfoScreen, TabbedInfoScreen
from git_branch_keeper.ui.widgets import NonExpandingHeader
from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.core import BranchKeeper

logger = get_logger(__name__)


class BranchKeeperApp(App):
    """Interactive TUI for git-branch-keeper."""

    ENABLE_COMMAND_PALETTE = True
    TITLE = "Git Branch Keeper"
    SUB_TITLE = ""  # Will be set dynamically to repo name

    STATUS_MESSAGE_TIMEOUT = 4.0
    STATUS_MESSAGE_PREFIXES: ClassVar[dict[str, str]] = {
        "information": "ℹ",
        "success": "✓",
        "warning": "⚠",
        "error": "✖",
    }
    STATUS_MESSAGE_STYLES: ClassVar[dict[str, str]] = {
        "information": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
    }

    CSS = """
    Screen {
        background: $surface;
    }

    DataTable {
        height: 1fr;
    }

    #status-bar {
        dock: bottom;
        height: 5;
        background: $panel;
        padding: 1;
    }

    .status-row {
        height: 1;
        overflow: hidden;
    }

    .deletable {
        background: $error 20%;
    }

    .protected {
        background: $accent 20%;
    }

    .marked {
        text-style: bold;
    }

    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("space", "toggle_mark", "Mark/Unmark"),
        Binding("f", "force_mark", "Force Mark"),
        Binding("a", "mark_all_deletable", "Mark All Deletable"),
        Binding("c", "clear_marks", "Clear Marks"),
        Binding("i", "show_info", "Show Info"),
        Binding("l", "show_legend", "Legend"),
        Binding("s", "cycle_sort", "Change Sort"),
        Binding("r", "refresh", "Refresh"),
        Binding("d", "toggle_delete_scope", "Delete Scope"),
        Binding("u", "undo_recent_deletion", "Undo Delete"),
    ]

    def __init__(
        self,
        keeper: BranchKeeper,
        branches: list[BranchDetails] | None = None,
        cleanup_mode: bool = False,
    ):
        super().__init__()
        self.keeper = keeper
        self.branches = branches or []
        self.analysis: BranchAnalysisResult | None = None
        self.marked_branches: set[str] = set()  # Normal marked branches
        self.force_marked_branches: set[str] = set()  # Force-marked branches
        self.sort_column = "age"
        self.sort_reverse = False  # Newest first by default
        self.cleanup_mode = cleanup_mode
        self.is_refreshing = False
        self.operation_label = "Refreshing"
        self.analysis_progress: BranchAnalysisProgress | None = None
        self.status_message: str | None = None
        self.status_message_severity = "information"
        self._status_message_timer: Timer | None = None

        # Set subtitle to show repository name (version displays on right via clock)
        repo_path = self.keeper.repo.working_dir
        repo_name = os.path.basename(repo_path) if repo_path else "unknown"
        self.sub_title = repo_name

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield NonExpandingHeader(show_clock=True, icon="")
        yield DataTable(id="branch-table", cursor_type="row", zebra_stripes=True)
        yield Container(
            Static(id="status-scope", classes="status-row"),
            Static(id="status-summary", classes="status-row"),
            Static(id="status-dynamic", classes="status-row"),
            id="status-bar",
        )
        yield Footer()

    def _auto_mark_deletable(self, deletable_branches: list[BranchDetails] | None = None) -> None:
        """Auto-mark deletable branches when cleanup mode is active."""
        if not self.cleanup_mode:
            return

        branches = deletable_branches
        if branches is None:
            branches = self._plan_deletable()

        for branch in branches:
            self.marked_branches.add(branch.name)
            self.force_marked_branches.discard(branch.name)

    def _currently_deletable_names(self) -> set[str]:
        """Names the last applied analysis considered cleanup candidates."""
        if self.analysis is not None:
            return {branch.name for branch in self.analysis.deletable_branches}
        return {branch.name for branch in self._plan_deletable()}

    def _apply_analysis_result(
        self,
        analysis: BranchAnalysisResult,
        preserve_marks: bool = False,
        preserve_view: bool = False,
        mark_newly_deletable: bool = False,
    ) -> None:
        """Apply shared branch analysis output to the TUI widgets.

        `preserve_marks` keeps whatever the user has marked (and, just as
        importantly, deliberately unmarked). `mark_newly_deletable` is for the
        startup path, where rows are painted from cache and then replaced by a
        background analysis: a branch that only becomes a cleanup candidate in
        that second pass was never offered to the user, so it still needs its
        auto-mark. Without it a branch merged since the previous run shows up as
        `merged` but stays unmarked until the next launch.
        """
        table_view = self._capture_table_view() if preserve_view else None
        previously_deletable = (
            self._currently_deletable_names() if preserve_marks and mark_newly_deletable else set()
        )

        self.analysis = analysis
        self.branches = analysis.branches

        if preserve_marks:
            existing_names = {branch.name for branch in self.branches}
            self.marked_branches = {name for name in self.marked_branches if name in existing_names}
            self.force_marked_branches = {
                name for name in self.force_marked_branches if name in existing_names
            }
        else:
            self.marked_branches.clear()
            self.force_marked_branches.clear()

        if not preserve_marks:
            self._auto_mark_deletable(analysis.deletable_branches)
        elif mark_newly_deletable:
            self._auto_mark_deletable(
                [
                    branch
                    for branch in analysis.deletable_branches
                    if branch.name not in previously_deletable
                ]
            )
        self._populate_table()
        if table_view is not None:
            self._restore_table_view(table_view)
        self._update_status()

    def _capture_table_view(self):
        """Capture enough table state to avoid visible jumps after refresh."""
        table = self.query_one(DataTable)
        cursor_row = table.cursor_row
        current_row_key = None

        if cursor_row is not None and cursor_row < len(self.branches):
            current_row_key = self._row_key_for_branch(self.branches[cursor_row])

        return current_row_key, cursor_row, table.scroll_x, table.scroll_y

    def _restore_table_view(self, table_view) -> None:
        """Restore cursor and scroll position after rebuilding table rows."""
        current_row_key, fallback_row, scroll_x, scroll_y = table_view
        table = self.query_one(DataTable)

        cursor_row = None
        if current_row_key is not None:
            for index, branch in enumerate(self.branches):
                if self._row_key_for_branch(branch) == current_row_key:
                    cursor_row = index
                    break

        if cursor_row is None:
            cursor_row = fallback_row

        if cursor_row is not None and table.row_count:
            table.cursor_coordinate = Coordinate(min(cursor_row, table.row_count - 1), 0)

        table.scroll_to(
            x=scroll_x,
            y=scroll_y,
            animate=False,
            force=True,
            immediate=True,
        )

    def _cancel_status_message_timer(self) -> None:
        """Cancel any pending status-message clear timer."""
        if self._status_message_timer is not None:
            self._status_message_timer.stop()
            self._status_message_timer = None

    def _clear_status_message(self, expected_message: str | None = None) -> None:
        """Clear the transient status-bar message."""
        if expected_message is not None and self.status_message != expected_message:
            return

        self._cancel_status_message_timer()
        self.status_message = None
        self.status_message_severity = "information"
        self._update_status()

    def _set_status_message(
        self,
        message: str | None,
        severity: str = "information",
        timeout: float | None = STATUS_MESSAGE_TIMEOUT,
    ) -> None:
        """Show short feedback in the status bar instead of a toast."""
        self._cancel_status_message_timer()
        self.status_message = message
        self.status_message_severity = severity
        self._update_status()

        if message and timeout:
            self._status_message_timer = self.set_timer(
                timeout,
                lambda: self._clear_status_message(message),
            )

    def _set_analysis_progress(self, progress: BranchAnalysisProgress) -> None:
        """Update TUI-native branch-analysis progress."""
        self.analysis_progress = progress
        self._update_status()

    def _analysis_progress_callback(self, progress: BranchAnalysisProgress) -> None:
        """Thread-safe progress callback passed into the shared analyzer."""
        try:
            self.call_from_thread(self._set_analysis_progress, progress)
        except RuntimeError:
            logger.debug("Unable to update TUI analysis progress", exc_info=True)

    def _format_operation_progress(self) -> str:
        """Format the current operation phase/count for the status bar."""
        progress = self.analysis_progress
        if progress is None:
            return f"⟳ {self.operation_label}: Starting… — actions paused"

        label = progress.message or progress.phase
        if progress.total is None:
            detail = f"{label}…"
        elif progress.total <= 0:
            detail = f"{label} (100%)"
        else:
            current = max(0, min(progress.current, progress.total))
            detail = f"{label} {current}/{progress.total} ({progress.percent}%)"

        suffix = (
            "navigation OK; actions paused"
            if self.operation_label == "Refreshing"
            else "actions paused"
        )
        return f"⟳ {self.operation_label}: {detail} — {suffix}"

    def _set_refreshing(
        self,
        refreshing: bool,
        show_initial_loader: bool = False,
        operation_label: str = "Refreshing",
    ) -> None:
        """Update visible refresh state for the TUI.

        The full-table loader is reserved for cold/forced startup loads where
        there are no rows to keep on screen. In-place refreshes keep the table
        visible and report progress only in the status bar.
        """
        self.is_refreshing = refreshing
        self.operation_label = operation_label
        table = self.query_one(DataTable)

        if refreshing:
            self._set_status_message(None)
            table.loading = show_initial_loader
            if self.analysis_progress is None:
                self.analysis_progress = BranchAnalysisProgress(
                    phase="Starting",
                    message="Starting",
                )
        else:
            table.loading = False
            self.analysis_progress = None
            self.operation_label = "Refreshing"
        self._update_status()

    def _block_if_refreshing(self, action_name: str) -> bool:
        """Prevent branch-changing actions while refresh is reconciling data."""
        if not self.is_refreshing:
            return False

        self._set_status_message(
            f"{self.operation_label} in progress — {action_name} paused; navigation is OK",
            severity="warning",
        )
        return True

    def on_mount(self) -> None:
        """Set up the table when app starts."""
        table = self.query_one(DataTable)

        # Add columns (width=None enables Textual's auto-width based on content)
        # Add Mark column first (TUI-specific for interactive selection)
        table.add_column(Text(SYMBOL_UNMARKED, justify="center"), width=None, key="mark")

        # Add unified columns from COLUMNS constant
        for col in COLUMNS:
            # Center-justify Location and Changes columns for better visual alignment
            if col.key in ["location", "changes"]:
                table.add_column(Text(col.label, justify="center"), width=None, key=col.key)
            else:
                table.add_column(col.label, width=None, key=col.key)

        # If no branches loaded yet, use the shared analysis path. Show any
        # cached rows immediately, then refresh stale/unstable rows in the
        # background. Since the main branch is intentionally refreshed on every
        # run, a cache can be useful even when the snapshot is not complete.
        if not self.branches:
            cached_analysis = self.keeper.get_cached_analysis_fast(
                finalize_partial=True, include_refresh_candidates=True
            )

            if cached_analysis.branches:
                self._apply_analysis_result(cached_analysis)

            if not cached_analysis.is_complete:
                self._set_refreshing(
                    True,
                    show_initial_loader=not bool(cached_analysis.branches),
                )
                self.load_initial_data()  # @work decorator handles Worker creation
            elif not cached_analysis.branches:
                self._set_status_message("No branches found", severity="warning")
        else:
            # Initial population for tests/callers that inject rows directly.
            self.branches = self.keeper.sort_branches(self.branches)
            self._auto_mark_deletable()
            self._populate_table()
            self._update_status()

    @staticmethod
    def _row_key_for_branch(branch: BranchDetails) -> str:
        """Return the stable DataTable row key for a branch/worktree entry."""
        return f"{branch.name}:{branch.worktree_path}" if branch.is_worktree else branch.name

    def _current_branch_name(self) -> str | None:
        """Return the current branch name, or None for detached HEAD/unavailable repos."""
        try:
            return self.keeper.repo.active_branch.name
        except (TypeError, AttributeError):
            return None

    def _format_branch_row(
        self,
        branch: BranchDetails,
        current_branch_name: str | None,
        github_base_url: str | None,
    ):
        """Build DataTable cell values for a branch row."""
        is_marked = branch.name in self.marked_branches
        is_force_marked = branch.name in self.force_marked_branches

        # Determine text color using unified styling logic
        style_type = get_branch_style_type(branch, self.keeper.protected_branches)
        # Override color for force-marked branches to show they will be deleted
        if is_force_marked:
            text_color = TUI_COLORS[BranchStyleType.DELETABLE]  # Use red color
        else:
            text_color = TUI_COLORS.get(style_type, TUI_COLORS["active"])

        logger.debug(
            f"[TUI DISPLAY] {branch.name}: status={branch.status.value}, in_worktree={branch.in_worktree}, style_type={style_type}, color={text_color}"
        )

        # Mark column - show different symbol for force-marked
        if is_force_marked:
            mark = Text("✓!", justify="center", style="bold red")
        elif is_marked:
            mark = Text(SYMBOL_MARKED, justify="center")
        else:
            mark = Text(SYMBOL_UNMARKED, justify="center")

        # Format branch name with color and indent
        is_current = branch.name == current_branch_name if current_branch_name else False
        formatted_name = format_branch_name_with_indent(branch.name, branch.is_worktree, is_current)
        branch_text = Text(formatted_name, style=text_color)

        # Format cleanup-focused status using shared formatter
        status_str = format_display_status(branch, self.keeper.protected_branches)
        status_text = Text(status_str, style=text_color)

        # Format last commit date using shared formatter
        last_commit = format_date(branch.last_commit_date)

        # Format age using shared formatter
        age_display = format_age(branch.age_days)

        # Changes column - using shared formatter
        changes_indicator = format_changes(branch, current_branch_name)
        changes = Text(changes_indicator, justify="center")

        # Location column - plain text distinguishes local, paired, and remote-only refs
        location_label = format_remote_status(branch.has_remote, branch.has_local)
        location = Text(location_label, justify="center")

        # PR column - using shared formatter
        pr_display = format_pr_link(branch.pr_status, github_base_url)

        # Match COLUMNS order: Branch, Status, Last Commit, Age, Changes, Sync,
        # Location, PRs, Notes
        # (Plus Mark column at the beginning)
        return (
            mark,
            branch_text,
            status_text,
            last_commit,
            age_display,
            changes,
            branch.sync_status or "",
            location,
            pr_display,
            branch.notes or "",
        )

    def _populate_table(self) -> None:
        """Add branch data to table."""
        table = self.query_one(DataTable)
        table.clear()

        # Get current branch name and GitHub URL once for all rows
        current_branch_name = self._current_branch_name()
        github_base_url = self.keeper._get_github_base_url()

        for branch in self.branches:
            row_key = self._row_key_for_branch(branch)
            table.add_row(
                *self._format_branch_row(branch, current_branch_name, github_base_url),
                key=row_key,
            )

    def _refresh_branch_rows(self, branch_names: set[str] | None = None) -> None:
        """Refresh existing table rows without clearing the table.

        Clearing and rebuilding the DataTable resets its scroll offset. Mark/unmark
        actions only change row presentation, so update cells in place to keep long
        lists from jumping while the user works through them.
        """
        table = self.query_one(DataTable)

        # If the table shape no longer matches the model, fall back to a full rebuild.
        if table.row_count != len(self.branches):
            self._populate_table()
            return

        current_branch_name = self._current_branch_name()
        github_base_url = self.keeper._get_github_base_url()
        column_keys = ["mark"] + [col.key for col in COLUMNS]

        for branch in self.branches:
            if branch_names is not None and branch.name not in branch_names:
                continue

            row_key = self._row_key_for_branch(branch)
            for column_key, value in zip(
                column_keys,
                self._format_branch_row(branch, current_branch_name, github_base_url),
            ):
                # The force-mark indicator is wider ("✓!"), so allow only that
                # compact mark column to resize; other cells keep existing widths.
                table.update_cell(row_key, column_key, value, update_width=column_key == "mark")

    def _mark_with_hierarchy(
        self, branch_name: str, mark_set: set[str], is_force: bool = False
    ) -> tuple[bool, str | None]:
        """Mark a branch and all related items (parent + worktrees) if validation passes.

        Args:
            branch_name: Name of the branch to mark
            mark_set: The set to add marks to (marked_branches or force_marked_branches)
            is_force: If True, skip uncommitted changes validation

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        # Find all branches/worktrees with this name
        matching = [b for b in self.branches if b.name == branch_name]

        if not matching:
            return False, "Branch not found"

        # Validate ALL related items before marking any
        issues = []
        for branch in matching:
            if (
                not branch.is_worktree
                and not branch.has_local
                and not (
                    branch.has_remote and branch.status == BranchStatus.MERGED
                )
            ):
                logger.debug(f"[MARK_WITH_HIERARCHY] {branch.name} is remote-only")
                return False, "Cannot mark a remote-only branch unless it is merged"

            # Check protected branches (always enforced)
            if BranchValidationService.is_protected(branch.name, self.keeper.protected_branches):
                logger.debug(f"[MARK_WITH_HIERARCHY] {branch.name} is protected")
                return False, "Cannot mark protected branch"

            # Check uncommitted changes (unless force mode)
            if not is_force:
                has_uncommitted = (
                    branch.modified_files is True
                    or branch.untracked_files is True
                    or branch.staged_files is True
                )
                if has_uncommitted:
                    if branch.is_worktree:
                        issues.append("worktree has uncommitted changes")
                    else:
                        issues.append("branch has uncommitted changes")

        # If any validation issues, return error
        if issues:
            error = "This branch's " + " and ".join(issues) + " (press 'f' to force-mark)"
            logger.debug(f"[MARK_WITH_HIERARCHY] Validation failed: {error}")
            return False, error

        # All validations passed - mark all related items
        for branch in matching:
            mark_set.add(branch.name)

        return True, None

    def _unmark_with_hierarchy(self, branch_name: str) -> None:
        """Unmark a branch and all related items from both sets.

        Args:
            branch_name: Name of the branch to unmark
        """
        self.marked_branches.discard(branch_name)
        self.force_marked_branches.discard(branch_name)

    def _update_status(self) -> None:
        """Update status bar with current stats."""
        scope_status = self.query_one("#status-scope", Static)
        summary_status = self.query_one("#status-summary", Static)
        dynamic_status = self.query_one("#status-dynamic", Static)

        # Total counts table rows; every other figure counts branch names, because
        # that is what a mark applies to. A branch and its worktree are two rows
        # but one name, one mark, and one cleanup decision.
        total = len(self.branches)
        marked = len(self.marked_branches)
        deletable_names = {branch.name for branch in self._plan_deletable()}
        deletable = len(deletable_names)
        protected = len(
            {
                branch.name
                for branch in self.branches
                if BranchValidationService.is_protected(branch.name, self.keeper.protected_branches)
            }
        )

        # Merged/stale branches that are visible but can never be marked. Without
        # this, `Deletable: 0` alongside a screen of `merged` rows looks like a bug.
        # The per-branch reason lives in the detail pane's deletion blockers.
        # Subtract the plan: it unblocks branches whose only obstacle is a clean
        # worktree, and the two figures must not contradict each other.
        blocked = len(
            {
                branch.name
                for branch in self.branches
                if BranchValidationService.blocking_reason(
                    branch, self.keeper.protected_branches, self._current_branch_name()
                )
            }
            - deletable_names
        )

        sort_order = "desc" if self.sort_reverse else "asc"
        force_marked = len(self.force_marked_branches)

        scope_text = Text(no_wrap=True, overflow="ellipsis")
        if self.keeper.delete_remote:
            scope_text.append("Delete scope: LOCAL + REMOTE [d]", style="bold yellow")
        else:
            scope_text.append(
                "Delete scope: LOCAL + MERGED REMOTE-ONLY [d]",
                style="bold green",
            )

        summary_text = Text(no_wrap=True, overflow="ellipsis")
        summary_text.append(
            f"Total: {total} | "
            f"Protected: {protected} | "
            f"Deletable: {deletable} | "
            + (f"Blocked: {blocked} | " if blocked else "")
            + f"Marked: {marked} | "
            f"Force: {force_marked} | "
            f"Sort: {self.sort_column} ({sort_order})"
        )

        dynamic_text = Text(no_wrap=True, overflow="ellipsis")
        if self.status_message:
            severity = self.status_message_severity
            prefix = self.STATUS_MESSAGE_PREFIXES.get(severity, "ℹ")
            style = self.STATUS_MESSAGE_STYLES.get(severity, "cyan")
            dynamic_text.append(f"{prefix} {self.status_message}", style=style)
        elif self.is_refreshing:
            dynamic_text.append(self._format_operation_progress(), style="cyan")

        scope_status.update(scope_text)
        summary_status.update(summary_text)
        dynamic_status.update(dynamic_text)

    def action_toggle_delete_scope(self) -> None:
        """Toggle whether deleting a local branch also deletes its remote branch."""
        if self._block_if_refreshing("changing deletion scope"):
            return

        self.keeper.delete_remote = not self.keeper.delete_remote
        if self.keeper.delete_remote:
            self._set_status_message(
                f"Remote deletion enabled for {self.keeper.remote_name} — "
                "undo restores local branches only",
                severity="warning",
            )
        else:
            self._set_status_message(
                "Paired remote deletion disabled — merged remote-only cleanup is unchanged"
            )

    def action_toggle_mark(self) -> None:
        """Toggle mark on current row."""
        if self._block_if_refreshing("marking"):
            return

        table = self.query_one(DataTable)

        if table.cursor_row is None:
            return

        # Get branch name from cursor position
        row_index = table.cursor_row
        if row_index >= len(self.branches):
            return

        # Find the branch at this visual position
        # Since we sort branches, we need to get the actual branch
        branch = self.branches[row_index]

        # Toggle mark (with hierarchy - marks parent + worktrees together)
        if branch.name in self.marked_branches or branch.name in self.force_marked_branches:
            # Unmark from both sets
            self._unmark_with_hierarchy(branch.name)
        else:
            # Try to mark in normal set (validates all related items)
            success, error = self._mark_with_hierarchy(
                branch.name, self.marked_branches, is_force=False
            )

            if not success:
                if error:
                    self._set_status_message(error, severity="warning")
                return

            # Remove from force-marked if it was there
            self.force_marked_branches.discard(branch.name)

        # Save cursor position before refreshing the row display
        saved_row = table.cursor_row

        self._refresh_branch_rows({branch.name})
        self._update_status()

        # Restore cursor and move down one row
        if saved_row is not None and saved_row < len(self.branches):
            new_row = min(saved_row + 1, len(self.branches) - 1)
            table.cursor_coordinate = Coordinate(new_row, 0)

    def _plan_deletable(self, force_mode: bool = False) -> list[BranchDetails]:
        """The branches "mark all" would mark, via the keeper's shared planning.

        Never filter rows with `is_deletable` directly for this: the planner also
        drops the checked-out branch and worktree rows, and adds branches that only
        become deletable once a clean worktree is removed in the same operation.
        Counting rows instead advertises candidates that cannot be marked - notably
        the current branch, which is a merged feature branch whenever GBK runs from
        a linked worktree.
        """
        removable_worktrees = self.keeper.get_removable_worktrees(self.branches)
        deletable_branches = self.keeper.get_deletable_branches(
            self.branches, force_mode=force_mode
        )
        deletable_branches.extend(
            self.keeper.get_branches_unblocked_by_worktree_removal(
                self.branches,
                branches_to_delete=deletable_branches,
                worktrees_to_remove=removable_worktrees,
                force_mode=force_mode,
            )
        )
        return deletable_branches

    def action_mark_all_deletable(self) -> None:
        """Mark all deletable branches (normal mode only)."""
        if self._block_if_refreshing("mark all"):
            return

        for branch in self._plan_deletable():
            self.marked_branches.add(branch.name)
            # Remove from force-marked if it was there
            self.force_marked_branches.discard(branch.name)

        self._refresh_branch_rows()
        self._set_status_message(f"Marked {len(self.marked_branches)} deletable branches")

    def action_clear_marks(self) -> None:
        """Clear all marks."""
        if self._block_if_refreshing("clearing marks"):
            return

        count = len(self.marked_branches) + len(self.force_marked_branches)
        self.marked_branches.clear()
        self.force_marked_branches.clear()
        self._refresh_branch_rows()
        self._update_status()
        if count > 0:
            self._set_status_message(f"Cleared {count} marks")

    def action_force_mark(self) -> None:
        """Force-mark current branch (ignores uncommitted changes)."""
        if self._block_if_refreshing("force-marking"):
            return

        table = self.query_one(DataTable)
        if table.cursor_row is None:
            return

        row_index = table.cursor_row
        if row_index >= len(self.branches):
            return

        branch = self.branches[row_index]

        # Check basic force-mark eligibility (status)
        if branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
            self._set_status_message(
                "Can only force-mark stale/merged branches",
                severity="warning",
            )
            return

        # Toggle force-mark (with hierarchy - marks parent + worktrees together)
        if branch.name in self.force_marked_branches or branch.name in self.marked_branches:
            # Unmark from both sets
            self._unmark_with_hierarchy(branch.name)
        else:
            # Try to force-mark (validates protected branches only)
            success, error = self._mark_with_hierarchy(
                branch.name, self.force_marked_branches, is_force=True
            )

            if not success:
                if error:
                    self._set_status_message(error, severity="warning")
                return

            # Remove from normal marks if it was there
            self.marked_branches.discard(branch.name)

        saved_row = table.cursor_row
        self._refresh_branch_rows({branch.name})
        self._update_status()

        # Restore cursor and move down
        if saved_row is not None and saved_row < len(self.branches):
            new_row = min(saved_row + 1, len(self.branches) - 1)
            table.cursor_coordinate = Coordinate(new_row, 0)

    def action_delete_marked(self) -> None:
        """Delete all marked branches."""
        if self._block_if_refreshing("deletion"):
            return

        total_marked = len(self.marked_branches) + len(self.force_marked_branches)
        logger.debug(
            f"[DELETE_MARKED] Called: marked={self.marked_branches}, "
            f"force_marked={self.force_marked_branches}, total={total_marked}"
        )

        if total_marked == 0:
            self._set_status_message("No branches marked for deletion", severity="warning")
            return

        # Look up full BranchDetails objects for marked branches (both normal and force)
        all_marked_names = self.marked_branches | self.force_marked_branches
        branches_to_delete = [branch for branch in self.branches if branch.name in all_marked_names]

        # Build confirmation message
        force_count = len(self.force_marked_branches)
        normal_count = len(self.marked_branches)
        branches_list = format_deletion_confirmation_items(
            branches_to_delete, self.keeper.delete_remote
        )
        remote_only_count = len(
            {
                branch.name
                for branch in branches_to_delete
                if branch.has_remote and not branch.has_local
            }
        )
        remote_only_message = (
            f"\n{remote_only_count} merged remote-only branch"
            f"{'es' if remote_only_count != 1 else ''} on {self.keeper.remote_name} "
            "will be deleted. "
            "These have no local ref, so the delete-scope toggle does not apply."
            if remote_only_count
            else ""
        )

        if self.keeper.delete_remote:
            scope_message = (
                "Deletion scope: LOCAL + REMOTE\n"
                f"Matching branches on {self.keeper.remote_name} will also be deleted. "
                "Undo restores local branches only; deleted remote branches must be "
                f"pushed back manually.{remote_only_message}\n"
                "Cancel and press d to keep remote branches instead."
            )
        else:
            scope_message = (
                "Deletion scope: LOCAL ONLY\n"
                f"Matching remotes for local branches on {self.keeper.remote_name} "
                "will be kept and may "
                f"remain visible as remote-only rows.{remote_only_message}\n"
                "Cancel and press d to delete local and remote branches together."
            )

        if force_count > 0:
            message = (
                f"Delete {total_marked} marked branch{'es' if total_marked > 1 else ''}?\n"
                f"({normal_count} normal, {force_count} force-marked)\n\n"
                f"{scope_message}\n\n"
                f"{branches_list}"
            )
        else:
            message = (
                f"Delete {total_marked} marked branch{'es' if total_marked > 1 else ''}?"
                f"\n\n{scope_message}\n\n{branches_list}"
            )

        # Show confirmation screen
        self.push_screen(
            ConfirmScreen(
                message,
                dialog_title="Confirm deletion",
                confirm_label="Delete",
            ),
            self._handle_delete_confirmation,
        )

    def _handle_delete_confirmation(self, confirmed: bool | None) -> None:
        """Handle delete confirmation result."""
        if not confirmed:
            self._set_status_message("Deletion cancelled")
            return

        # Separate into normal and force-marked branches
        def process_marked_branches(marked_set, is_force):
            branches = []
            worktrees = []

            for branch_name in marked_set:
                matching = [b for b in self.branches if b.name == branch_name]

                for branch in matching:
                    if branch.is_worktree:
                        worktrees.append(branch)
                    elif (
                        branch.worktree_is_orphaned
                        or is_force
                        or BranchValidationService.is_deletable(
                            branch, self.keeper.protected_branches
                        )
                    ):
                        branches.append(branch)

            branches.extend(
                self.keeper.get_branches_unblocked_by_worktree_removal(
                    self.branches,
                    branches_to_delete=branches,
                    worktrees_to_remove=worktrees,
                    force_mode=is_force,
                )
            )

            return branches, worktrees

        # Process force-marked branches first
        force_branches, force_worktrees = process_marked_branches(
            self.force_marked_branches, is_force=True
        )

        # Process normal-marked branches
        normal_branches, normal_worktrees = process_marked_branches(
            self.marked_branches, is_force=False
        )

        deletion_batch_id = self.keeper.git_service.deletion_journal.new_batch_id()
        self._set_refreshing(True, operation_label="Deleting")
        self.delete_marked_items(
            force_branches,
            force_worktrees,
            normal_branches,
            normal_worktrees,
            deletion_batch_id,
        )

    @work(exclusive=True, thread=False)
    async def delete_marked_items(
        self,
        force_branches: list,
        force_worktrees: list,
        normal_branches: list,
        normal_worktrees: list,
        deletion_batch_id: str,
    ) -> None:
        """Delete marked branches/worktrees in the background with progress."""
        all_deleted_branches = []
        all_failed_branches = []
        all_removed_worktrees = []
        all_failed_worktrees = []

        try:
            # Delete force-marked items with force mode
            if force_branches or force_worktrees:
                deleted, failed_b, removed, failed_w = await asyncio.to_thread(
                    self.keeper.perform_deletion,
                    force_branches,
                    force_worktrees,
                    force_mode=True,
                    batch_id=deletion_batch_id,
                    progress_callback=self._analysis_progress_callback,
                )
                all_deleted_branches.extend(deleted)
                all_failed_branches.extend(failed_b)
                all_removed_worktrees.extend(removed)
                all_failed_worktrees.extend(failed_w)

            # Delete normal-marked items without force
            if normal_branches or normal_worktrees:
                deleted, failed_b, removed, failed_w = await asyncio.to_thread(
                    self.keeper.perform_deletion,
                    normal_branches,
                    normal_worktrees,
                    force_mode=False,
                    batch_id=deletion_batch_id,
                    progress_callback=self._analysis_progress_callback,
                )
                all_deleted_branches.extend(deleted)
                all_failed_branches.extend(failed_b)
                all_removed_worktrees.extend(removed)
                all_failed_worktrees.extend(failed_w)

            # Remove deleted/removed items from our list
            deleted_names = set(all_deleted_branches)
            removed_paths = set(all_removed_worktrees)

            self.branches = [
                b
                for b in self.branches
                if not (
                    b.name in deleted_names or (b.is_worktree and b.worktree_path in removed_paths)
                )
            ]

            # Clear marks and refresh
            self.marked_branches.clear()
            self.force_marked_branches.clear()
            self._populate_table()
            self._update_status()

            # Show results
            total_success = len(all_deleted_branches) + len(all_removed_worktrees)
            total_failed = len(all_failed_branches) + len(all_failed_worktrees)

            if total_success > 0:
                undo_hint = " (press u to undo)" if all_deleted_branches else ""
                self._set_status_message(
                    (
                        f"Removed {len(all_removed_worktrees)} worktrees and "
                        f"deleted {len(all_deleted_branches)} branches{undo_hint}"
                    ),
                    severity="success",
                )

            if total_failed > 0:
                failed_list = []
                for branch_name, error in all_failed_branches:
                    failed_list.append(f"  • {branch_name}: {error}")
                for wt_path, error in all_failed_worktrees:
                    failed_list.append(f"  • {wt_path}: {error}")

                error_msg = (
                    f"Failed to delete {total_failed} item{'s' if total_failed > 1 else ''}:\n\n"
                    + "\n".join(failed_list)
                )
                self.push_screen(InfoScreen(error_msg))

        except Exception as e:  # noqa: BLE001 - background worker must fail closed
            # Deliberately broad: this is a @work-decorated worker; an unexpected
            # failure must surface as an InfoScreen, not crash the whole TUI.
            error_msg = f"Error during deletion:\n\n{e!s}"
            self.push_screen(InfoScreen(error_msg))
        finally:
            self._set_refreshing(False)

    def _repo_path(self) -> str:
        """Return the working repository path for undo/restore operations."""
        return str(self.keeper.repo.working_dir or self.keeper.repo_path)

    def action_undo_recent_deletion(self) -> None:
        """Restore the most recent deleted branch recorded in the journal."""
        if self._block_if_refreshing("undo"):
            return

        repo_path = self._repo_path()
        journal = self.keeper.git_service.deletion_journal
        deletions = journal.deletions()

        if not deletions:
            self._set_status_message(
                "No recorded deletions for this repository",
                severity="warning",
            )
            return

        try:
            repo = git.Repo(repo_path)
        except GIT_ERRORS as e:
            self.push_screen(InfoScreen(f"Could not open repository:\n\n{e}"))
            return

        entries = pick_latest_batch(deletions, repo)
        if not entries:
            self._set_status_message("No deleted branches to restore", severity="warning")
            return

        if len(entries) == 1:
            entry = entries[0]
            message = (
                f"Restore branch {entry['branch']} at {entry['sha'][:12]}?\n"
                f"Deleted: {entry.get('timestamp', 'unknown time')}\n\n"
                "This restores the local branch only."
            )
        else:
            branch_list = "\n".join(
                f"  • {entry['branch']} at {entry['sha'][:12]}" for entry in entries
            )
            message = (
                f"Restore {len(entries)} branches from the last deletion batch?\n\n"
                f"{branch_list}\n\n"
                "This restores local branches only."
            )

        remote_deleted_entries = [entry for entry in entries if entry.get("remote_deleted")]
        if remote_deleted_entries:
            message += "\n\nOne or more remote branches were also deleted; the TUI will not push them back."

        self.push_screen(
            ConfirmScreen(
                message,
                dialog_title="Restore branches",
                confirm_label="Restore",
                danger=False,
            ),
            lambda confirmed: self._handle_undo_confirmation(confirmed, entries),
        )

    def _handle_undo_confirmation(self, confirmed: bool | None, entries: list[dict]) -> None:
        """Handle confirmation for restoring a deleted branch batch."""
        if not confirmed:
            self._set_status_message("Restore cancelled")
            return

        journal = self.keeper.git_service.deletion_journal
        restored, failed = restore_entries(
            self._repo_path(), entries, journal, include_remote=False
        )

        if failed:
            failed_list = "\n".join(f"  • {branch}: {error}" for branch, error in failed)
            self.push_screen(InfoScreen(f"Could not restore all branches:\n\n{failed_list}"))

        if restored:
            self._set_status_message(
                f"Restored {len(restored)} branch{'es' if len(restored) != 1 else ''}",
                severity="success",
            )

        remote_deleted_entries = [entry for entry in entries if entry.get("remote_deleted")]
        if remote_deleted_entries:
            commands = "\n".join(
                f"git push {entry.get('remote', 'origin')} {entry['sha']}:refs/heads/{entry['branch']}"
                for entry in remote_deleted_entries
            )
            self.push_screen(
                InfoScreen(
                    f"Remote branches were deleted too. Restore them manually with:\n\n{commands}"
                )
            )

        if restored:
            # Re-analyze so restored branches appear in the table with fresh status/details.
            self.refresh_data()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle keyboard selection in DataTable (Enter deletes marked branches).

        Textual's DataTable also emits RowSelected for a mouse click on the already
        highlighted row. In this app mouse clicks should only move/highlight the cursor;
        deletion is intentionally keyboard-driven via Enter.

        Using this event handler rather than an app-level Enter binding also keeps Enter
        available to the DataTable itself; modal screens are already isolated from app
        bindings by Textual's modal binding chain.
        """
        if getattr(event.data_table, "_show_hover_cursor", False):
            logger.debug("Ignoring DataTable row selection from mouse click")
            return

        self.action_delete_marked()

    def action_show_info(self) -> None:
        """Show detailed info for selected branch with tabbed interface."""
        table = self.query_one(DataTable)
        if table.cursor_row is None:
            return

        row_index = table.cursor_row
        if row_index >= len(self.branches):
            return

        branch = self.branches[row_index]

        # Get main branch from keeper
        main_branch = self.keeper.main_branch

        # Show the new tabbed info screen
        self.push_screen(TabbedInfoScreen(branch, self.keeper, main_branch))

    def action_show_legend(self) -> None:
        """Show legend explaining symbols and colors."""
        self.push_screen(InfoScreen(LEGEND_TEXT))

    def action_cycle_sort(self) -> None:
        """Cycle through sort options."""
        if self._block_if_refreshing("sorting"):
            return

        sort_options = ["age", "branch", "status"]
        try:
            current_idx = sort_options.index(self.sort_column)
            next_idx = (current_idx + 1) % len(sort_options)
            self.sort_column = sort_options[next_idx]

            # Toggle reverse for same column
            if next_idx == 0:  # Back to age
                self.sort_reverse = not self.sort_reverse
        except ValueError:
            self.sort_column = "age"
            self.sort_reverse = True

        # Update config with TUI sort settings (map "branch" to "name" for consistency with CLI)
        sort_by_mapping = {"age": "age", "branch": "name", "status": "status"}
        self.keeper.config.sort_by = sort_by_mapping[self.sort_column]
        self.keeper.config.sort_order = "desc" if self.sort_reverse else "asc"

        # Sort using keeper's unified sorting logic and refresh
        self.branches = self.keeper.sort_branches(self.branches)
        self._populate_table()
        self._update_status()

        sort_name = {
            "age": "Age",
            "branch": "Branch Name",
            "status": "Status",
        }[self.sort_column]
        self._set_status_message(
            f"Sorted by {sort_name} ({'desc' if self.sort_reverse else 'asc'})"
        )

    @work(exclusive=True, thread=False)
    async def load_initial_data(self) -> None:
        """Load branch data on initial TUI startup (runs in background).

        Keeps refresh feedback in the status bar with shared analyzer progress.
        Uses the full-table loading screen only when there are no rows yet.
        """
        try:
            self._set_refreshing(True, show_initial_loader=not bool(self.branches))

            # Use the shared analyzer with Rich progress disabled for TUI.
            # keeper methods are sync, so run them off the event loop.
            analysis = await asyncio.to_thread(
                self.keeper.analyze_branches,
                show_progress=False,
                progress_callback=self._analysis_progress_callback,
            )

            if analysis.branches:
                preserve_existing_rows = bool(self.branches)
                self._apply_analysis_result(
                    analysis,
                    preserve_marks=preserve_existing_rows,
                    preserve_view=preserve_existing_rows,
                    mark_newly_deletable=True,
                )
            else:
                self._set_status_message("No branches found", severity="warning")

        except Exception as e:
            logger.exception("Error loading branches")
            error_msg = f"Error loading branches:\n\n{e!s}\n\nCheck the logs for more details."
            self.push_screen(InfoScreen(error_msg))
        finally:
            self._set_refreshing(False)

    @work(exclusive=True, thread=False)
    async def load_additional_data(
        self, cached_branches: list[BranchDetails] | None, branches_to_process: list[str]
    ) -> None:
        """Load branches with cached data as starting point, refresh unstable branches.

        This is called when we have cached data but some branches need refreshing.
        Keeps existing rows visible while the status bar reports refresh progress.

        Args:
            cached_branches: Previously cached branch details (can be None)
            branches_to_process: List of branch names that need processing
        """
        self._set_refreshing(True)

        try:
            logger.debug(
                f"Refreshing cached TUI data via shared analyzer; "
                f"{len(branches_to_process)} branches need processing"
            )
            analysis = await asyncio.to_thread(
                self.keeper.analyze_branches,
                show_progress=False,
                progress_callback=self._analysis_progress_callback,
            )

            if analysis.branches:
                preserve_existing_rows = bool(self.branches)
                self._apply_analysis_result(
                    analysis,
                    preserve_marks=preserve_existing_rows,
                    preserve_view=preserve_existing_rows,
                    mark_newly_deletable=True,
                )
            else:
                self._set_status_message("No branches found", severity="warning")

        except Exception as e:
            logger.exception("Error loading additional branches")
            error_msg = (
                f"Error loading additional branches:\n\n{e!s}\n\nCheck the logs for more details."
            )
            self.push_screen(InfoScreen(error_msg))
        finally:
            self._set_refreshing(False)

    def action_refresh(self) -> None:
        """Trigger refresh of branch data."""
        if self.is_refreshing:
            self._set_status_message("Refresh already in progress")
            return

        self._set_refreshing(True)
        self.refresh_data()  # @work decorator handles Worker creation

    @work(exclusive=True, thread=False)
    async def refresh_data(self) -> None:
        """Refresh branch data by re-analyzing with cache bypass (runs in background).

        Keeps existing rows visible while the status bar reports refresh progress.
        """
        # Show status-bar refresh state. Keep existing rows visible while refreshing.
        self._set_refreshing(True)

        # Store original refresh flag value using safe .get() method
        original_refresh = self.keeper.config.get("refresh", False)

        try:
            # Temporarily enable refresh to bypass cache
            self.keeper.config.refresh = True

            # Re-run the shared analyzer with Rich progress disabled for TUI.
            analysis = await asyncio.to_thread(
                self.keeper.analyze_branches,
                show_progress=False,
                progress_callback=self._analysis_progress_callback,
            )

            if analysis.branches:
                self._apply_analysis_result(
                    analysis,
                    preserve_marks=True,
                    preserve_view=True,
                )

                self._set_status_message("Branch data refreshed", severity="success")
            else:
                self._set_status_message("No branches found", severity="warning")

        except Exception as e:
            logger.exception("Error refreshing")
            error_msg = (
                f"Error refreshing branch data:\n\n{e!s}\n\nCheck the logs for more details."
            )
            self.push_screen(InfoScreen(error_msg))
        finally:
            # Restore original refresh flag
            self.keeper.config.refresh = original_refresh
            self._set_refreshing(False)

    async def action_quit(self) -> None:
        """Override quit action to clean up resources before exiting."""
        try:
            # Cancel all running workers before exit
            self.workers.cancel_all()

            # Close keeper resources (GitHub connections, etc.)
            if self.keeper:
                self.keeper.close()
        except Exception as e:  # noqa: BLE001 - quit must never be blocked by cleanup errors
            # Deliberately broad: this runs on the way out; log and keep exiting
            # rather than delaying or blocking shutdown.
            logger.debug(f"Error during quit cleanup: {e}")
        finally:
            # Call parent's exit method
            self.exit()
