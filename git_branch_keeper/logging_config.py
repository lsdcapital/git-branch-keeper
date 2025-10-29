"""Logging configuration for git-branch-keeper"""
import logging
import sys


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


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """
    Configure logging for the application.

    Args:
        verbose: If True, show INFO level messages
        debug: If True, show DEBUG level messages and detailed formatting
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
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
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
