"""Display and formatting service for branch information"""
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from typing import List, Dict, Optional
from git_branch_keeper.models.branch import BranchDetails, BranchStatus

console = Console()

class DisplayService:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.repo = None  # Will be set when display_branch_table is called
        self.branch_status_service = None  # Will be set when display_branch_table is called

    def display_branch_table(self, branches_data: List[Dict] | List[BranchDetails], repo=None, github_base_url: Optional[str] = None, branch_status_service=None) -> List[str]:
        """Display branch information in a table format and return branches to process."""
        self.repo = repo
        self.branch_status_service = branch_status_service
        
        # Filter branches based on status_filter
        status_filter = self.branch_status_service.config.get('status_filter') if self.branch_status_service else None
        if status_filter and status_filter != 'all':
            filtered_data = []
            for branch in branches_data:
                if isinstance(branch, dict):
                    if branch['status'] == status_filter:
                        filtered_data.append(branch)
                else:  # BranchDetails
                    if branch.status.value == status_filter:
                        filtered_data.append(branch)
            branches_data = filtered_data
        
        if not branches_data:
            console.print("No branches match the filter criteria")
            return []
        
        print("")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Branch")
        table.add_column("Last Commit Date")
        table.add_column("Age (days)", justify="right")
        table.add_column("Status")
        table.add_column("Remote", justify="center")
        table.add_column("Sync Status")
        table.add_column("PRs")

        branches_to_process = []

        # Handle both Dict and BranchDetails inputs
        for branch in branches_data:
            if isinstance(branch, dict):
                # Handle dictionary input
                branch_name = branch['name']
                status = branch['status']
                age_days = branch['age_days']
                has_remote = branch['has_remote']
                sync_status = branch['sync_status']
                pr_count = branch['pr_count']
                would_clean = branch['would_clean']
                is_current = branch['is_current']
                last_commit_date = branch['last_commit_date']
                pr_display = ""
                if pr_count > 0 and github_base_url:
                    pr_url = f"{github_base_url}/pulls?q=is%3Apr+head%3A{branch_name}"
                    if branch_name in ['main', 'master']:
                        pr_url = f"{github_base_url}/pulls"
                    pr_display = f"[link={pr_url}]{pr_count}[/link]"
                elif branch.get('github_disabled'):
                    pr_display = "disabled"
            else:
                # Handle BranchDetails input
                branch_name = branch.name
                status = branch.status.value
                age_days = branch.age_days
                has_remote = branch.has_remote
                sync_status = branch.sync_status
                would_clean = False
                is_current = self.repo and branch_name == self.repo.active_branch.name
                last_commit_date = branch.last_commit_date
                pr_display = branch.pr_status or ""
                
                # Check if branch should be cleaned up
                if self.branch_status_service:
                    would_clean, _ = self.branch_status_service.should_process_branch(
                        branch_name,
                        branch.status,
                        self.repo.active_branch.name if self.repo else "main"
                    )
                    if pr_display.isdigit() and github_base_url:
                        pr_url = f"{github_base_url}/pulls?q=is%3Apr+head%3A{branch_name}"
                        if branch_name in ['main', 'master']:
                            pr_url = f"{github_base_url}/pulls"
                        pr_display = f"[link={pr_url}]{pr_display}[/link]"

            # Create branch name with link if it has a remote
            branch_display = branch_name
            if github_base_url and has_remote:
                branch_url = f"{github_base_url}/tree/{branch_name}"
                branch_display = f"[link={branch_url}]{branch_name}[/link]"

            # Add row to table with conditional styling
            row_style = None
            if would_clean:
                row_style = "yellow"
            elif self.branch_status_service and branch_name in self.branch_status_service.config.get('protected_branches', ['main', 'master']):
                row_style = "cyan"

            table.add_row(
                branch_display + (" *" if is_current else ""),
                last_commit_date,
                str(age_days),
                status,
                "✔️" if has_remote else "✗",
                sync_status,
                pr_display,
                style=row_style
            )

            if would_clean:
                branches_to_process.append(branch_name)

        # Print the table
        console.print(table)
        
        # Only show legend and summary in verbose mode
        if self.verbose:
            # Print legend with aligned columns using fixed width
            console.print("\nLegend:")
            console.print("✔️ = Has remote branch     ✗ = Local only")
            console.print("↑ = Unpushed commits      ↓ = Commits to pull")
            console.print("* = Current branch        +M = Modified files")
            console.print("+U = Untracked files      +S = Staged files")
            console.print("Yellow = Would be cleaned up")
            console.print("Cyan = Protected branch")
            
            # Print summary of branch statuses
            if isinstance(branches_data[0], BranchDetails):
                active_count = sum(1 for b in branches_data if b.status == BranchStatus.ACTIVE)
                stale_count = sum(1 for b in branches_data if b.status == BranchStatus.STALE)
                merged_count = sum(1 for b in branches_data if b.status == BranchStatus.MERGED)
            else:
                active_count = sum(1 for b in branches_data if b['status'] == 'active')
                stale_count = sum(1 for b in branches_data if b['status'] == 'stale')
                merged_count = sum(1 for b in branches_data if b['status'] == 'merged')
            
            console.print("\nSummary:")
            console.print(f"Active branches: {active_count}")
            console.print(f"Stale branches: {stale_count}")
            console.print(f"Merged branches: {merged_count}")
            console.print(f"Total branches: {len(branches_data)}")
            
            # Print merge detection stats if available
            if self.branch_status_service and hasattr(self.branch_status_service.git_service, 'merge_detection_stats'):
                console.print("\nMerge Detection Stats:")
                stats = self.branch_status_service.git_service.get_merge_stats()
                console.print(stats)
        
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