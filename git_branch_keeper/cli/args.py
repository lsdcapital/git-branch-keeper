"""Command-line argument parsing for git-branch-keeper."""

import argparse

from git_branch_keeper.__version__ import __version__


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Git branch management tool for any Git repository (GitHub, GitLab, Bitbucket, or local)",
        epilog="GitHub Integration (OPTIONAL): Set GITHUB_TOKEN/GH_TOKEN, configure github_token, "
        "or run `gh auth login` to enable PR detection and protection. "
        "Get token at https://github.com/settings/tokens (scopes: repo or public_repo). "
        "Tool works without GitHub auth for basic branch management. "
        "SAFETY: CLI mode is read-only unless --delete/--cleanup/--force is passed. "
        "Use --dry-run to preview cleanup candidates.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["undo", "schema"],
        help="Optional subcommand: 'undo' restores recently deleted branches; 'schema' prints machine-readable schemas",
    )
    parser.add_argument(
        "target",
        nargs="?",
        metavar="BRANCH",
        help="With 'undo': branch name to restore (default: most recent deletion)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="With 'undo': list recent deletions for this repository",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output")
    parser.add_argument("--version", action="version", version=f"git-branch-keeper {__version__}")
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format. Use json for machine-readable, read-only scan results.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Shortcut for --output json (machine-readable, read-only scan results)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview cleanup candidates without making changes",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete/cleanup eligible branches in CLI mode (asks for confirmation unless --force is used)",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Deprecated alias for --delete",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="[DANGEROUS] In CLI mode, delete without confirmation (legacy: implies --delete)",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Also delete the remote branch (default: local-only, remote is kept). "
        "Remote deletions affect collaborators and are harder to undo.",
    )
    parser.add_argument(
        "--interactive", action="store_true", help="Launch interactive TUI mode (default for TTY)"
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Force plain CLI mode (read-only unless --delete, --cleanup, --force, or --dry-run is used)",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Alias for --cli",
    )
    parser.add_argument("--stale-days", type=int, default=30, help="Days until branch is stale")
    parser.add_argument(
        "--protected", nargs="*", default=["main", "master"], help="Protected branches"
    )
    parser.add_argument("--ignore", nargs="*", default=[], help="Branch patterns to ignore")
    parser.add_argument(
        "--filter",
        choices=["all", "stale", "merged", "unstarted"],
        default="all",
        help="Filter which branches to show and process (all/stale/merged/unstarted)",
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
