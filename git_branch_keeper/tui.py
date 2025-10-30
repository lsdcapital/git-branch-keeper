"""Interactive TUI for git-branch-keeper using Textual."""
import asyncio
from typing import List, Set, Optional
from textual import work
from textual.app import App, ComposeResult
from textual.events import Click
from textual.widgets import DataTable, Header, Footer, Static
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button
from rich.text import Text

from .__version__ import __version__
from .models.branch import BranchDetails, BranchStatus
from .constants import TUI_COLORS, SYMBOL_MARKED, SYMBOL_UNMARKED, COLUMNS, LEGEND_TEXT
from .formatters import (
    format_date,
    format_remote_status,
    format_status,
    format_age,
    format_changes,
    format_deletion_confirmation_items,
    get_branch_style_type,
)
from .services.branch_validation_service import BranchValidationService
from .logging_config import get_logger

logger = get_logger(__name__)


class ConfirmScreen(ModalScreen[bool]):
    """Modal confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }

    #confirm-dialog {
        width: 80%;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }

    #confirm-message {
        width: 100%;
        height: auto;
        padding: 1 0;
    }

    #button-container {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1 0;
    }

    Button {
        margin: 0 1;
    }
    """

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.message, id="confirm-message")
            with Container(id="button-container"):
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        self.dismiss(event.button.id == "yes")


