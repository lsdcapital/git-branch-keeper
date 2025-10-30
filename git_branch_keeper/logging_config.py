"""Logging configuration for git-branch-keeper"""
import logging
import sys
from pathlib import Path


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels in terminal output."""

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }

    def format(self, record):
        """Format log record with colors if in a terminal."""
        if sys.stderr.isatty():
            levelname = record.levelname
            if levelname in self.COLORS:
                record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
        return super().format(record)


def setup_logging(verbose: bool = False, debug: bool = False, tui_mode: bool = False) -> None:
    """
    Configure logging for the application.

    Args:
        verbose: If True, show INFO level messages
        debug: If True, show DEBUG level messages and detailed formatting
        tui_mode: If True, always log to file (since TUI hides console output)
    """
    # Determine log level
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    # Configure root logger
    root_logger = logging.getLogger()
    # In TUI mode, always use DEBUG level for root logger so file gets all messages
    # Individual handlers control what actually gets written/displayed
    if tui_mode:
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Always add file handler in TUI mode or debug mode
    if tui_mode or debug:
        log_dir = Path.home() / '.git-branch-keeper'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / 'git-branch-keeper.log'
        file_handler = logging.FileHandler(log_file, mode='w')  # Overwrite each run
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file
        file_formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Create console handler (only if not TUI mode)
    if not tui_mode:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)

        # Set formatter based on mode
        if debug:
            # Detailed format for debug mode
            formatter = ColoredFormatter(
                fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
        else:
            # Simple format for normal/verbose mode
            formatter = ColoredFormatter(
                fmt='[%(name)s] %(message)s'
            )

        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for the specified module.

    Args:
        name: Name of the module (typically __name__)

    Returns:
        Logger instance
    """
    # Strip the package prefix for cleaner log names
    if name.startswith('git_branch_keeper.'):
        name = name.replace('git_branch_keeper.', '')
    if name.startswith('services.'):
        name = name.replace('services.', '')

    return logging.getLogger(name)
