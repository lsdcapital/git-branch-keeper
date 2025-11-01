"""Custom widgets for git-branch-keeper TUI."""

from textual.app import ComposeResult, RenderResult
from textual.events import Click
from textual.widgets import Header
from textual.widgets._header import HeaderIcon, HeaderTitle, HeaderClockSpace
from rich.text import Text

from git_branch_keeper.__version__ import __version__


class VersionDisplay(HeaderClockSpace):
    """Custom widget to display version in place of clock."""

    DEFAULT_CSS = """
    VersionDisplay {
        width: auto;
        dock: right;
        padding: 0 1;
        background: $foreground 5%;
        color: $text;
        text-align: center;
        text-opacity: 85%;
    }
    """

    def render(self) -> RenderResult:
        """Render the version string."""
        return Text(f"v{__version__}")


class NonExpandingHeader(Header):
    """Header widget that doesn't expand/contract on click and shows version instead of clock."""

    def compose(self) -> ComposeResult:
        """Compose the header with custom version display."""
        yield HeaderIcon().data_bind(Header.icon)
        yield HeaderTitle()
        yield VersionDisplay() if self._show_clock else HeaderClockSpace()

    def on_click(self, event: Click) -> None:
        """Override to disable click-to-expand behavior."""
        event.stop()  # Stop event propagation to prevent default toggle behavior
