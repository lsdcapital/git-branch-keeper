"""Undo command - restore branches recorded in the deletion journal."""

from typing import Dict, List, Optional

import git
from rich.console import Console
from rich.table import Table

from git_branch_keeper.services.deletion_journal import DeletionJournal
from git_branch_keeper.services.undo_service import (
    pick_entry,
    pick_latest_batch,
    restore_entries,
    restore_entry,
)
from git_branch_keeper.utils.logging import get_logger

console = Console()
logger = get_logger(__name__)

__all__ = ["run_undo", "pick_entry", "restore_entry"]


def _print_deletion_list(deletions: List[Dict]) -> None:
    table = Table(title="Recent deletions (most recent first)")
    table.add_column("When")
    table.add_column("Branch")
    table.add_column("SHA")
    table.add_column("Batch")
    table.add_column("Remote deleted")
    for entry in reversed(deletions[-20:]):
        table.add_row(
            entry.get("timestamp", "?"),
            entry["branch"],
            entry["sha"][:12],
            str(entry.get("batch_id", "?"))[-12:],
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

    if target:
        entry = pick_entry(deletions, repo, target)
        if entry is None:
            console.print(f"[yellow]No recorded deletion found for branch {target}.[/yellow]")
            console.print("[dim]Use 'git-branch-keeper undo --list' to see recent deletions.[/dim]")
            return 1
        entries = [entry]
    else:
        entries = pick_latest_batch(deletions, repo)
        if not entries:
            console.print(
                "[yellow]All recorded deletions already exist as local branches - "
                "nothing to restore.[/yellow]"
            )
            console.print("[dim]Use 'git-branch-keeper undo --list' to see recent deletions.[/dim]")
            return 1

    batch_id = entries[0].get("batch_id")
    if len(entries) == 1:
        entry = entries[0]
        console.print(
            f"Restore branch [bold]{entry['branch']}[/bold] at {entry['sha'][:12]} "
            f"(deleted {entry.get('timestamp', 'unknown time')})"
        )
    else:
        console.print(
            f"Restore [bold]{len(entries)} branches[/bold] from deletion batch "
            f"[dim]{batch_id}[/dim]:"
        )
        for entry in entries:
            console.print(f"  • {entry['branch']} at {entry['sha'][:12]}")

    if not force:
        response = console.input("Proceed? [y/N] ")
        if response.lower() != "y":
            console.print("[yellow]Restore cancelled[/yellow]")
            return 1

    include_remote = False
    remote_deleted_entries = [entry for entry in entries if entry.get("remote_deleted")]
    if remote_deleted_entries and not force:
        response = console.input(
            "One or more remote branches were also deleted. Push restored remote "
            "branches back too? [y/N] "
        )
        include_remote = response.lower() == "y"

    restored, failed = restore_entries(repo_path, entries, journal, include_remote=include_remote)
    for branch_name in restored:
        console.print(f"[green]✓ Restored branch {branch_name}[/green]")

    if failed:
        console.print(f"[red]Failed to restore {len(failed)} branch(es):[/red]")
        for branch_name, error in failed:
            console.print(f"[red]  • {branch_name}: {error}[/red]")
        return 1

    if remote_deleted_entries and not include_remote:
        console.print("[dim]To restore remote branches manually:[/dim]")
        for entry in remote_deleted_entries:
            console.print(
                f"[dim]  git push {entry.get('remote', 'origin')} "
                f"{entry['sha']}:refs/heads/{entry['branch']}[/dim]"
            )
    return 0
