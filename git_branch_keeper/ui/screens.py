"""Modal screens for git-branch-keeper TUI."""

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent, TabPane

from git_branch_keeper.formatters import format_pr_link
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.services.branch_validation_service import BranchValidationService

if TYPE_CHECKING:
    from git_branch_keeper.core import BranchKeeper


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

    def __init__(self, branch: BranchDetails, keeper: "BranchKeeper", main_branch: str):
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
