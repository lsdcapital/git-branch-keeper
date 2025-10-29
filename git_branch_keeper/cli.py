"""Command-line interface for git-branch-keeper"""

import os
import sys
from rich.console import Console
from .args import parse_args
from .core import BranchKeeper
from .logging_config import setup_logging
from .config import Config

console = Console()

def main():
    """Main entry point for the application."""
    try:
        # Parse command line arguments
        parsed_args = parse_args()

        # Setup logging before creating BranchKeeper
        setup_logging(verbose=parsed_args.verbose, debug=parsed_args.debug)

        # Build config from parsed arguments
        config = Config(
            interactive=not parsed_args.force,
            dry_run=not parsed_args.cleanup,
            force=parsed_args.force,
            verbose=parsed_args.verbose,
            stale_days=parsed_args.stale_days,
            protected_branches=parsed_args.protected,
            ignore_patterns=parsed_args.ignore,
            status_filter=parsed_args.filter,
            main_branch=parsed_args.main_branch,
            debug=parsed_args.debug,
            sort_by=parsed_args.sort_by,
            sort_order=parsed_args.sort_order,
            refresh=parsed_args.refresh,
            sequential=parsed_args.sequential,
            workers=parsed_args.workers
        )
        
        if parsed_args.debug:
            console.print("[yellow]Debug mode enabled[/yellow]")

            # Show threading information
            from git_branch_keeper.threading_utils import get_threading_info
            threading_info = get_threading_info()
            console.print("[yellow]Threading Information:[/yellow]")
            console.print(f"  Python version: {threading_info['python_version']}")
            console.print(f"  Threading mode: {threading_info['mode']}")
            console.print(f"  CPU count: {threading_info['cpu_count']}")
            console.print(f"  Optimal workers: {threading_info['optimal_workers']}")
            console.print(f"  Free-threading enabled: {threading_info['free_threading']}")

            console.print("[yellow]Configuration:[/yellow]")
            for key, value in config.to_dict().items():
                console.print(f"  {key}: {value}")
            console.print("[dim]Note: Debug mode forces sequential processing for readable logs[/dim]")
        
        # Initialize BranchKeeper with repo_path and config
        keeper = BranchKeeper(os.getcwd(), config)
        
        # Process branches
        keeper.process_branches(cleanup_enabled=parsed_args.cleanup)
        
        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        return 1
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if parsed_args.debug:
            console.print_exception()
        return 1


if __name__ == "__main__":
    sys.exit(main())
