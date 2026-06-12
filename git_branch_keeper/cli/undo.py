"""Undo command - restore branches recorded in the deletion journal."""

from typing import Dict, List, Optional

import git
from rich.console import Console
from rich.table import Table

from git_branch_keeper.services.deletion_journal import DeletionJournal
from git_branch_keeper.services.undo_service import pick_entry, restore_entry
from git_branch_keeper.utils.logging import get_logger

console = Console()
logger = get_logger(__name__)


def _print_deletion_list(deletions: List[Dict]) -> None:
    table = Table(title="Recent deletions (most recent first)")
    table.add_column("When")
    table.add_column("Branch")
    table.add_column("SHA")
    table.add_column("Remote deleted")
    for entry in reversed(deletions[-20:]):
        table.add_row(
            entry.get("timestamp", "?"),
            entry["branch"],
            entry["sha"][:12],
            "yes" if entry.get("remote_deleted") else "no",
        )
    console.print(table)


def run_undo(
    repo_path: str, target: Optional[str] = None, list_only: bool = False, force: bool = False
) -> int:
    """Entry point for `git-branch-keeper undo`.

    Args:
        repo_path: Repository to restore branches in
        target: Specific branch name to restore (default: most recent deletion)
        list_only: Just show recent deletions, restore nothing
        force: Skip the confirmation prompt (never pushes to the remote)

    Returns:
        Process exit code
    """
    journal = DeletionJournal(repo_path)
    deletions = journal.deletions()

    if not deletions:
        console.print("[yellow]No recorded deletions for this repository.[/yellow]")
        return 1

    if list_only:
        _print_deletion_list(deletions)
        return 0

    try:
        repo = git.Repo(repo_path)
    except Exception as e:
        console.print(f"[red]Could not open repository: {e}[/red]")
        return 1

    entry = pick_entry(deletions, repo, target)
    if entry is None:
        if target:
            console.print(f"[yellow]No recorded deletion found for branch {target}.[/yellow]")
        else:
            console.print(
                "[yellow]All recorded deletions already exist as local branches - "
                "nothing to restore.[/yellow]"
            )
        console.print("[dim]Use 'git-branch-keeper undo --list' to see recent deletions.[/dim]")
        return 1

    branch_name = entry["branch"]
    sha = entry["sha"]
    console.print(
        f"Restore branch [bold]{branch_name}[/bold] at {sha[:12]} "
        f"(deleted {entry.get('timestamp', 'unknown time')})"
    )

    if not force:
        response = console.input("Proceed? [y/N] ")
        if response.lower() != "y":
            console.print("[yellow]Restore cancelled[/yellow]")
            return 1

    include_remote = False
    if entry.get("remote_deleted") and not force:
        response = console.input(
            f"The remote branch was also deleted. Push {branch_name} back to "
            f"{entry.get('remote', 'origin')}? [y/N] "
        )
        include_remote = response.lower() == "y"

    success, error = restore_entry(repo_path, entry, journal, include_remote=include_remote)
    if not success:
        console.print(f"[red]{error}[/red]")
        return 1

    console.print(f"[green]✓ Restored branch {branch_name} at {sha[:12]}[/green]")
    if entry.get("remote_deleted") and not include_remote:
        console.print(
            f"[dim]To restore the remote branch: "
            f"git push {entry.get('remote', 'origin')} {sha}:refs/heads/{branch_name}[/dim]"
        )
    return 0
