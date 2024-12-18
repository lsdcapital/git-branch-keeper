"""Display and formatting service for branch information"""
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from typing import List, Dict, Optional

console = Console()

class DisplayService:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def print_branch_table(self, branches_data: List[Dict], github_base_url: Optional[str] = None) -> List[str]:
        """Print table of branches with their status and return branches to process."""
        print("")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Branch", style="cyan")
        table.add_column("Last Commit Date")
        table.add_column("Age (days)")
        table.add_column("Status")
        table.add_column("Remote")
        table.add_column("Sync Status")
        table.add_column("PRs")

        branches_to_process = []

        for branch_data in branches_data:
            branch_name = branch_data['name']
            status = branch_data['status']
            age_days = branch_data['age_days']
            has_remote = branch_data['has_remote']
            sync_status = branch_data['sync_status']
            pr_count = branch_data['pr_count']
            would_clean = branch_data['would_clean']
            is_current = branch_data['is_current']

            # Create branch name with link if it has a remote
            branch_display = branch_name
            if github_base_url and has_remote:
                branch_url = f"{github_base_url}/tree/{branch_name}"
                branch_display = f"[blue][link={branch_url}]{branch_name}[/link][/blue]"

            # Create PR count with link if there are PRs
            pr_display = ""
            if pr_count > 0 and github_base_url:
                pr_url = f"{github_base_url}/pulls?q=is:pr+is:open+head:{branch_name}"
                pr_display = f"[blue][link={pr_url}]{pr_count}[/link][/blue]"
            elif branch_data.get('github_disabled'):
                pr_display = "[dim]disabled[/dim]"

            # Add row to table with conditional styling
            row_style = "yellow" if would_clean else None
            table.add_row(
                branch_display + (" *" if is_current else ""),
                branch_data['last_commit_date'],
                str(age_days),
                status,
                "✓" if has_remote else "✗",
                sync_status,
                pr_display,
                style=row_style
            )

            if would_clean:
                branches_to_process.append(branch_name)

        # Print the table
        console.print(table)
        
        # Print legend
        console.print("\n✓ = Has remote branch  ✗ = Local only")
        console.print("↑ = Unpushed commits  ↓ = Commits to pull")
        console.print("* = Current branch  Yellow = Would be cleaned up")
        console.print(f"\nBranches older than {branch_data['stale_days']} days are marked as stale\n")
        
        return branches_to_process

    def print_progress(self, total: int, description: str = "Processing branches..."):
        """Create and return a progress bar."""
        progress = Progress()
        task = progress.add_task(description, total=total)
        return progress, task

    def debug(self, message: str) -> None:
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            print(f"[Display] {message}") 