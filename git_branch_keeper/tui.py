"""Interactive TUI for git-branch-keeper using Textual."""

import asyncio
from typing import List, Set, Optional
from textual import work
from textual.app import App, ComposeResult
from textual.events import Click
from textual.widgets import DataTable, Header, Footer, Static, TabbedContent, TabPane
from textual.binding import Binding
from textual.containers import Container, Vertical, ScrollableContainer
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button
from rich.text import Text

from .__version__ import __version__
from .models.branch import BranchDetails, BranchStatus
from .constants import (
    TUI_COLORS,
    SYMBOL_MARKED,
    SYMBOL_UNMARKED,
    COLUMNS,
    LEGEND_TEXT,
    BranchStyleType,
)
from .formatters import (
    format_date,
    format_remote_status,
    format_status,
    format_age,
    format_changes,
    format_deletion_confirmation_items,
    format_branch_name_with_indent,
    format_pr_link,
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
    """Modal info display dialog (legacy, kept for error messages)."""

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


class TabbedInfoScreen(ModalScreen):
    """Modal info dialog with dynamic tabs based on branch status."""

    DEFAULT_CSS = """
    TabbedInfoScreen {
        align: center middle;
    }

    #tabbed-info-dialog {
        width: 90%;
        height: 80%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }

    #tab-content {
        width: 100%;
        height: 1fr;
        padding: 1 0;
    }

    #info-button-container {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1 0;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("1", "switch_tab(0)", "Tab 1", show=False),
        Binding("2", "switch_tab(1)", "Tab 2", show=False),
        Binding("3", "switch_tab(2)", "Tab 3", show=False),
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close", show=False),
        Binding("i", "close", "Close", show=False),
    ]

    def __init__(self, branch: BranchDetails, keeper, main_branch: str):
        super().__init__()
        self.branch = branch
        self.keeper = keeper
        self.main_branch = main_branch

    def compose(self) -> ComposeResult:
        """Build the dialog with dynamic tabs based on branch status."""
        with Vertical(id="tabbed-info-dialog"):
            with TabbedContent(id="tab-content"):
                # Always include Info tab
                with TabPane("Info", id="tab-info"):
                    yield self._build_info_tab()

                # Add status-specific tabs
                has_uncommitted = (
                    self.branch.modified_files is True
                    or self.branch.untracked_files is True
                    or self.branch.staged_files is True
                )

                if has_uncommitted:
                    # Tabs for branches with uncommitted changes
                    with TabPane("Files", id="tab-files"):
                        yield self._build_files_tab()

                    with TabPane("Diff", id="tab-diff"):
                        yield self._build_diff_tab()

                elif self.branch.status == BranchStatus.MERGED:
                    # Tabs for merged branches
                    with TabPane("Merge Details", id="tab-merge"):
                        yield self._build_merge_tab()

                    with TabPane("Commits", id="tab-commits"):
                        yield self._build_commits_tab()

                elif self.branch.status == BranchStatus.STALE:
                    # Tabs for stale branches
                    with TabPane("Divergence", id="tab-divergence"):
                        yield self._build_divergence_tab()

                    with TabPane("Commits", id="tab-commits"):
                        yield self._build_commits_tab()

                elif self.branch.status == BranchStatus.ACTIVE:
                    # Tabs for active clean branches
                    with TabPane("History", id="tab-history"):
                        yield self._build_history_tab()

                    with TabPane("Comparison", id="tab-comparison"):
                        yield self._build_comparison_tab()

            with Container(id="info-button-container"):
                yield Button("Close", variant="primary", id="close")

    def _build_info_tab(self) -> Static:
        """Build the general info tab (always shown)."""
        from .services.branch_validation_service import BranchValidationService

        # Build change details
        if (
            self.branch.modified_files is None
            or self.branch.untracked_files is None
            or self.branch.staged_files is None
        ):
            # Check if there's an error in notes to reference
            if self.branch.notes and "[ERROR]" in self.branch.notes:
                changes_text = (
                    "[yellow]Unknown - see [bold]Notes[/bold] below for error details[/yellow]"
                )
            else:
                changes_text = "[yellow]Unknown (could not check)[/yellow]"
        else:
            change_details = []
            if self.branch.modified_files:
                change_details.append("Modified files")
            if self.branch.untracked_files:
                change_details.append("Untracked files")
            if self.branch.staged_files:
                change_details.append("Staged files")
            changes_text = ", ".join(change_details) if change_details else "Clean"

        # Build notes section - highlight errors
        notes_text = "None"
        if self.branch.notes:
            if "[ERROR]" in self.branch.notes:
                # Highlight errors in red
                notes_text = self.branch.notes.replace("[ERROR]", "[red][bold]ERROR:[/bold][/red]")
            else:
                notes_text = self.branch.notes

        # Format PR display using shared formatter
        github_base_url = self.keeper._get_github_base_url()
        pr_display = format_pr_link(self.branch.pr_status, github_base_url) or "None"

        # Format detailed info
        info = f"""[bold]Branch:[/bold] {self.branch.name}
[bold]Status:[/bold] {self.branch.status.value}
[bold]Age:[/bold] {self.branch.age_days} days
[bold]Last Commit:[/bold] {self.branch.last_commit_date}
[bold]Branch State:[/bold] {changes_text}
[bold]Sync:[/bold] {self.branch.sync_status}
[bold]Remote:[/bold] {"Yes" if self.branch.has_remote else "No"}
[bold]PRs:[/bold] {pr_display}
[bold]Notes:[/bold] {notes_text}
[bold]Protected:[/bold] {"Yes" if BranchValidationService.is_protected(self.branch.name, self.keeper.protected_branches) else "No"}
[bold]Deletable:[/bold] {"Yes" if BranchValidationService.is_deletable(self.branch, self.keeper.protected_branches) else "No"}
        """.strip()

        if self.branch.is_worktree:
            info += f"\n[bold]Worktree Path:[/bold] {self.branch.worktree_path}"

        return Static(info, markup=True)

    def _build_files_tab(self) -> ScrollableContainer:
        """Build the files tab showing uncommitted files."""
        git_service = self.keeper.git_service

        # Check if this is a worktree entry or a parent branch with a worktree
        if self.branch.is_worktree or (self.branch.in_worktree and self.branch.worktree_path):
            file_status = git_service.get_file_status_detailed(
                worktree_path=self.branch.worktree_path
            )
        else:
            file_status = git_service.get_file_status_detailed(branch_name=self.branch.name)

        content = "[bold]Uncommitted Files[/bold]\n\n"

        if file_status.get("staged"):
            content += "[green bold]Staged files:[/green bold]\n"
            for f in file_status["staged"]:
                content += f"  • {f}\n"
            content += "\n"

        if file_status.get("modified"):
            content += "[yellow bold]Modified files:[/yellow bold]\n"
            for f in file_status["modified"]:
                content += f"  • {f}\n"
            content += "\n"

        if file_status.get("untracked"):
            content += "[red bold]Untracked files:[/red bold]\n"
            for f in file_status["untracked"]:
                content += f"  • {f}\n"
            content += "\n"

        if not any(file_status.values()):
            content += "[dim]No uncommitted files[/dim]"

        return ScrollableContainer(Static(content, markup=True))

    def _build_diff_tab(self) -> ScrollableContainer:
        """Build the diff tab showing changes."""
        import os

        git_service = self.keeper.git_service

        # Get both staged and unstaged diffs
        # Check if this is a worktree entry or a parent branch with a worktree
        if self.branch.is_worktree or (self.branch.in_worktree and self.branch.worktree_path):
            unstaged_diff = git_service.get_diff(
                worktree_path=self.branch.worktree_path, staged=False
            )
            staged_diff = git_service.get_diff(worktree_path=self.branch.worktree_path, staged=True)
            file_status = git_service.get_file_status_detailed(
                worktree_path=self.branch.worktree_path
            )
            base_path = self.branch.worktree_path
        else:
            unstaged_diff = git_service.get_diff(branch_name=self.branch.name, staged=False)
            staged_diff = git_service.get_diff(branch_name=self.branch.name, staged=True)
            file_status = git_service.get_file_status_detailed(branch_name=self.branch.name)
            base_path = self.keeper.repo_path

        content = ""

        if staged_diff and staged_diff != "No changes":
            content += "[green bold]Staged Changes:[/green bold]\n\n"
            content += f"{staged_diff}\n\n"

        if unstaged_diff and unstaged_diff != "No changes":
            content += "[yellow bold]Unstaged Changes:[/yellow bold]\n\n"
            content += f"{unstaged_diff}\n\n"

        # If no diffs but there are untracked files, show their contents
        if not content and file_status.get("untracked") and base_path:
            content += "[red bold]Untracked Files:[/red bold]\n\n"
            for filepath in file_status["untracked"]:
                full_path = os.path.join(base_path, filepath)
                content += f"[cyan]--- {filepath}[/cyan]\n"

                try:
                    # Try to read file content
                    if os.path.isfile(full_path):
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            file_content = f.read()

                        # Limit content to first 50 lines to avoid huge output
                        lines = file_content.split("\n")
                        if len(lines) > 50:
                            content += "\n".join(lines[:50])
                            content += f"\n\n[dim]... ({len(lines) - 50} more lines)[/dim]\n"
                        else:
                            content += file_content
                    else:
                        content += "[dim]<file not found>[/dim]\n"
                except Exception as e:
                    content += f"[red]<error reading file: {e}>[/red]\n"

                content += "\n\n"

        if not content:
            content = "[dim]No changes to display[/dim]"

        return ScrollableContainer(Static(content, markup=True))

    def _build_merge_tab(self) -> ScrollableContainer:
        """Build the merge details tab."""
        git_service = self.keeper.git_service
        merge_details = git_service.get_merge_details(self.branch.name, self.main_branch)

        if merge_details.get("found"):
            content = f"""[bold]Merge Information[/bold]

[bold]Merge Commit:[/bold] {merge_details['merge_sha']}
[bold]Message:[/bold] {merge_details['merge_message']}
[bold]Author:[/bold] {merge_details['merge_author']}
[bold]Date:[/bold] {merge_details['merge_date']}
"""
        else:
            content = f"""[bold]Merge Information[/bold]

{merge_details.get('message', 'Unable to determine merge details')}
"""

        return ScrollableContainer(Static(content, markup=True))

    def _build_commits_tab(self) -> ScrollableContainer:
        """Build the commits tab showing branch commits."""
        git_service = self.keeper.git_service
        commits = git_service.get_branch_commits(self.branch.name, self.main_branch, limit=20)

        content = f"[bold]Commits on {self.branch.name} (not on {self.main_branch})[/bold]\n\n"

        if commits:
            for commit in commits:
                content += f"[cyan]{commit['sha']}[/cyan] {commit['date']} - [dim]{commit['author']}[/dim]\n"
                content += f"  {commit['message']}\n\n"
        else:
            content += "[dim]No unique commits on this branch[/dim]"

        return ScrollableContainer(Static(content, markup=True))

    def _build_divergence_tab(self) -> ScrollableContainer:
        """Build the divergence tab showing ahead/behind info."""
        git_service = self.keeper.git_service
        divergence = git_service.get_divergence_info(self.branch.name, self.main_branch)

        content = f"""[bold]Branch Divergence vs {self.main_branch}[/bold]

[bold]Ahead:[/bold] {divergence['ahead']} commits
[bold]Behind:[/bold] {divergence['behind']} commits

"""

        if divergence["ahead_commits"]:
            content += "[green bold]Commits ahead (on this branch):[/green bold]\n"
            for commit in divergence["ahead_commits"]:
                content += (
                    f"  [cyan]{commit['sha']}[/cyan] {commit['date']} - {commit['message']}\n"
                )
            content += "\n"

        if divergence["behind_commits"]:
            content += f"[yellow bold]Commits behind (on {self.main_branch}):[/yellow bold]\n"
            for commit in divergence["behind_commits"]:
                content += (
                    f"  [cyan]{commit['sha']}[/cyan] {commit['date']} - {commit['message']}\n"
                )

        return ScrollableContainer(Static(content, markup=True))

    def _build_history_tab(self) -> ScrollableContainer:
        """Build the history tab for active branches."""
        from datetime import datetime, timezone

        git_service = self.keeper.git_service

        # For main branch, show recent commits on main (not unique comparison)
        # For feature branches, show commits unique to that branch
        if self.branch.name == self.main_branch:
            # Show recent commits on main branch
            try:
                repo = git_service._get_repo()
                commits = []
                for commit in repo.iter_commits(self.main_branch, max_count=10):
                    commits.append(
                        {
                            "sha": commit.hexsha[:7],
                            "message": commit.message.strip().split("\n")[0],
                            "author": commit.author.name,
                            "date": datetime.fromtimestamp(
                                commit.committed_date, tz=timezone.utc
                            ).strftime("%Y-%m-%d %H:%M"),
                        }
                    )
                content_title = f"[bold]Recent commits on {self.main_branch}[/bold]\n\n"
            except Exception:
                commits = []
                content_title = f"[bold]Recent commits on {self.main_branch}[/bold]\n\n"
        else:
            # Show commits unique to feature branch
            commits = git_service.get_branch_commits(self.branch.name, self.main_branch, limit=10)
            content_title = f"[bold]Commits unique to {self.branch.name}[/bold]\n\n"

        content = content_title

        if commits:
            for commit in commits:
                content += (
                    f"[cyan]{commit['sha']}[/cyan] {commit['date']} - "
                    f"[dim]{commit['author']}[/dim]\n"
                )
                content += f"  {commit['message']}\n\n"
        else:
            if self.branch.name == self.main_branch:
                content += "[dim]No commits found[/dim]"
            else:
                content += "[dim]No commits unique to this branch[/dim]"

        return ScrollableContainer(Static(content, markup=True))

    def _build_comparison_tab(self) -> ScrollableContainer:
        """Build the comparison tab showing diff with main or remote status."""
        git_service = self.keeper.git_service

        try:
            repo = git_service._get_repo()

            # For main branch, show comparison with remote
            if self.branch.name == self.main_branch:
                try:
                    # Show difference between local main and origin/main
                    diff = repo.git.diff(f"origin/{self.main_branch}...{self.main_branch}")
                    content = f"[bold]Local vs Remote {self.main_branch}[/bold]\n\n"
                    if diff:
                        content += f"{diff}"
                    else:
                        content += "[dim]Local and remote are in sync[/dim]"
                except Exception:
                    content = "[dim]Cannot compare with remote - remote may not exist[/dim]"
            else:
                # For feature branches, show diff compared to main
                diff = repo.git.diff(f"{self.main_branch}...{self.branch.name}")
                content = f"[bold]Changes compared to {self.main_branch}[/bold]\n\n"
                if diff:
                    content += f"{diff}"
                else:
                    content += f"[dim]No differences with {self.main_branch}[/dim]"
        except Exception as e:
            content = f"[red]Error getting comparison: {e}[/red]"

        return ScrollableContainer(Static(content, markup=True))

    def action_switch_tab(self, index: int) -> None:
        """Switch to a specific tab by index."""
        tabbed_content = self.query_one(TabbedContent)
        tabs = list(tabbed_content.query(TabPane))

        if 0 <= index < len(tabs):
            tab_id = tabs[index].id
            if tab_id is not None:
                tabbed_content.active = tab_id

    def action_close(self) -> None:
        """Close the dialog."""
        self.dismiss()

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

    ToastRack {
        offset: 0 -3;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "delete_marked", "Delete Marked"),
        Binding("space", "toggle_mark", "Mark/Unmark"),
        Binding("f", "force_mark", "Force Mark"),
        Binding("a", "mark_all_deletable", "Mark All Deletable"),
        Binding("c", "clear_marks", "Clear Marks"),
        Binding("i", "show_info", "Show Info"),
        Binding("l", "show_legend", "Legend"),
        Binding("s", "cycle_sort", "Change Sort"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self, keeper, branches: Optional[List[BranchDetails]] = None, cleanup_mode: bool = False
    ):
        super().__init__()
        self.keeper = keeper
        self.branches = branches or []
        self.marked_branches: Set[str] = set()  # Normal marked branches
        self.force_marked_branches: Set[str] = set()  # Force-marked branches
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
                worktree_branches = self.keeper.git_service.worktree_service.get_worktree_branches()
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

                # Insert worktree entries after their parent branches
                self.branches = self.keeper._insert_worktree_entries(self.branches)

                # Auto-mark deletable branches if cleanup mode is enabled
                if self.cleanup_mode:
                    for branch in self.branches:
                        if BranchValidationService.is_deletable(
                            branch, self.keeper.protected_branches
                        ):
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

        # Get current branch name and GitHub URL once for all rows
        try:
            current_branch_name = self.keeper.repo.active_branch.name
        except (TypeError, AttributeError):
            current_branch_name = None  # Detached HEAD or repo not available

        github_base_url = self.keeper._get_github_base_url()

        for branch in self.branches:
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
            formatted_name = format_branch_name_with_indent(
                branch.name, branch.is_worktree, is_current
            )
            branch_text = Text(formatted_name, style=text_color)

            # Format status using shared formatter
            status_str = format_status(branch.status)
            status_text = Text(status_str, style=text_color)

            # Format last commit date using shared formatter
            last_commit = format_date(branch.last_commit_date)

            # Format age using shared formatter
            age_display = format_age(branch.age_days)

            # Changes column - using shared formatter
            changes_indicator = format_changes(branch, current_branch_name)
            changes = Text(changes_indicator, justify="center")

            # Remote column - using shared formatter
            remote_symbol = format_remote_status(branch.has_remote)
            remote = Text(remote_symbol, justify="center")

            # PR column - using shared formatter
            pr_display = format_pr_link(branch.pr_status, github_base_url)

            # Match COLUMNS order: Branch, Status, Last Commit, Age, Changes, Sync, Remote, PRs, Notes
            # (Plus Mark column at the beginning)
            # Make row key unique for worktrees by including path
            row_key = f"{branch.name}:{branch.worktree_path}" if branch.is_worktree else branch.name
            table.add_row(
                mark,
                branch_text,
                status_text,
                last_commit,
                age_display,
                changes,
                branch.sync_status or "",
                remote,
                pr_display,
                branch.notes or "",
                key=row_key,
            )

    def _mark_with_hierarchy(
        self, branch_name: str, mark_set: Set[str], is_force: bool = False
    ) -> tuple[bool, Optional[str]]:
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
            # Check protected branches (always enforced)
            if BranchValidationService.is_protected(branch.name, self.keeper.protected_branches):
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
        status = self.query_one("#status-bar", Static)

        total = len(self.branches)
        marked = len(self.marked_branches)
        deletable = sum(
            1
            for b in self.branches
            if BranchValidationService.is_deletable(b, self.keeper.protected_branches)
        )
        protected = sum(
            1
            for b in self.branches
            if BranchValidationService.is_protected(b.name, self.keeper.protected_branches)
        )

        sort_order = "desc" if self.sort_reverse else "asc"
        force_marked = len(self.force_marked_branches)

        status.update(
            f"Total: {total} | "
            f"Protected: {protected} | "
            f"Deletable: {deletable} | "
            f"Marked: {marked} | "
            f"Force: {force_marked} | "
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
                    self.notify(error, severity="warning")
                return

            # Remove from force-marked if it was there
            self.force_marked_branches.discard(branch.name)

        # Save cursor position before repopulating
        saved_row = table.cursor_row

        self._populate_table()
        self._update_status()

        # Restore cursor and move down one row
        if saved_row is not None and saved_row < len(self.branches):
            new_row = min(saved_row + 1, len(self.branches) - 1)
            table.cursor_coordinate = Coordinate(new_row, 0)

    def action_mark_all_deletable(self) -> None:
        """Mark all deletable branches (normal mode only)."""
        # Use shared method from keeper (normal mode)
        deletable_branches = self.keeper.get_deletable_branches(self.branches, force_mode=False)

        for branch in deletable_branches:
            self.marked_branches.add(branch.name)
            # Remove from force-marked if it was there
            self.force_marked_branches.discard(branch.name)

        self._populate_table()
        self._update_status()
        self.notify(f"Marked {len(self.marked_branches)} deletable branches")

    def action_clear_marks(self) -> None:
        """Clear all marks."""
        count = len(self.marked_branches) + len(self.force_marked_branches)
        self.marked_branches.clear()
        self.force_marked_branches.clear()
        self._populate_table()
        self._update_status()
        if count > 0:
            self.notify(f"Cleared {count} marks")

    def action_force_mark(self) -> None:
        """Force-mark current branch (ignores uncommitted changes)."""
        table = self.query_one(DataTable)
        if table.cursor_row is None:
            return

        row_index = table.cursor_row
        if row_index >= len(self.branches):
            return

        branch = self.branches[row_index]

        # Check basic force-mark eligibility (status)
        if branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
            self.notify("Can only force-mark stale/merged branches", severity="warning")
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
                    self.notify(error, severity="warning")
                return

            # Remove from normal marks if it was there
            self.marked_branches.discard(branch.name)

        saved_row = table.cursor_row
        self._populate_table()
        self._update_status()

        # Restore cursor and move down
        if saved_row is not None and saved_row < len(self.branches):
            new_row = min(saved_row + 1, len(self.branches) - 1)
            table.cursor_coordinate = Coordinate(new_row, 0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter key press on DataTable - triggers delete action."""
        self.action_delete_marked()

    def action_delete_marked(self) -> None:
        """Delete all marked branches."""
        total_marked = len(self.marked_branches) + len(self.force_marked_branches)

        if total_marked == 0:
            self.notify("No branches marked for deletion", severity="warning")
            return

        # Look up full BranchDetails objects for marked branches (both normal and force)
        all_marked_names = self.marked_branches | self.force_marked_branches
        branches_to_delete = [branch for branch in self.branches if branch.name in all_marked_names]

        # Build confirmation message
        force_count = len(self.force_marked_branches)
        normal_count = len(self.marked_branches)
        branches_list = format_deletion_confirmation_items(branches_to_delete)

        if force_count > 0:
            message = (
                f"Delete {total_marked} marked branch{'es' if total_marked > 1 else ''}?\n"
                f"({normal_count} normal, {force_count} force-marked)\n\n"
                f"{branches_list}"
            )
        else:
            message = f"Delete {total_marked} marked branch{'es' if total_marked > 1 else ''}?\n\n{branches_list}"

        # Show confirmation screen
        self.push_screen(ConfirmScreen(message), self._handle_delete_confirmation)

    def _handle_delete_confirmation(self, confirmed: bool | None) -> None:
        """Handle delete confirmation result."""
        if not confirmed:
            self.notify("Deletion cancelled")
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
                    elif branch.worktree_is_orphaned:
                        branches.append(branch)
                    elif is_force or BranchValidationService.is_deletable(
                        branch, self.keeper.protected_branches
                    ):
                        branches.append(branch)

            return branches, worktrees

        # Process force-marked branches first
        force_branches, force_worktrees = process_marked_branches(
            self.force_marked_branches, is_force=True
        )

        # Process normal-marked branches
        normal_branches, normal_worktrees = process_marked_branches(
            self.marked_branches, is_force=False
        )

        all_deleted_branches = []
        all_failed_branches = []
        all_removed_worktrees = []
        all_failed_worktrees = []

        # Use shared deletion logic from keeper
        try:
            # Delete force-marked items with force mode
            if force_branches or force_worktrees:
                deleted, failed_b, removed, failed_w = self.keeper.perform_deletion(
                    force_branches, force_worktrees, force_mode=True
                )
                all_deleted_branches.extend(deleted)
                all_failed_branches.extend(failed_b)
                all_removed_worktrees.extend(removed)
                all_failed_worktrees.extend(failed_w)

            # Delete normal-marked items without force
            if normal_branches or normal_worktrees:
                deleted, failed_b, removed, failed_w = self.keeper.perform_deletion(
                    normal_branches, normal_worktrees, force_mode=False
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
                self.notify(
                    f"✓ Removed {len(all_removed_worktrees)} worktrees and deleted {len(all_deleted_branches)} branches",
                    severity="information",
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

        except Exception as e:
            error_msg = f"Error during deletion:\n\n{str(e)}"
            self.push_screen(InfoScreen(error_msg))

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
        self.keeper.config["sort_by"] = sort_by_mapping[self.sort_column]
        self.keeper.config["sort_order"] = "desc" if self.sort_reverse else "asc"

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
                # Update branches (already sorted with worktrees from get_branch_details)
                self.branches = new_branches

                # Auto-mark deletable branches if cleanup mode is enabled
                if self.cleanup_mode:
                    for branch in self.branches:
                        if BranchValidationService.is_deletable(
                            branch, self.keeper.protected_branches
                        ):
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
        self, cached_branches: Optional[List[BranchDetails]], branches_to_process: List[str]
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
                    self.keeper.github_service.get_bulk_pr_data, branches_to_process
                )
            except Exception as e:
                logger.debug(f"Failed to fetch PR data: {e}")

            # Process each branch
            for branch_name in branches_to_process:
                try:
                    details = await asyncio.to_thread(
                        self.keeper._process_single_branch,
                        branch_name,
                        self.keeper.config.get("status_filter", "all"),
                        pr_data,
                        None,
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
            worktree_branches = self.keeper.git_service.worktree_service.get_worktree_branches()
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

            # Insert worktree entries after their parent branches
            self.branches = self.keeper._insert_worktree_entries(self.branches)

            # Auto-mark deletable branches if in cleanup mode
            if self.cleanup_mode:
                for branch in self.branches:
                    if BranchValidationService.is_deletable(branch, self.keeper.protected_branches):
                        self.marked_branches.add(branch.name)

            # Display the complete table once
            self._populate_table()
            self._update_status()

            # Save updated cache
            use_cache = not self.keeper.config.get("refresh", False)
            if use_cache:
                await asyncio.to_thread(
                    self.keeper.cache_service.save_cache, self.branches, self.keeper.main_branch
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
        original_refresh = self.keeper.config.get("refresh", False)

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
                self.marked_branches = {
                    name for name in self.marked_branches if name in existing_names
                }

                # Repopulate table (branches already sorted and have worktrees from get_branch_details)
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
            error_msg = (
                f"Error refreshing branch data:\n\n{str(e)}\n\nCheck the logs for more details."
            )
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
