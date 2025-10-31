"""Custom widgets for git-branch-keeper TUI."""

from textual.events import Click
from textual.widgets import Header


class NonExpandingHeader(Header):
    """Header widget that doesn't expand/contract on click."""

    def on_click(self, event: Click) -> None:
        """Override to disable click-to-expand behavior."""
        event.stop()  # Stop event propagation to prevent default toggle behavior
