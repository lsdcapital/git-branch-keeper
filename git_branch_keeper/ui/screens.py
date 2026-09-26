"""Modal screens for git-branch-keeper TUI."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Literal, TypeVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent, TabPane

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.formatters import format_display_status, format_pr_text
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.services.branch_validation_service import BranchValidationService

if TYPE_CHECKING:
    from git_branch_keeper.core import BranchKeeper


def _format_location(branch: BranchDetails) -> str:
    """Describe where a branch lives, for the detail pane's Remote row.

    A plain "Yes"/"No" cannot say "remote only", which is the one case the user
    needs called out because deleting it necessarily mutates shared remote state.
    """
    if branch.has_remote and not branch.has_local:
        return "Remote only (deletion requires remote opt-in)"
    return "Yes" if branch.has_remote else "No"


def _m(markup: str, **values: object) -> Content:
    """Render trusted markup, inserting each `$name` value as literal text.

    Commit messages, diffs, file contents, paths and git errors must never be
    parsed: `[/]` in a diff crashes the screen, and `[@click=app.delete_marked]`
    in a commit message becomes a link that runs an app action. Escaping the
    string is not enough - a trailing backslash escapes the *next* closing tag.
    Textual's variable substitution inserts values outside the parser.
    """
    return Content.from_markup(markup, **{name: str(value) for name, value in values.items()})


def _join(parts: Iterable[Content]) -> Content:
    return Content("").join(parts)


ScreenResultT = TypeVar("ScreenResultT")
ConfirmationTone = Literal["default", "scope", "warning", "danger"]


@dataclass(frozen=True, slots=True)
class ConfirmationSection:
    """One scannable block inside a confirmation prompt."""

    title: str
    body: str
    tone: ConfirmationTone = "default"


@dataclass(frozen=True, slots=True)
class ConfirmationPrompt:
    """Presentation model shared by every confirmation dialog."""

    title: str
    question: str
    confirm_label: str
    sections: tuple[ConfirmationSection, ...] = ()
    description: str | None = None
    warning: str | None = None
    cancel_label: str = "Cancel"
    danger: bool = True
    default_confirm: bool = False

    def plain_text(self) -> str:
        """Return the prompt without presentation styling for logs and tests."""
        parts = [self.question]
        if self.description:
            parts.append(self.description)
        if self.warning:
            parts.append(self.warning)
        parts.extend(f"{section.title}\n{section.body}" for section in self.sections)
        return "\n\n".join(parts)


class BaseModal(ModalScreen[ScreenResultT]):
    """Shared chrome for every git-branch-keeper modal dialog.

    Subclasses compose a ``Vertical(classes="modal-dialog")`` root containing a
    ``.modal-body`` scroll region and a ``.modal-buttons`` row. The shared rules below
    select by CSS *class* and subclasses override by *id*, so overrides win on plain
    specificity (id beats class) rather than on DEFAULT_CSS merge order.

    Note that a rule targeting the screen itself must literally start with ``BaseModal``.
    Textual scopes each rule to the declaring class, so ``ModalScreen { ... }`` would
    become the descendant selector ``BaseModal ModalScreen { ... }`` and match nothing.
    """

    DEFAULT_CSS = """
    BaseModal {
        align: center middle;
    }

    BaseModal .modal-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: round $primary;
        border-title-align: left;
        border-title-color: $text;
        border-title-background: $surface;
        border-subtitle-align: right;
        border-subtitle-color: $text-muted;
    }

    BaseModal .modal-dialog.-danger {
        border: round $error;
        border-title-color: $error;
    }

    BaseModal .modal-body {
        width: 100%;
        height: auto;
        max-height: 16;
        padding: 1 0;
        scrollbar-size-vertical: 1;
    }

    BaseModal .modal-buttons {
        layout: horizontal;
        width: 100%;
        height: auto;
        align-horizontal: right;
        padding-top: 1;
    }

    BaseModal .modal-buttons Button {
        margin-left: 1;
        min-width: 12;
    }
    """


class ConfirmScreen(BaseModal[bool]):
    """Structured, safety-first confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen #confirm-dialog {
        width: 76;
        min-width: 40;
        max-width: 94%;
        max-height: 90%;
        padding: 0;
    }

    ConfirmScreen #confirm-dialog.-structured {
        height: auto;
    }

    ConfirmScreen #confirm-dialog.-simple {
        height: auto;
    }

    ConfirmScreen #confirm-header {
        width: 100%;
        height: auto;
        padding: 1 2 0 2;
        background: $surface;
    }

    ConfirmScreen #confirm-question {
        width: 100%;
        height: auto;
        color: $text;
        text-style: bold;
    }

    ConfirmScreen #confirm-description {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    ConfirmScreen #confirm-warning {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $warning;
        text-style: bold;
    }

    ConfirmScreen #confirm-message {
        height: auto;
        max-height: 10;
        padding: 1 2 0 2;
    }

    ConfirmScreen .confirm-section {
        width: 100%;
        height: auto;
        margin-bottom: 0;
        padding-bottom: 0;
    }

    ConfirmScreen .confirm-section.-scope {
        padding: 1 2;
        background: $primary 12%;
    }

    ConfirmScreen .confirm-section.-warning {
        padding: 1 2;
        background: $warning 14%;
    }

    ConfirmScreen .confirm-section.-danger {
        padding: 1 2;
        background: $error 14%;
    }

    ConfirmScreen .confirm-section-title {
        width: 100%;
        height: 1;
        color: $text-muted;
        text-style: bold;
    }

    ConfirmScreen .confirm-section.-scope .confirm-section-title {
        color: $primary;
    }

    ConfirmScreen .confirm-section.-warning .confirm-section-title {
        color: $warning;
    }

    ConfirmScreen .confirm-section.-danger .confirm-section-title {
        color: $error;
    }

    ConfirmScreen .confirm-section-body {
        width: 100%;
        height: auto;
        margin-top: 0;
        color: $text;
    }

    ConfirmScreen #confirm-actions {
        height: 2;
        min-height: 2;
        padding: 1 2 0 2;
        background: $surface;
    }

    ConfirmScreen #confirm-actions Button {
        width: auto;
        min-width: 10;
        height: 1;
        margin-left: 2;
    }
    """

    AUTO_FOCUS = "#no"

    # No "enter" binding: Enter activates the focused button. Generic confirmations
    # focus Cancel; intentionally initiated batch deletions can opt into confirm focus.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "confirm_yes", "Yes", show=False),
        Binding("escape", "confirm_no", "Cancel", show=False),
        Binding("n", "confirm_no", "No", show=False),
    ]

    def __init__(
        self,
        prompt: ConfirmationPrompt | str,
        dialog_title: str = "Confirm",
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        danger: bool = True,
    ):
        """Create a confirmation screen.

        A plain string remains supported for callers that only need a question. New
        flows should pass ``ConfirmationPrompt`` so impact, scope, and selections
        remain visually distinct.
        """
        # Not "title": Screen.title is a reactive that overrides App.title while active.
        super().__init__()
        self.prompt = (
            prompt
            if isinstance(prompt, ConfirmationPrompt)
            else ConfirmationPrompt(
                title=dialog_title,
                question=prompt,
                confirm_label=confirm_label,
                cancel_label=cancel_label,
                danger=danger,
            )
        )
        # Preserve a plain representation for diagnostics and compatibility with callers
        # that inspect the message while the modal is open.
        self.message = self.prompt.plain_text()
        self.dialog_title = self.prompt.title
        self.confirm_label = self.prompt.confirm_label
        self.cancel_label = self.prompt.cancel_label
        self.danger = self.prompt.danger

    def compose(self) -> ComposeResult:
        structure_class = "-structured" if self.prompt.sections else "-simple"
        danger_class = " -danger" if self.danger else ""
        classes = f"modal-dialog {structure_class}{danger_class}"
        with Vertical(id="confirm-dialog", classes=classes):
            with Vertical(id="confirm-header"):
                yield Static(self.prompt.question, id="confirm-question", markup=False)
                if self.prompt.description:
                    yield Static(
                        self.prompt.description,
                        id="confirm-description",
                        markup=False,
                    )
                if self.prompt.warning:
                    yield Static(
                        self.prompt.warning,
                        id="confirm-warning",
                        markup=False,
                    )
            if self.prompt.sections:
                with ScrollableContainer(id="confirm-message", classes="modal-body"):
                    for section in self.prompt.sections:
                        section_classes = f"confirm-section -{section.tone}"
                        with Vertical(classes=section_classes):
                            yield Static(
                                section.title,
                                classes="confirm-section-title",
                                markup=False,
                            )
                            # Raw branch names and git metadata may contain Rich markup
                            # delimiters, so every caller-provided value stays literal.
                            yield Static(
                                section.body,
                                classes="confirm-section-body",
                                markup=False,
                            )
            with Horizontal(id="confirm-actions", classes="modal-buttons"):
                yield Button(
                    self.cancel_label,
                    variant="default",
                    id="no",
                    compact=True,
                )
                yield Button(
                    self.confirm_label,
                    variant="error" if self.danger else "primary",
                    id="yes",
                    compact=True,
                )

    def on_mount(self) -> None:
        dialog = self.query_one("#confirm-dialog", Vertical)
        dialog.border_title = self.dialog_title
        if self.prompt.default_confirm:
            dialog.border_subtitle = f" Esc {self.cancel_label} · Enter/Y {self.confirm_label} "
        else:
            dialog.border_subtitle = f" Esc/Enter {self.cancel_label} · Y {self.confirm_label} "
        if self.prompt.default_confirm:
            self.query_one("#yes", Button).focus()

    def action_confirm_yes(self) -> None:
        """Confirm action (Yes)."""
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        """Cancel action (No)."""
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        self.dismiss(event.button.id == "yes")


class InfoScreen(BaseModal[None]):
    """Modal info display dialog (legacy, kept for error messages)."""

    AUTO_FOCUS = "#close"

    # Enter is kept here: the focused Close button and the screen binding do the same
    # thing, so unlike ConfirmScreen there is no ambiguity to remove.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "close", "Close", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(self, info: str, dialog_title: str = "Info"):
        super().__init__()
        self.info = info
        self.dialog_title = dialog_title

    def compose(self) -> ComposeResult:
        with Vertical(id="info-dialog", classes="modal-dialog"):
            with ScrollableContainer(id="info-content", classes="modal-body"):
                # markup=False: callers pass raw git/OS error text.
                yield Static(self.info, markup=False)
            with Horizontal(id="info-button-container", classes="modal-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_mount(self) -> None:
        self.query_one("#info-dialog", Vertical).border_title = self.dialog_title

    def action_close(self) -> None:
        """Close the dialog."""
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        self.dismiss()


class TabbedInfoScreen(BaseModal[None]):
    """Modal info dialog with dynamic tabs based on branch status."""

    # max-width/max-height are load-bearing: without them BaseModal's 90%/80% caps would
    # clamp this dialog to 81%x64%. No AUTO_FOCUS either - the default focuses the tab
    # bar, which is what makes left/right arrow tab switching work.
    DEFAULT_CSS = """
    TabbedInfoScreen #tabbed-info-dialog {
        width: 90%;
        max-width: 100%;
        height: 80%;
        max-height: 100%;
    }

    TabbedInfoScreen #tab-content {
        width: 100%;
        height: 1fr;
        padding: 1 0;
    }

    TabbedInfoScreen TabbedContent {
        height: 1fr;
    }

    TabbedInfoScreen TabPane {
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
        with Vertical(id="tabbed-info-dialog", classes="modal-dialog"):
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

            with Horizontal(id="info-button-container", classes="modal-buttons"):
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

        if not self.branch.has_local and not (
            self.branch.has_remote and self.branch.status == BranchStatus.MERGED
        ):
            blockers.append("Remote-only branch is not confirmed merged")
        elif not self.branch.has_local and not self.keeper.delete_remote:
            blockers.append(
                "Remote deletion is disabled; press 'd' in the branch list to enable it"
            )

        if (
            not self.branch.has_local
            and self.keeper.owned_remote_only
            and not self.keeper.remote_deletion_allowed(self.branch)
        ):
            blockers.append("Remote branch is outside your verified merged-PR selection")

        if self.branch.status == BranchStatus.UNSTARTED:
            blockers.append(
                "Tip is already in main; merge history is unclear"
                if not self.branch.has_local
                else "Branch has no commits of its own - nothing was merged"
            )
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
                changes = _m("[yellow]Unknown - see [bold]Notes[/bold] below for error details[/]")
            else:
                changes = _m("[yellow]Unknown (could not check)[/yellow]")
        else:
            change_details = []
            if self.branch.modified_files:
                change_details.append("Modified files")
            if self.branch.untracked_files:
                change_details.append("Untracked files")
            if self.branch.staged_files:
                change_details.append("Staged files")
            changes = Content(", ".join(change_details) if change_details else "Clean")

        # Build notes section - highlight errors. Notes carry git error text and
        # worktree paths, so everything around the ERROR label stays literal.
        notes: Content = Content("None")
        if self.branch.notes:
            error_label = _m("[red][bold]ERROR:[/bold][/red]")
            notes = error_label.join(Content(part) for part in self.branch.notes.split("[ERROR]"))

        # Format PR/status display using shared formatters
        github_base_url = self.keeper._get_github_base_url()
        pr_text = format_pr_text(self.branch.pr_status, github_base_url)
        pr_display = Content.from_rich_text(pr_text) if pr_text.plain else Content("None")
        display_status = format_display_status(
            self.branch,
            self.keeper.protected_branches,
            allow_remote_deletion=self.keeper.remote_deletion_allowed(self.branch),
        )

        worktree_path = self._worktree_path_for_branch()
        is_deletable = (
            False
            if self.branch.is_worktree
            else BranchValidationService.is_deletable(
                self.branch,
                self.keeper.protected_branches,
                allow_remote_deletion=self.keeper.remote_deletion_allowed(self.branch),
            )
        )
        is_protected = BranchValidationService.is_protected(
            self.branch.name, self.keeper.protected_branches
        )

        parts = [
            _m("[bold]Branch:[/bold] $v\n", v=self.branch.name),
            _m("[bold]Status:[/bold] $v\n", v=display_status),
            _m("[bold]Age:[/bold] $v days\n", v=self.branch.age_days),
            _m("[bold]Last Commit:[/bold] $v\n", v=self.branch.last_commit_date),
            _m("[bold]Branch State:[/bold] "),
            changes,
            _m("\n[bold]Sync:[/bold] $v\n", v=self.branch.sync_status),
            _m("[bold]Remote:[/bold] $v\n", v=_format_location(self.branch)),
            _m("[bold]PRs:[/bold] "),
            pr_display,
            _m("\n[bold]Notes:[/bold] "),
            notes,
            _m("\n[bold]Protected:[/bold] $v\n", v="Yes" if is_protected else "No"),
            _m("[bold]Deletable:[/bold] $v\n", v="Yes" if is_deletable else "No"),
            _m("[bold]Deletion Blockers:[/bold]\n$v", v=self._format_deletion_blockers()),
        ]

        if self.branch.is_worktree:
            parts.append(
                _m(
                    "\n[bold]Row Type:[/bold] Worktree entry "
                    "(remove worktree before deleting branch)"
                )
            )

        if worktree_path:
            parts.append(_m("\n[bold]Worktree Path:[/bold] $v", v=worktree_path))

        return Static(_join(parts))

    def _build_files_tab(self) -> ScrollableContainer:
        """Build the files tab showing uncommitted files."""
        git_service = self.keeper.git_service

        worktree_path = self._worktree_path_for_branch()

        # Check if this is a worktree entry or a parent branch with a worktree
        if self.branch.is_worktree or (self.branch.in_worktree and worktree_path):
            file_status = git_service.get_file_status_detailed(worktree_path=worktree_path)
        else:
            file_status = git_service.get_file_status_detailed(branch_name=self.branch.name)

        parts = [_m("[bold]Uncommitted Files[/bold]\n\n")]

        for key, heading in (
            ("staged", "[green bold]Staged files:[/green bold]\n"),
            ("modified", "[yellow bold]Modified files:[/yellow bold]\n"),
            ("untracked", "[red bold]Untracked files:[/red bold]\n"),
        ):
            if file_status.get(key):
                parts.append(_m(heading))
                parts.extend(Content(f"  • {f}\n") for f in file_status[key])
                parts.append(Content("\n"))

        if not any(file_status.values()):
            parts.append(_m("[dim]No uncommitted files[/dim]"))

        return ScrollableContainer(Static(_join(parts)))

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

        parts: list[Content] = []

        if staged_diff and staged_diff != "No changes":
            parts.append(_m("[green bold]Staged Changes:[/green bold]\n\n"))
            parts.append(Content(f"{staged_diff}\n\n"))

        if unstaged_diff and unstaged_diff != "No changes":
            parts.append(_m("[yellow bold]Unstaged Changes:[/yellow bold]\n\n"))
            parts.append(Content(f"{unstaged_diff}\n\n"))

        # If no diffs but there are untracked files, show their contents
        if not parts and file_status.get("untracked") and base_path:
            parts.append(_m("[red bold]Untracked Files:[/red bold]\n\n"))
            for filepath in file_status["untracked"]:
                full_path = os.path.join(base_path, filepath)
                parts.append(_m("[cyan]--- $v[/cyan]\n", v=filepath))

                try:
                    # Try to read file content
                    if os.path.isfile(full_path):
                        with open(full_path, encoding="utf-8", errors="ignore") as f:
                            file_content = f.read()

                        # Limit content to first 50 lines to avoid huge output
                        lines = file_content.split("\n")
                        if len(lines) > 50:
                            parts.append(Content("\n".join(lines[:50])))
                            parts.append(
                                _m("\n\n[dim]... ($v more lines)[/dim]\n", v=len(lines) - 50)
                            )
                        else:
                            parts.append(Content(file_content))
                    else:
                        parts.append(_m("[dim]<file not found>[/dim]\n"))
                except OSError as e:
                    parts.append(_m("[red]<error reading file: $v>[/red]\n", v=e))

                parts.append(Content("\n\n"))

        if not parts:
            parts.append(_m("[dim]No changes to display[/dim]"))

        return ScrollableContainer(Static(_join(parts)))

    def _build_merge_tab(self) -> ScrollableContainer:
        """Build the merge details tab."""
        git_service = self.keeper.git_service
        merge_details = git_service.get_merge_details(self.branch.name, self.main_branch)

        if merge_details.get("found"):
            content = _m(
                "[bold]Merge Information[/bold]\n\n"
                "[bold]Merge Commit:[/bold] $sha\n"
                "[bold]Message:[/bold] $message\n"
                "[bold]Author:[/bold] $author\n"
                "[bold]Date:[/bold] $date\n",
                sha=merge_details["merge_sha"],
                message=merge_details["merge_message"],
                author=merge_details["merge_author"],
                date=merge_details["merge_date"],
            )
        else:
            content = _m(
                "[bold]Merge Information[/bold]\n\n$v\n",
                v=merge_details.get("message", "Unable to determine merge details"),
            )

        return ScrollableContainer(Static(content))

    @staticmethod
    def _commit_lines(commits: list[dict]) -> list[Content]:
        """Render commits as `sha date - author` over an indented message line."""
        return [
            _m(
                "[cyan]$sha[/cyan] $date - [dim]$author[/dim]\n  $message\n\n",
                sha=commit["sha"],
                date=commit["date"],
                author=commit["author"],
                message=commit["message"],
            )
            for commit in commits
        ]

    def _build_commits_tab(self) -> ScrollableContainer:
        """Build the commits tab showing branch commits."""
        git_service = self.keeper.git_service
        commits = git_service.get_branch_commits(self.branch.name, self.main_branch, limit=20)

        parts = [
            _m(
                "[bold]Commits on $branch (not on $main)[/bold]\n\n",
                branch=self.branch.name,
                main=self.main_branch,
            )
        ]

        if commits:
            parts.extend(self._commit_lines(commits))
        else:
            parts.append(_m("[dim]No unique commits on this branch[/dim]"))

        return ScrollableContainer(Static(_join(parts)))

    def _build_divergence_tab(self) -> ScrollableContainer:
        """Build the divergence tab showing ahead/behind info."""
        git_service = self.keeper.git_service
        divergence = git_service.get_divergence_info(self.branch.name, self.main_branch)

        parts = [
            _m(
                "[bold]Branch Divergence vs $main[/bold]\n\n"
                "[bold]Ahead:[/bold] $ahead commits\n"
                "[bold]Behind:[/bold] $behind commits\n\n",
                main=self.main_branch,
                ahead=divergence["ahead"],
                behind=divergence["behind"],
            )
        ]

        def commit_line(commit: dict) -> Content:
            return _m(
                "  [cyan]$sha[/cyan] $date - $message\n",
                sha=commit["sha"],
                date=commit["date"],
                message=commit["message"],
            )

        if divergence["ahead_commits"]:
            parts.append(_m("[green bold]Commits ahead (on this branch):[/green bold]\n"))
            parts.extend(commit_line(commit) for commit in divergence["ahead_commits"])
            parts.append(Content("\n"))

        if divergence["behind_commits"]:
            parts.append(
                _m("[yellow bold]Commits behind (on $v):[/yellow bold]\n", v=self.main_branch)
            )
            parts.extend(commit_line(commit) for commit in divergence["behind_commits"])

        return ScrollableContainer(Static(_join(parts)))

    def _build_history_tab(self) -> ScrollableContainer:
        """Build the history tab for active branches."""
        git_service = self.keeper.git_service

        # For main branch, show recent commits on main (not unique comparison)
        # For feature branches, show commits unique to that branch
        if self.branch.name == self.main_branch:
            title = _m("[bold]Recent commits on $v[/bold]\n\n", v=self.main_branch)
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
                            "date": datetime.fromtimestamp(commit.committed_date, tz=UTC).strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                        }
                    )
            except GIT_ERRORS:
                commits = []
        else:
            # Show commits unique to feature branch
            commits = git_service.get_branch_commits(self.branch.name, self.main_branch, limit=10)
            title = _m("[bold]Commits unique to $v[/bold]\n\n", v=self.branch.name)

        parts = [title]

        if commits:
            parts.extend(self._commit_lines(commits))
        elif self.branch.name == self.main_branch:
            parts.append(_m("[dim]No commits found[/dim]"))
        else:
            parts.append(_m("[dim]No commits unique to this branch[/dim]"))

        return ScrollableContainer(Static(_join(parts)))

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
                    title = _m("[bold]Local vs Remote $v[/bold]\n\n", v=self.main_branch)
                    if diff:
                        content = title + Content(diff)
                    else:
                        content = title + _m("[dim]Local and remote are in sync[/dim]")
                except GIT_ERRORS:
                    content = _m("[dim]Cannot compare with remote - remote may not exist[/dim]")
            else:
                # For feature branches, show diff compared to main
                diff = repo.git.diff(f"{self.main_branch}...{self.branch.name}")
                title = _m("[bold]Changes compared to $v[/bold]\n\n", v=self.main_branch)
                if diff:
                    content = title + Content(diff)
                else:
                    content = title + _m("[dim]No differences with $v[/dim]", v=self.main_branch)
        except GIT_ERRORS as e:
            content = _m("[red]Error getting comparison: $v[/red]", v=e)

        return ScrollableContainer(Static(content))

    def on_mount(self) -> None:
        dialog = self.query_one("#tabbed-info-dialog", Vertical)
        dialog.border_title = self.branch.name
        dialog.border_subtitle = " esc = close "

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
