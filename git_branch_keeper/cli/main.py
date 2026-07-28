"""Command-line interface for git-branch-keeper"""

import json
import os
import sys
from typing import Any

from rich.console import Console

from git_branch_keeper.cli.args import parse_args
from git_branch_keeper.config import Config
from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.utils.logging import setup_logging

console = Console()


def _print_json(payload: dict[str, Any]) -> None:
    """Print a JSON payload to stdout for machine-readable CLI modes."""
    print(json.dumps(payload, indent=2, sort_keys=True))


def _json_error(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    """Build a structured JSON error payload."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def main():
    """Main entry point for the application."""
    parsed_args = None
    output_json = False
    try:
        # Parse command line arguments
        parsed_args = parse_args()
        output_json = parsed_args.json or parsed_args.output == "json"

        if parsed_args.command == "schema":
            from git_branch_keeper.formatters.json_output import schema_to_dict

            _print_json(schema_to_dict())
            return 0

        # Handle the 'undo' subcommand before any branch processing.
        # JSON mode must never fall back to human text.
        if parsed_args.command == "undo":
            if output_json:
                _print_json(
                    _json_error(
                        "JSON_UNSUPPORTED_FOR_COMMAND",
                        "JSON output is not yet implemented for the undo command",
                    )
                )
                return 2

            from git_branch_keeper.cli.undo import run_undo

            setup_logging(verbose=parsed_args.verbose, debug=parsed_args.debug, tui_mode=False)
            return run_undo(
                os.getcwd(),
                target=parsed_args.target,
                list_only=parsed_args.list,
                force=parsed_args.force,
            )

        # Build config from parsed arguments.
        # CLI mode is read-only by default. Cleanup/deletion is only enabled by
        # --delete, deprecated --cleanup, legacy --force, or --dry-run preview.
        # JSON output is an agent-friendly read-only scan mode, so force dry_run.
        delete_requested = parsed_args.delete or parsed_args.cleanup or parsed_args.force
        cleanup_enabled = delete_requested or parsed_args.dry_run

        config = Config(
            interactive=not parsed_args.force,
            dry_run=True if output_json else parsed_args.dry_run,
            force=parsed_args.force,
            delete_remote=parsed_args.remote,
            verbose=parsed_args.verbose,
            stale_days=parsed_args.stale_days,
            protected_branches=parsed_args.protected,
            ignore_patterns=parsed_args.ignore,
            status_filter=parsed_args.filter,
            include_remote_branches=not parsed_args.no_remote_branches,
            main_branch=parsed_args.main_branch,
            debug=parsed_args.debug,
            sort_by=parsed_args.sort_by,
            sort_order=parsed_args.sort_order,
            refresh=parsed_args.refresh,
            sequential=parsed_args.sequential,
            workers=parsed_args.workers,
        )

        # Determine if we should use interactive mode.
        # JSON output is always non-interactive to keep stdout machine-readable.
        cli_mode_requested = parsed_args.cli or parsed_args.no_interactive
        use_interactive = (
            False
            if output_json
            else parsed_args.interactive or (sys.stdin.isatty() and not cli_mode_requested)
        )

        # Setup logging after determining mode. JSON output uses file logging so
        # stdout remains valid JSON and human log messages do not leak into it.
        setup_logging(
            verbose=parsed_args.verbose,
            debug=parsed_args.debug,
            tui_mode=use_interactive or output_json,
        )

        if parsed_args.debug and not output_json:
            console.print("[yellow]Debug mode enabled[/yellow]")

            # Show threading information
            from git_branch_keeper.utils.threading import get_threading_info

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
            console.print(
                "[dim]Note: Debug mode forces sequential processing for readable logs[/dim]"
            )

        # Initialize BranchKeeper with repo_path and config.
        # Suppress Rich console output in TUI and JSON modes.
        keeper = BranchKeeper(os.getcwd(), config, tui_mode=use_interactive or output_json)

        if output_json:
            from git_branch_keeper.formatters.json_output import analysis_to_dict

            analysis = keeper.analyze_branches(show_progress=False)
            _print_json(analysis_to_dict(keeper, analysis))
            return 0

        # Check if interactive mode should be used
        if use_interactive:
            # Launch interactive TUI mode immediately
            # TUI will load data in background with status-bar progress
            from git_branch_keeper.ui import BranchKeeperApp

            # TUI keeps its historical behavior: auto-mark recommended cleanup
            # candidates by default, while --dry-run disables auto-marking.
            app = BranchKeeperApp(keeper, cleanup_mode=not parsed_args.dry_run)
            app.run()
        else:
            # Normal CLI mode is read-only unless cleanup/deletion (or dry-run preview)
            # was explicitly requested.
            keeper.process_branches(cleanup_enabled=cleanup_enabled)

        return 0
    except KeyboardInterrupt:
        if output_json:
            _print_json(_json_error("OPERATION_CANCELLED", "Operation cancelled by user"))
        else:
            console.print("\n[yellow]Operation cancelled by user[/yellow]")
        return 1
    except RuntimeError as e:
        # RuntimeError is used for expected errors (like unavailable GitHub auth)
        if output_json:
            _print_json(_json_error("RUNTIME_ERROR", str(e)))
        else:
            # Display the error message without full stack trace
            console.print(f"\n[red]{e}[/red]\n")
        return 1
    except Exception as e:  # noqa: BLE001 - CLI entry point must fail closed
        # Deliberately broad: this is the outermost handler for the whole run.
        # Anything reaching here is unexpected; show a clean message (with
        # --debug for the full traceback) instead of crashing with a raw one.
        if output_json:
            _print_json(_json_error("UNEXPECTED_ERROR", str(e)))
        else:
            console.print(f"[red]Error: {e}[/red]")
            if parsed_args and parsed_args.debug:
                console.print_exception()
        return 1


if __name__ == "__main__":
    sys.exit(main())
