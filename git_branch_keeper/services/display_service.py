"""Display and formatting service for branch information"""
from rich.console import Console
from rich.table import Table
from typing import List, Optional, TYPE_CHECKING
from git_branch_keeper.models.branch import BranchDetails, BranchStatus
from git_branch_keeper.logging_config import get_logger
from git_branch_keeper.constants import COLUMNS, CLI_COLORS
from git_branch_keeper.formatters import (
    format_date,
    format_remote_status,
    format_status,
    format_changes,
    format_pr_link,
    format_branch_link,
    format_deletion_reason,
    get_branch_style_type,
)
from git_branch_keeper.services.branch_validation_service import BranchValidationService
import git

if TYPE_CHECKING:
    from git_branch_keeper.services.branch_status_service import BranchStatusService

console = Console()
logger = get_logger(__name__)

class DisplayService:
    def __init__(self, verbose: bool = False, debug: bool = False):
        self.verbose = verbose
        self.debug_mode = debug
        self.repo: Optional[git.Repo] = None  # Will be set when display_branch_table is called
        self.branch_status_service: Optional['BranchStatusService'] = None  # Will be set when display_branch_table is called

    def display_branch_table(
            self,
            branch_details: List[BranchDetails],
            repo: git.Repo,
            github_base_url: Optional[str],
            branch_status_service: 'BranchStatusService',
            protected_branches: List[str],
            show_summary: bool = False
        ) -> None:
        """Display a table of branch information."""
        self.repo = repo
        self.branch_status_service = branch_status_service
        table = Table()

        # Add columns using shared constants
        for col in COLUMNS:
            table.add_column(col.label)

        # Add rows
        for branch in branch_details:
            # Get row style using shared formatter
            style_type = get_branch_style_type(branch, protected_branches)
            row_style = CLI_COLORS.get(style_type)

            # Format fields using shared formatters
            try:
                current_branch_name = repo.active_branch.name
            except TypeError:
                current_branch_name = None  # Detached HEAD
            is_current = branch.name == current_branch_name if current_branch_name else False
            branch_name = format_branch_link(branch.name, github_base_url, is_current)
            last_commit_date = format_date(branch.last_commit_date)
            remote_status = format_remote_status(branch.has_remote)
            status_text = format_status(branch.status)
            changes_indicator = format_changes(branch, current_branch_name)
            pr_display = format_pr_link(branch.pr_status, github_base_url)

            # Match COLUMNS order: Branch, Status, Last Commit, Age, Changes, Sync, Remote, PRs, Notes
            table.add_row(
                branch_name,
                status_text,
                last_commit_date,
                str(branch.age_days),
                changes_indicator,
                branch.sync_status,
                remote_status,
                pr_display,
                branch.notes if branch.notes else "",
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
                if BranchValidationService.is_deletable(branch, protected_branches)
            ]
            
            if branches_to_delete:
                console.print("\nBranches that would be deleted:")
                for branch in branches_to_delete:
                    reason = format_deletion_reason(branch.status)
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