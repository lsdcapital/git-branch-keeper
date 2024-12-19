"""Display and formatting service for branch information"""
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from typing import List, Dict, Optional
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
import git

console = Console()

class DisplayService:
    def __init__(self, verbose: bool = False, debug: bool = False):
        self.verbose = verbose
        self.debug_mode = debug
        self.repo = None  # Will be set when display_branch_table is called
        self.branch_status_service = None  # Will be set when display_branch_table is called

    def display_branch_table(
            self,
            branch_details: List[BranchDetails],
            repo: git.Repo,
            github_base_url: Optional[str],
            branch_status_service: 'BranchStatusService',
            show_summary: bool = False
        ) -> None:
        """Display a table of branch information."""
        self.repo = repo
        self.branch_status_service = branch_status_service
        table = Table()
        
        # Add columns
        table.add_column("Branch")
        table.add_column("Last Commit")
        table.add_column("Age (days)")
        table.add_column("Status")
        table.add_column("Sync")
        table.add_column("Remote")
        table.add_column("PRs")
        table.add_column("Notes") # Added Notes column

        # Add rows
        for branch in branch_details:
            row_style = self._get_row_style(branch)
            
            # Format the last commit date (date only)
            last_commit_date = branch.last_commit_date
            if hasattr(last_commit_date, 'strftime'):
                last_commit_date = last_commit_date.strftime("%Y-%m-%d")
            
            remote_status = "✓" if branch.has_remote else "✗"
            
            # Format branch name with link if GitHub is enabled
            branch_name = branch.name + (" *" if branch.name == repo.active_branch.name else "")
            if github_base_url:
                branch_name = f"[link={github_base_url}/tree/{branch.name}]{branch_name}[/link]"
            
            # Format status with more descriptive text
            status_text = self._format_status(branch.status)
            
            # Format PR status with link if GitHub is enabled
            pr_display = branch.pr_status
            if github_base_url and pr_display:
                if pr_display.startswith('target:'):
                    # For main branch, show PRs targeting it and add overview link
                    count = pr_display.split(':')[1]
                    pr_display = f"[link={github_base_url}/pulls]{count}[/link]"
                else:
                    # For other branches, link to the specific PR
                    pr_display = f"[link={github_base_url}/pull/{pr_display}]{pr_display}[/link]"
            
            table.add_row(
                branch_name,
                last_commit_date,
                str(branch.age_days),
                status_text,
                branch.sync_status,
                remote_status,
                pr_display if pr_display else "",
                branch.notes if branch.notes else "", # Added notes
                style=row_style
            )

        console.print(table)
        
        if show_summary:
            # Display legend
            console.print("\nLegend:")
            console.print("✓ = Has remote branch     ✗ = Local only")
            console.print("↑ = Unpushed commits      ↓ = Commits to pull")
            console.print("* = Current branch        +M = Modified files")
            console.print("+U = Untracked files      +S = Staged files")
            console.print("Yellow = Would be cleaned up")
            console.print("Cyan = Protected branch")
            
            # Show branches that would be deleted
            branches_to_delete = [
                branch for branch in branch_details 
                if branch.status in [BranchStatus.STALE, BranchStatus.MERGED]
                and branch.name not in self.branch_status_service.config.get('protected_branches', [])
            ]
            
            if branches_to_delete:
                console.print("\nBranches that would be deleted:")
                for branch in branches_to_delete:
                    reason = "stale" if branch.status == BranchStatus.STALE else "merged"
                    remote_info = "remote and local" if branch.has_remote else "local only"
                    console.print(f"  {branch.name} ({reason}, {remote_info})")
            
            # Calculate summary statistics
            total_branches = len(branch_details)
            active_branches = sum(1 for b in branch_details if b.status == BranchStatus.ACTIVE)
            stale_branches = sum(1 for b in branch_details if b.status == BranchStatus.STALE)
            merged_branches = sum(1 for b in branch_details if b.status == BranchStatus.MERGED)
            
            # Display summary
            console.print("\nSummary:")
            console.print(f"Total branches: {total_branches}")
            console.print(f"Active branches: {active_branches}")
            console.print(f"Stale branches: {stale_branches}")
            console.print(f"Merged branches: {merged_branches}")

            # Display merge detection stats
            merge_stats = self.branch_status_service.git_service.get_merge_stats()
            if merge_stats != "No merges detected":
                console.print("\nMerge Detection Stats:")
                console.print(merge_stats)

    def print_progress(self, total: int, description: str = "Processing branches..."):
        """Create and return a progress bar."""
        progress = Progress()
        task = progress.add_task(description, total=total)
        return progress, task

    def debug(self, message: str, source: str = "Display") -> None:
        """Print debug message if debug mode is enabled."""
        if self.debug_mode:
            print(f"[{source}] {message}")

    def _format_status(self, status: BranchStatus) -> str:
        """Format branch status with descriptive text."""
        if status == BranchStatus.ACTIVE:
            return "active"
        elif status == BranchStatus.STALE:
            return "stale"
        elif status == BranchStatus.MERGED:
            return "merged"
        return str(status.value)

    def _get_row_style(self, branch: BranchDetails) -> Optional[str]:
        """Get the style for a table row based on branch status."""
        if self.branch_status_service and branch.name in self.branch_status_service.config.get('protected_branches', ['main', 'master']):
            return "cyan"
        if branch.status == BranchStatus.STALE or branch.status == BranchStatus.MERGED:
            return "yellow"
        return None