class InfoScreen(ModalScreen):
    """Modal info display dialog."""

    DEFAULT_CSS = """
    InfoScreen {
        align: center middle;
    }

    #info-dialog {
        width: 80%;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }

    #info-content {
        width: 100%;
        height: auto;
        padding: 1 0;
    }

    #info-button-container {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1 0;
    }
    """

    def __init__(self, info: str):
        super().__init__()
        self.info = info

    def compose(self) -> ComposeResult:
        with Vertical(id="info-dialog"):
            yield Static(self.info, id="info-content")
            with Container(id="info-button-container"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        self.dismiss()


class NonExpandingHeader(Header):
    """Header widget that doesn't expand/contract on click."""

    def on_click(self, event: Click) -> None:
        """Override to disable click-to-expand behavior."""
        event.stop()  # Stop event propagation to prevent default toggle behavior


class BranchKeeperApp(App):
    """Interactive TUI for git-branch-keeper."""

    ENABLE_COMMAND_PALETTE = True
    TITLE = "Git Branch Keeper"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen {
        background: $surface;
    }

    DataTable {
        height: 1fr;
    }

    #status-bar {
        dock: bottom;
        height: auto;
        background: $panel;
        padding: 1;
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

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "delete_marked", "Delete Marked"),
        Binding("space", "toggle_mark", "Mark/Unmark"),
        Binding("a", "mark_all_deletable", "Mark All Deletable"),
        Binding("c", "clear_marks", "Clear Marks"),
        Binding("i", "show_info", "Show Info"),
        Binding("l", "show_legend", "Legend"),
        Binding("s", "cycle_sort", "Change Sort"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, keeper, branches: Optional[List[BranchDetails]] = None, cleanup_mode: bool = False):
        super().__init__()
        self.keeper = keeper
        self.branches = branches or []
        self.marked_branches: Set[str] = set()
        self.sort_column = "age"
        self.sort_reverse = False  # Newest first by default
        self.cleanup_mode = cleanup_mode

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield NonExpandingHeader(show_clock=False, icon="")
        yield DataTable(id="branch-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the table when app starts."""
        table = self.query_one(DataTable)

        # Add columns (width=None enables Textual's auto-width based on content)
        # Add Mark column first (TUI-specific for interactive selection)
        table.add_column(Text(SYMBOL_UNMARKED, justify="center"), width=None, key="mark")

        # Add unified columns from COLUMNS constant
        for col in COLUMNS:
            # Center-justify Remote and Changes columns for better visual alignment
            if col.key in ["remote", "changes"]:
                table.add_column(Text(col.label, justify="center"), width=None, key=col.key)
            else:
                table.add_column(col.label, width=None, key=col.key)

        # If no branches loaded yet, check cache first
        if not self.branches:
            # Try to load cached branches synchronously
            cached_branches, branches_to_process = self.keeper.get_cached_branches_fast()

            if cached_branches and branches_to_process:
                # We have cached data but some branches need refresh
                # Show loading indicator and process everything together
                table.loading = True
                self.load_additional_data(cached_branches, branches_to_process)
            elif cached_branches:
                # All branches are cached and stable - display immediately
                self.branches = cached_branches

                # Update in_worktree status for cached branches
                worktree_branches = self.keeper.git_service.get_worktree_branches()
                try:
                    current_branch = self.keeper.repo.active_branch.name
                except TypeError:
                    current_branch = None

                for branch in self.branches:
                    is_current = (branch.name == current_branch) if current_branch else False
                    if branch.name in worktree_branches and not is_current:
                        branch.in_worktree = True
                    else:
                        branch.in_worktree = False

                self.branches = self.keeper.sort_branches(self.branches)

                # Auto-mark deletable branches if cleanup mode is enabled
                if self.cleanup_mode:
                    for branch in self.branches:
                        if BranchValidationService.is_deletable(branch, self.keeper.protected_branches):
                            self.marked_branches.add(branch.name)

                self._populate_table()
                self._update_status()
            else:
                # No cache, show loading indicator and process everything
                table.loading = True
                self.load_initial_data()  # @work decorator handles Worker creation
        else:
            # Initial population and sort if data already provided
            self.branches = self.keeper.sort_branches(self.branches)

            # Auto-mark deletable branches if cleanup mode is enabled
            if self.cleanup_mode:
                for branch in self.branches:
                    if BranchValidationService.is_deletable(branch, self.keeper.protected_branches):
                        self.marked_branches.add(branch.name)

            self._populate_table()
            self._update_status()

    def _populate_table(self) -> None:
        """Add branch data to table."""
        table = self.query_one(DataTable)
        table.clear()

        for branch in self.branches:
            is_marked = branch.name in self.marked_branches

            # Determine text color using unified styling logic
            style_type = get_branch_style_type(branch, self.keeper.protected_branches)
            text_color = TUI_COLORS.get(style_type, TUI_COLORS["active"])

            logger.debug(f"[TUI DISPLAY] {branch.name}: status={branch.status.value}, in_worktree={branch.in_worktree}, style_type={style_type}, color={text_color}")

            # Mark column - using shared symbols
            mark_symbol = SYMBOL_MARKED if is_marked else SYMBOL_UNMARKED
            mark = Text(mark_symbol, justify="center")

            # Format branch name with color
            branch_text = Text(branch.name, style=text_color)

            # Format status using shared formatter
            status_str = format_status(branch.status)
            status_text = Text(status_str, style=text_color)

            # Format last commit date using shared formatter
            last_commit = format_date(branch.last_commit_date)

            # Format age using shared formatter
            age_display = format_age(branch.age_days)

            # Changes column - using shared formatter
            try:
                current_branch_name = self.keeper.repo.active_branch.name
            except (TypeError, AttributeError):
                current_branch_name = None  # Detached HEAD or repo not available
            changes_indicator = format_changes(branch, current_branch_name)
            changes = Text(changes_indicator, justify="center")

            # Remote column - using shared formatter
            remote_symbol = format_remote_status(branch.has_remote)
            remote = Text(remote_symbol, justify="center")

            # Match COLUMNS order: Branch, Status, Last Commit, Age, Changes, Sync, Remote, PRs, Notes
            # (Plus Mark column at the beginning)
            row_key = branch.name
            table.add_row(
                mark,
                branch_text,
                status_text,
                last_commit,
                age_display,
                changes,
                branch.sync_status or "",
                remote,
                branch.pr_status or "",
                branch.notes or "",
                key=row_key,
            )

    def _update_status(self) -> None:
        """Update status bar with current stats."""
        status = self.query_one("#status-bar", Static)

        total = len(self.branches)
        marked = len(self.marked_branches)
        deletable = sum(1 for b in self.branches if BranchValidationService.is_deletable(b, self.keeper.protected_branches))
        protected = sum(1 for b in self.branches if BranchValidationService.is_protected(b.name, self.keeper.protected_branches))

        sort_order = "desc" if self.sort_reverse else "asc"

        status.update(
            f"Total: {total} | "
            f"Protected: {protected} | "
            f"Deletable: {deletable} | "
            f"Marked: {marked} | "
            f"Sort: {self.sort_column} ({sort_order})"
        )

    def action_toggle_mark(self) -> None:
        """Toggle mark on current row."""
        table = self.query_one(DataTable)
        if table.cursor_row is None:
            return

        # Get branch name from cursor position
        row_index = table.cursor_row
        if row_index >= len(self.branches):
            return

        # Find the branch at this visual position
        # Since we sort branches, we need to get the actual branch
        branch_name = self.branches[row_index].name

        # Don't allow marking protected branches
        if BranchValidationService.is_protected(branch_name, self.keeper.protected_branches):
            self.notify("Cannot mark protected branch", severity="warning")
            return

        if branch_name in self.marked_branches:
            self.marked_branches.remove(branch_name)
        else:
            self.marked_branches.add(branch_name)

        # Save cursor position before repopulating
        saved_row = table.cursor_row

        self._populate_table()
        self._update_status()

        # Restore cursor and move down one row
        if saved_row is not None and saved_row < len(self.branches):
            new_row = min(saved_row + 1, len(self.branches) - 1)
            table.cursor_coordinate = Coordinate(new_row, 0)

    def action_mark_all_deletable(self) -> None:
        """Mark all deletable branches."""
        for branch in self.branches:
            if BranchValidationService.is_deletable(branch, self.keeper.protected_branches):
                self.marked_branches.add(branch.name)

        self._populate_table()
        self._update_status()
        self.notify(f"Marked {len(self.marked_branches)} deletable branches")

    def action_clear_marks(self) -> None:
        """Clear all marks."""
        count = len(self.marked_branches)
        self.marked_branches.clear()
        self._populate_table()
        self._update_status()
        if count > 0:
            self.notify(f"Cleared {count} marks")

    def action_delete_marked(self) -> None:
        """Delete all marked branches."""
        if not self.marked_branches:
            self.notify("No branches marked for deletion", severity="warning")
            return

        # Look up full BranchDetails objects for marked branches
        branches_to_delete = [
            branch for branch in self.branches
            if branch.name in self.marked_branches
        ]

        # Build confirmation message
        count = len(self.marked_branches)
        branches_list = format_deletion_confirmation_items(branches_to_delete)
        message = f"Delete {count} marked branch{'es' if count > 1 else ''}?\n\n{branches_list}"

        # Show confirmation screen
        self.push_screen(ConfirmScreen(message), self._handle_delete_confirmation)

    def _handle_delete_confirmation(self, confirmed: bool | None) -> None:
        """Handle delete confirmation result."""
        if not confirmed:
            self.notify("Deletion cancelled")
            return

        # Delete the branches
        deleted = []
        failed = []

        for branch_name in list(self.marked_branches):
            branch = next((b for b in self.branches if b.name == branch_name), None)
            if not branch:
                continue

            reason = "merged" if branch.status == BranchStatus.MERGED else "stale"

            # Use the keeper's delete_branch method
            try:
                success, error_message = self.keeper.delete_branch(branch_name, reason)
                if success:
                    deleted.append(branch_name)
                    # Remove from our list
                    self.branches = [b for b in self.branches if b.name != branch_name]
                else:
                    failed.append((branch_name, error_message or "Unknown error"))
            except Exception as e:
                error_msg = f"Error deleting {branch_name}:\n\n{str(e)}"
                self.push_screen(InfoScreen(error_msg))
                failed.append((branch_name, str(e)))

        # Clear marks and refresh
        self.marked_branches.clear()
        self._populate_table()
        self._update_status()

        # Show result
        if deleted:
            self.notify(
                f"✓ Deleted {len(deleted)} branch{'es' if len(deleted) > 1 else ''} successfully",
                severity="information",
            )
        if failed:
            failed_list = "\n".join(f"  • {branch}: {reason}" for branch, reason in failed)
            error_msg = f"Failed to delete {len(failed)} branch{'es' if len(failed) > 1 else ''}:\n\n{failed_list}"
            self.push_screen(InfoScreen(error_msg))

    def action_show_info(self) -> None:
        """Show detailed info for selected branch."""
        table = self.query_one(DataTable)
        if table.cursor_row is None:
            return

        row_index = table.cursor_row
        if row_index >= len(self.branches):
            return

        branch = self.branches[row_index]

        # Build change details
        if branch.modified_files is None or branch.untracked_files is None or branch.staged_files is None:
            changes_text = "Unknown (could not check - working directory may be dirty)"
        else:
            change_details = []
            if branch.modified_files:
                change_details.append("Modified files")
            if branch.untracked_files:
                change_details.append("Untracked files")
            if branch.staged_files:
                change_details.append("Staged files")
            changes_text = ", ".join(change_details) if change_details else "Clean"

        # Format detailed info
        info = f"""[bold]Branch:[/bold] {branch.name}
[bold]Status:[/bold] {branch.status.value}
[bold]Age:[/bold] {branch.age_days} days
[bold]Last Commit:[/bold] {branch.last_commit_date}
[bold]Branch State:[/bold] {changes_text}
[bold]Sync:[/bold] {branch.sync_status}
[bold]Remote:[/bold] {"Yes" if branch.has_remote else "No"}
[bold]PRs:[/bold] {branch.pr_status or "None"}
[bold]Notes:[/bold] {branch.notes or "None"}
[bold]Protected:[/bold] {"Yes" if BranchValidationService.is_protected(branch.name, self.keeper.protected_branches) else "No"}
[bold]Deletable:[/bold] {"Yes" if BranchValidationService.is_deletable(branch, self.keeper.protected_branches) else "No"}
        """.strip()

        self.push_screen(InfoScreen(info))

    def action_show_legend(self) -> None:
        """Show legend explaining symbols and colors."""
        self.push_screen(InfoScreen(LEGEND_TEXT))

    def action_cycle_sort(self) -> None:
        """Cycle through sort options."""
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
        self.keeper.config['sort_by'] = sort_by_mapping[self.sort_column]
        self.keeper.config['sort_order'] = 'desc' if self.sort_reverse else 'asc'

        # Sort using keeper's unified sorting logic and refresh
        self.branches = self.keeper.sort_branches(self.branches)
        self._populate_table()
        self._update_status()

        sort_name = {
            "age": "Age",
            "branch": "Branch Name",
            "status": "Status",
        }[self.sort_column]
        self.notify(f"Sorted by {sort_name} ({'desc' if self.sort_reverse else 'asc'})")

    @work(exclusive=True, thread=False)
    async def load_initial_data(self) -> None:
        """Load branch data on initial TUI startup (runs in background).
        
        Uses DataTable's built-in loading indicator, no modal is shown.
        """
        try:
            # Load branch details (with progress disabled for TUI)
            # Use asyncio.to_thread since keeper methods are sync but we're in async worker
            new_branches = await asyncio.to_thread(self.keeper.get_branch_details, False)

            if new_branches:
                # Update branches
                self.branches = new_branches

                # Sort and populate
                self.branches = self.keeper.sort_branches(self.branches)

                # Auto-mark deletable branches if cleanup mode is enabled
                if self.cleanup_mode:
                    for branch in self.branches:
                        if BranchValidationService.is_deletable(branch, self.keeper.protected_branches):
                            self.marked_branches.add(branch.name)

                self._populate_table()
                self._update_status()
            else:
                self.notify("No branches found", severity="warning")

        except Exception as e:
            logger.error(f"Error loading branches: {e}", exc_info=True)
            error_msg = f"Error loading branches:\n\n{str(e)}\n\nCheck the logs for more details."
            self.push_screen(InfoScreen(error_msg))
        finally:
            # Clear table loading state
            table = self.query_one(DataTable)
            table.loading = False

    @work(exclusive=True, thread=False)
    async def load_additional_data(
        self,
        cached_branches: Optional[List[BranchDetails]],
        branches_to_process: List[str]
    ) -> None:
        """Load branches with cached data as starting point, refresh unstable branches.

        This is called when we have cached data but some branches need refreshing.
        Shows loading indicator and displays complete data once processing is done.

        Args:
            cached_branches: Previously cached branch details (can be None)
            branches_to_process: List of branch names that need processing
        """
        if not branches_to_process:
            return

        table = self.query_one(DataTable)

        try:
            logger.debug(f"Processing {len(branches_to_process)} branches with cache base")

            # Start with cached branches if provided
            if cached_branches:
                existing_branches = {b.name: b for b in cached_branches}
            else:
                existing_branches = {b.name: b for b in self.branches}

            # Process the branches that need refreshing
            new_branch_details = []

            # Get PR data for all branches to process
            pr_data = {}
            try:
                pr_data = await asyncio.to_thread(
                    self.keeper.github_service.get_bulk_pr_data,
                    branches_to_process
                )
            except Exception as e:
                logger.debug(f"Failed to fetch PR data: {e}")

            # Process each branch
            for branch_name in branches_to_process:
                try:
                    details = await asyncio.to_thread(
                        self.keeper._process_single_branch,
                        branch_name,
                        self.keeper.config.get('status_filter', 'all'),
                        pr_data,
                        None
                    )
                    if details:
                        new_branch_details.append(details)
                except Exception as e:
                    logger.error(f"Error processing branch {branch_name}: {e}")

            # Merge refreshed branches with cached branches
            for branch in new_branch_details:
                existing_branches[branch.name] = branch

            # Update branches list
            self.branches = list(existing_branches.values())

            # Update in_worktree status for ALL branches (including cached)
            # Worktree status is dynamic and not cached
            worktree_branches = self.keeper.git_service.get_worktree_branches()
            try:
                current_branch = self.keeper.repo.active_branch.name
            except TypeError:
                current_branch = None

            for branch in self.branches:
                is_current = (branch.name == current_branch) if current_branch else False
                if branch.name in worktree_branches and not is_current:
                    branch.in_worktree = True
                    logger.debug(f"[TUI] Setting in_worktree=True for {branch.name}")
                else:
                    branch.in_worktree = False

            # Sort all branches
            self.branches = self.keeper.sort_branches(self.branches)

            # Auto-mark deletable branches if in cleanup mode
            if self.cleanup_mode:
                for branch in self.branches:
                    if BranchValidationService.is_deletable(branch, self.keeper.protected_branches):
                        self.marked_branches.add(branch.name)

            # Display the complete table once
            self._populate_table()
            self._update_status()

            # Save updated cache
            use_cache = not self.keeper.config.get('refresh', False)
            if use_cache:
                await asyncio.to_thread(
                    self.keeper.cache_service.save_cache,
                    self.branches,
                    self.keeper.main_branch
                )

        except Exception as e:
            logger.error(f"Error loading additional branches: {e}", exc_info=True)
            error_msg = f"Error loading additional branches:\n\n{str(e)}\n\nCheck the logs for more details."
            self.push_screen(InfoScreen(error_msg))
        finally:
            # Clear loading indicator
            table.loading = False

    def action_refresh(self) -> None:
        """Trigger refresh of branch data."""
        self.refresh_data()  # @work decorator handles Worker creation

    @work(exclusive=True, thread=False)
    async def refresh_data(self) -> None:
        """Refresh branch data by re-analyzing with cache bypass (runs in background).
        
        Uses DataTable's built-in loading indicator, no modal is shown.
        """
        table = self.query_one(DataTable)
        
        # Show table loading indicator
        table.loading = True

        # Store original refresh flag value using safe .get() method
        original_refresh = self.keeper.config.get('refresh', False)

        try:
            # Temporarily enable refresh to bypass cache
            self.keeper.config.refresh = True

            # Re-fetch branch details (with progress disabled for TUI)
            # Use asyncio.to_thread since keeper methods are sync but we're in async worker
            new_branches = await asyncio.to_thread(self.keeper.get_branch_details, False)

            if new_branches:
                # Save cursor position
                saved_row = table.cursor_row

                # Update branches
                self.branches = new_branches

                # Clear marks that no longer exist
                existing_names = {b.name for b in self.branches}
                self.marked_branches = {name for name in self.marked_branches if name in existing_names}

                # Re-sort and repopulate
                self.branches = self.keeper.sort_branches(self.branches)
                self._populate_table()
                self._update_status()

                # Restore cursor position if possible
                if saved_row is not None and saved_row < len(self.branches):
                    table.cursor_coordinate = Coordinate(saved_row, 0)

                self.notify("✓ Branch data refreshed", severity="information")
            else:
                self.notify("No branches found", severity="warning")

        except Exception as e:
            logger.error(f"Error refreshing: {e}", exc_info=True)
            error_msg = f"Error refreshing branch data:\n\n{str(e)}\n\nCheck the logs for more details."
            self.push_screen(InfoScreen(error_msg))
        finally:
            # Restore original refresh flag
            self.keeper.config.refresh = original_refresh
            # Clear table loading state
            table.loading = False

    async def action_quit(self) -> None:
        """Override quit action to clean up resources before exiting."""
        try:
            # Cancel all running workers before exit
            self.workers.cancel_all()

            # Close keeper resources (GitHub connections, etc.)
            if self.keeper:
                self.keeper.close()
        except Exception:
            # Silently ignore cleanup errors to avoid delays
            pass
        finally:
            # Call parent's exit method
            self.exit()
