"""Modal screens for git-branch-keeper TUI."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent, TabPane

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.formatters import format_display_status, format_pr_link
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.services.branch_validation_service import BranchValidationService

if TYPE_CHECKING:
    from git_branch_keeper.core import BranchKeeper


def _format_location(branch: BranchDetails) -> str:
    """Describe where a branch lives, for the detail pane's Remote row.

    A plain "Yes"/"No" cannot say "remote only", which is the one case the user
    needs told: those branches are analyzed but can never be deleted here.
    """
    if branch.has_remote and not branch.has_local:
        return "Remote only (no local branch; not deletable)"
    return "Yes" if branch.has_remote else "No"


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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "confirm_yes", "Confirm", show=False),
        Binding("y", "confirm_yes", "Yes", show=False),
        Binding("escape", "confirm_no", "Cancel", show=False),
        Binding("n", "confirm_no", "No", show=False),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.message, id="confirm-message")
            with Container(id="button-container"):
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    def action_confirm_yes(self) -> None:
        """Confirm action (Yes)."""
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        """Cancel action (No)."""
        self.dismiss(False)

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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "close", "Close", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(self, info: str):
        super().__init__()
        self.info = info

    def compose(self) -> ComposeResult:
        with Vertical(id="info-dialog"):
            yield Static(self.info, id="info-content")
            with Container(id="info-button-container"):
                yield Button("Close", variant="primary", id="close")

    def action_close(self) -> None:
        """Close the dialog."""
        self.dismiss()

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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "switch_tab(0)", "Tab 1", show=False),
        Binding("2", "switch_tab(1)", "Tab 2", show=False),
        Binding("3", "switch_tab(2)", "Tab 3", show=False),
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close", show=False),
        Binding("i", "close", "Close", show=False),
    ]

    def __init__(self, branch: BranchDetails, keeper: BranchKeeper, main_branch: str):
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

                elif self.branch.status in (BranchStatus.ACTIVE, BranchStatus.UNSTARTED):
                    # Tabs for active clean branches
                    with TabPane("History", id="tab-history"):
                        yield self._build_history_tab()

                    with TabPane("Comparison", id="tab-comparison"):
                        yield self._build_comparison_tab()

            with Container(id="info-button-container"):
                yield Button("Close", variant="primary", id="close")

    def _worktree_path_for_branch(self) -> str | None:
        """Return the worktree path associated with this row, if any."""
        if self.branch.worktree_path:
            return self.branch.worktree_path

        if not self.branch.in_worktree:
            return None

        try:
            worktree_infos = self.keeper.git_service.worktree_service.get_worktree_info()
            worktree_info = next(
                (
                    wt
                    for wt in worktree_infos
                    if wt.branch_name == self.branch.name and not wt.is_main
                ),
                None,
            )
            return worktree_info.path if worktree_info else None
        except GIT_ERRORS:
            return None

    def _build_deletion_blockers(self) -> list[str]:
        """Return human-readable reasons this row is not currently deletable."""
        blockers = []
        worktree_path = self._worktree_path_for_branch()

        if self.branch.is_worktree:
            if self.branch.modified_files:
                blockers.append("Worktree has modified files")
            if self.branch.untracked_files:
                blockers.append("Worktree has untracked files")
            if self.branch.staged_files:
                blockers.append("Worktree has staged files")
            return blockers

        if BranchValidationService.is_protected(self.branch.name, self.keeper.protected_branches):
            blockers.append("Branch is protected")

        # Listed first among the status reasons because it holds regardless of
        # status: a remote-only branch reported as merged is still not deletable.
        if self.branch.has_remote and not self.branch.has_local:
            blockers.append(
                "Branch exists only on the remote - git-branch-keeper does not delete remote branches"
            )

        if self.branch.status == BranchStatus.UNSTARTED:
            blockers.append("Branch has no commits of its own - nothing was merged")
        elif self.branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
            blockers.append(f"Branch status is {self.branch.status.value}, not stale/merged")

        if self.branch.in_worktree:
            if worktree_path:
                blockers.append(f"Branch is checked out in worktree: {worktree_path}")
            else:
                blockers.append("Branch is checked out in another worktree")

        if self.branch.modified_files:
            blockers.append("Worktree/branch has modified files")
        if self.branch.untracked_files:
            blockers.append("Worktree/branch has untracked files")
        if self.branch.staged_files:
            blockers.append("Worktree/branch has staged files")

        return blockers

    def _format_deletion_blockers(self) -> str:
        """Format deletion blockers for the Info tab."""
        blockers = self._build_deletion_blockers()
        if not blockers:
            return "None"
        return "\n".join(f"  • {blocker}" for blocker in blockers)

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

        # Format PR/status display using shared formatters
        github_base_url = self.keeper._get_github_base_url()
        pr_display = format_pr_link(self.branch.pr_status, github_base_url) or "None"
        display_status = format_display_status(self.branch, self.keeper.protected_branches)
        status_lines = f"[bold]Status:[/bold] {display_status}"
        if display_status != self.branch.status.value:
            status_lines += f"\n[bold]Merge Status:[/bold] {self.branch.status.value}"
        worktree_path = self._worktree_path_for_branch()
        deletion_blockers = self._format_deletion_blockers()
        is_deletable = (
            False
            if self.branch.is_worktree
            else BranchValidationService.is_deletable(self.branch, self.keeper.protected_branches)
        )

        # Format detailed info
        info = f"""[bold]Branch:[/bold] {self.branch.name}
{status_lines}
[bold]Age:[/bold] {self.branch.age_days} days
[bold]Last Commit:[/bold] {self.branch.last_commit_date}
[bold]Branch State:[/bold] {changes_text}
[bold]Sync:[/bold] {self.branch.sync_status}
[bold]Remote:[/bold] {_format_location(self.branch)}
[bold]PRs:[/bold] {pr_display}
[bold]Notes:[/bold] {notes_text}
[bold]Protected:[/bold] {"Yes" if BranchValidationService.is_protected(self.branch.name, self.keeper.protected_branches) else "No"}
[bold]Deletable:[/bold] {"Yes" if is_deletable else "No"}
[bold]Deletion Blockers:[/bold]
{deletion_blockers}
        """.strip()

        if self.branch.is_worktree:
            info += (
                "\n[bold]Row Type:[/bold] Worktree entry (remove worktree before deleting branch)"
            )

        if worktree_path:
            info += f"\n[bold]Worktree Path:[/bold] {worktree_path}"

        return Static(info, markup=True)

    def _build_files_tab(self) -> ScrollableContainer:
        """Build the files tab showing uncommitted files."""
        git_service = self.keeper.git_service

        worktree_path = self._worktree_path_for_branch()

        # Check if this is a worktree entry or a parent branch with a worktree
        if self.branch.is_worktree or (self.branch.in_worktree and worktree_path):
            file_status = git_service.get_file_status_detailed(worktree_path=worktree_path)
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
        worktree_path = self._worktree_path_for_branch()

        # Check if this is a worktree entry or a parent branch with a worktree
        if self.branch.is_worktree or (self.branch.in_worktree and worktree_path):
            unstaged_diff = git_service.get_diff(worktree_path=worktree_path, staged=False)
            staged_diff = git_service.get_diff(worktree_path=worktree_path, staged=True)
            file_status = git_service.get_file_status_detailed(worktree_path=worktree_path)
            base_path = worktree_path
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
                        with open(full_path, encoding="utf-8", errors="ignore") as f:
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
                except OSError as e:
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
            except GIT_ERRORS:
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
                    # Show difference between local main and <remote>/main
                    remote_name = git_service.remote_name
                    diff = repo.git.diff(f"{remote_name}/{self.main_branch}...{self.main_branch}")
                    content = f"[bold]Local vs Remote {self.main_branch}[/bold]\n\n"
                    if diff:
                        content += f"{diff}"
                    else:
                        content += "[dim]Local and remote are in sync[/dim]"
                except GIT_ERRORS:
                    content = "[dim]Cannot compare with remote - remote may not exist[/dim]"
            else:
                # For feature branches, show diff compared to main
                diff = repo.git.diff(f"{self.main_branch}...{self.branch.name}")
                content = f"[bold]Changes compared to {self.main_branch}[/bold]\n\n"
                if diff:
                    content += f"{diff}"
                else:
                    content += f"[dim]No differences with {self.main_branch}[/dim]"
        except GIT_ERRORS as e:
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
