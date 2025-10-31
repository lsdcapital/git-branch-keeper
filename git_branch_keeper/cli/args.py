"""Command-line argument parsing for git-branch-keeper."""

import argparse
from git_branch_keeper.__version__ import __version__


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Git branch management tool for GitHub repositories",
        epilog="Setup: Requires GITHUB_TOKEN environment variable or 'github_token' in config file. "
        "Get a token at https://github.com/settings/tokens (scopes: repo or public_repo)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output")
    parser.add_argument("--version", action="version", version=f"git-branch-keeper {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode - show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="(Deprecated: cleanup is now default) Actually delete branches",
    )
    parser.add_argument("--force", action="store_true", help="Skip confirmations")
    parser.add_argument(
        "--interactive", action="store_true", help="Launch interactive TUI mode (default for TTY)"
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Force non-interactive CLI mode (for scripts/automation)",
    )
    parser.add_argument("--stale-days", type=int, default=30, help="Days until branch is stale")
    parser.add_argument(
        "--protected", nargs="*", default=["main", "master"], help="Protected branches"
    )
    parser.add_argument("--ignore", nargs="*", default=[], help="Branch patterns to ignore")
    parser.add_argument(
        "--filter",
        choices=["all", "stale", "merged"],
        default="all",
        help="Filter which branches to show and process (all/stale/merged)",
    )
    parser.add_argument("--main-branch", default="main", help="Main branch name")
    parser.add_argument(
        "--debug", action="store_true", help="Show debug information for troubleshooting"
    )
    parser.add_argument(
        "--sort-by",
        choices=["name", "age", "date", "status"],
        default="age",
        help="Sort branches by name, age, date, or status (default: age)",
    )
    parser.add_argument(
        "--sort-order",
        choices=["asc", "desc"],
        default="asc",
        help="Sort order: ascending or descending (default: asc)",
    )
    parser.add_argument("--refresh", action="store_true", help="Force refresh and bypass cache")
    parser.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help="Number of parallel workers for branch processing (default: auto-detect based on CPU and threading mode)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Force sequential processing (disable parallelism)",
    )

    return parser.parse_args()
