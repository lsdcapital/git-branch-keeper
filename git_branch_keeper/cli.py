"""Command-line interface for git-branch-keeper"""

import os
import sys
from rich.console import Console
from .args import parse_args
from .core import BranchKeeper

console = Console()

def main():
    """Main entry point for the application."""
    try:
        # Parse command line arguments
        parsed_args = parse_args()
        
        # Build config from parsed arguments
        config = {
            'interactive': not parsed_args.force,
            'dry_run': not parsed_args.cleanup,
            'force': parsed_args.force,
            'verbose': parsed_args.verbose,
            'stale_days': parsed_args.stale_days,
            'protected_branches': parsed_args.protected,
            'ignore_patterns': parsed_args.ignore,
            'status_filter': parsed_args.filter,
            'bypass_github': parsed_args.bypass_github,
            'main_branch': parsed_args.main_branch,
            'show_filter': parsed_args.show
        }
        
        if parsed_args.verbose:
            console.print("[yellow]Verbose mode enabled[/yellow]")
            console.print(f"[yellow]Configuration:[/yellow]")
            for key, value in config.items():
                console.print(f"  {key}: {value}")
        
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
        if parsed_args.verbose:
            console.print_exception()
        return 1


if __name__ == "__main__":
    sys.exit(main())
