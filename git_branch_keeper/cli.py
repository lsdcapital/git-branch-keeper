"""Command-line interface for git-branch-keeper"""

import argparse
import sys
from typing import Optional, List

from . import __version__
from .core import BranchKeeper
from .config import load_config


def create_parser() -> argparse.ArgumentParser:
    """Create the command line argument parser."""
    parser = argparse.ArgumentParser(
        description="A smart Git branch management tool that helps keep your repository clean and organized."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-branch-keeper {__version__}"
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to config file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force deletion without confirmation (use with caution)"
    )
    parser.add_argument(
        "--status",
        choices=["all", "merged", "stale"],
        default="all",
        help="Filter branches by status"
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=30,
        help="Number of days before a branch is considered stale"
    )
    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Main entry point for the command line interface."""
    if args is None:
        args = sys.argv[1:]

    parser = create_parser()
    parsed_args = parser.parse_args(args)

    # Load configuration
    config = load_config(parsed_args.config)

    # Create and run the branch keeper
    keeper = BranchKeeper(
        interactive=not parsed_args.force,
        dry_run=parsed_args.dry_run,
        verbose=parsed_args.verbose,
        stale_days=parsed_args.stale_days,
        config=config,
        force_mode=parsed_args.force,
        status_filter=parsed_args.status
    )

    try:
        keeper.process_branches()
        keeper.print_summary()
        return 0
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
