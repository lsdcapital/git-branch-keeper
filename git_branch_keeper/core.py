"""Core functionality for git-branch-keeper"""

import os
import signal
import sys
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import git
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from git_branch_keeper.models.branch import BranchStatus, SyncStatus, BranchDetails
from git_branch_keeper.services.github_service import GitHubService
from git_branch_keeper.services.git_service import GitService
from git_branch_keeper.services.display_service import DisplayService
from git_branch_keeper.services.branch_status_service import BranchStatusService

console = Console()

# Global flag to track if we're in the middle of a Git operation
in_git_operation = False

def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    if signum == signal.SIGINT:
        print()  # New line after ^C
        if in_git_operation:
            console.print("\n[yellow]Interrupted! Waiting for current Git operation to complete...[/yellow]")
        else:
            console.print("\n[yellow]Interrupted! Cleaning up...[/yellow]")
        sys.exit(1)

# Set up signal handlers
signal.signal(signal.SIGINT, signal_handler)

class BranchKeeper:
    """Main class for managing Git branches."""

    def __init__(self, repo_path: str, config: dict):
        """Initialize BranchKeeper."""
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get('verbose', False)
        self.debug_mode = config.get('debug', False)
        
        # Initialize repo first
        try:
            self.repo = git.Repo(self.repo_path)
        except Exception as e:
            raise Exception(f"Error initializing repository: {e}")

        # Get configuration values
        self.min_stale_days = config.get('stale_days', 30)
        self.protected_branches = config.get('protected_branches', ['main', 'master'])
        self.ignore_patterns = config.get('ignore_patterns', [])
        self.status_filter = config.get('status_filter', 'all')
        self.interactive = config.get('interactive', True)
        self.dry_run = config.get('dry_run', True)
        self.force_mode = config.get('force', False)
        self.main_branch = config.get('main_branch', 'main')

        # Initialize services
        self.github_service = GitHubService(self.repo, self.config)
        self.git_service = GitService(self.repo, self.config)
        
        # Setup GitHub integration
        try:
            remote_url = self.repo.remotes.origin.url
            if self.debug_mode:
                self.debug(f"Setting up GitHub API with remote: {remote_url}")
            self.github_service.setup_github_api(remote_url)
            
            # If GitHub is available but no token, show a helpful message
            if not self.github_service.github_enabled and 'github.com' in remote_url:
                console.print("[yellow]💡 Tip: Set up a GitHub token for better merge detection and PR status[/yellow]")
                console.print("[yellow]   See: git-branch-keeper --help or check the README for setup instructions[/yellow]")
                console.print("")
        except Exception as e:
            if self.debug_mode:
                self.debug(f"Failed to setup GitHub API: {e}")
        
        self.branch_status_service = BranchStatusService(
            self.repo,
            self.config,
            self.git_service,
            self.github_service,
            self.verbose
        )
        self.display_service = DisplayService(
            verbose=self.verbose,
            debug=self.debug_mode
        )

        # Initialize statistics
        self.stats = {
            "deleted": 0,
            "skipped_pr": 0,
            "skipped_protected": 0,
            "skipped_pattern": 0
        }

    def _setup_github_api(self):
        """Setup GitHub API access if possible."""
        try:
            remote_url = self.repo.remotes.origin.url
            self.debug(f"Remote URL: {remote_url}")
            self.github_service.setup_github_api(remote_url)
        except Exception as e:
            self.debug(f"Error setting up GitHub API: {e}")

    def delete_branch(self, branch_name: str, reason: str) -> bool:
        """Delete a branch or show what would be deleted in dry-run mode."""
        try:
            # Check for open PRs first
            if self.github_service.has_open_pr(branch_name):
                console.print(f"[yellow]Skipping {branch_name} - Has open PR[/yellow]")
                self.stats["skipped_pr"] += 1
                return False

            # Check for local changes
            status_details = self.git_service.get_branch_status_details(branch_name)
            if status_details['modified'] or status_details['untracked'] or status_details['staged']:
                warning = []
                if status_details['modified']:
                    warning.append("modified files")
                if status_details['untracked']:
                    warning.append("untracked files")
                if status_details['staged']:
                    warning.append("staged files")
                
                message = f"[yellow]Warning: {branch_name} has {', '.join(warning)}[/yellow]"
                if self.interactive:
                    console.print(message)
                    response = input(f"Still want to delete branch {branch_name}? [y/N] ")
                    if response.lower() != 'y':
                        return False
                else:
                    console.print(f"{message} - Skipping")
                    return False

            remote_exists = self.git_service.has_remote_branch(branch_name)
            if self.dry_run:
                if remote_exists:
                    console.print(f"Would delete local and remote branch {branch_name} ({reason})")
                else:
                    console.print(f"Would delete local branch {branch_name} ({reason})")
                return True

            # Cannot delete current branch
            if branch_name == self.repo.active_branch.name:
                console.print(f"[yellow]Cannot delete current branch {branch_name}[/yellow]")
                return False

            return self.git_service.delete_branch(branch_name, self.dry_run)

        except Exception as e:
            console.print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
            return False

    def process_branches(self, cleanup_enabled: bool = False) -> None:
        """Process all branches according to configuration."""
        try:
            # Check main branch status first
            main_sync_status = self.git_service.get_branch_sync_status(self.main_branch, self.main_branch)
            if "behind" in main_sync_status:
                console.print(f"[yellow]Warning: Your {self.main_branch} branch is {main_sync_status}[/yellow]")
                console.print(f"[yellow]Please update your {self.main_branch} branch first:[/yellow]")
                console.print(f"  git checkout {self.main_branch}")
                console.print(f"  git pull origin {self.main_branch}")
                console.print("")

            # Get all branches (excluding tags, stash, and remote refs)
            branches = [
                ref.name for ref in self.repo.refs 
                if not ref.name.startswith('origin/') 
                and not ref.name.startswith('refs/stash')
                and not self.git_service.is_tag(ref.name)
            ]
            
            # Only filter out ignored branches, keep protected ones
            branches = [b for b in branches if not self.branch_status_service.should_ignore_branch(b)]

            if not branches:
                console.print("No branches to process")
                return

            # Process branches first without PR data
            branch_details = []
            pr_data = {}
            
            # Process branches
            status_filter = self.config.get('status_filter', 'all')
            
            if self.verbose:
                console.print("Processing branches...")
                for branch in branches:
                    details = self._process_single_branch(branch, status_filter, pr_data, None)
                    if details:
                        branch_details.append(details)
            else:
                with Progress() as progress:
                    task = progress.add_task("Processing branches...", total=len(branches))
                    for branch in branches:
                        details = self._process_single_branch(branch, status_filter, pr_data, progress)
                        if details:
                            branch_details.append(details)
                        progress.update(task, advance=1)

            # Only fetch PR data for branches that need it
            if self.github_service.github_enabled:
                try:
                    # Check PRs for all branches
                    branches_to_check = [b.name for b in branch_details]
                    if branches_to_check:
                        if self.debug_mode:
                            self.debug(f"Fetching PR data for {len(branches_to_check)} branches")
                        pr_data = self.github_service.get_bulk_pr_data(branches_to_check)
                        
                        # Update branch statuses with PR data
                        for branch in branch_details:
                            if branch.name in pr_data:
                                if pr_data[branch.name]['count'] > 0:
                                    branch.status = BranchStatus.ACTIVE
                                    if branch.name == self.main_branch:
                                        # For main branch, show total PR count with a special indicator
                                        branch.pr_status = f"target:{pr_data[branch.name]['count']}"
                                    else:
                                        branch.pr_status = str(pr_data[branch.name]['count'])
                                elif pr_data[branch.name]['merged']:
                                    branch.status = BranchStatus.MERGED
                                elif pr_data[branch.name]['closed']:
                                    branch.notes = "PR closed without merging"
                except Exception as e:
                    if self.debug_mode:
                        self.debug(f"Failed to fetch PR data: {e}")

            # Get GitHub base URL for links
            remote_url = self.repo.remotes.origin.url
            github_base_url = None
            if 'github.com' in remote_url:
                if remote_url.startswith('git@'):
                    org_repo = remote_url.split(':')[1].replace('.git', '')
                    github_base_url = f"https://github.com/{org_repo}"
                else:
                    github_base_url = remote_url.replace('.git', '')

            # Display results
            if branch_details:
                self.display_service.display_branch_table(
                    branch_details,
                    self.repo,
                    github_base_url,
                    self.branch_status_service,
                    show_summary=self.verbose
                )
            else:
                console.print("No branches match the filter criteria")
        except Exception as e:
            console.print(f"[red]Error processing branches: {e}[/red]")

    def _process_single_branch(self, branch: str, status_filter: str, pr_data: dict, progress=None) -> Optional[BranchDetails]:
        """Process a single branch and return its details if it matches the filter."""
        # Check PR status first if available
        if pr_data and branch in pr_data:
            if pr_data[branch]['merged']:
                status = BranchStatus.MERGED
            elif pr_data[branch]['closed']:
                status = BranchStatus.ACTIVE
            else:
                status = self.branch_status_service.get_branch_status(branch, self.main_branch, pr_data)
        else:
            status = self.branch_status_service.get_branch_status(branch, self.main_branch, pr_data)
        
        # Skip if doesn't match filter
        if status_filter != 'all' and status.value != status_filter:
            if self.verbose:
                self.debug(f"Skipping {branch} - status {status.value} doesn't match filter {status_filter}")
            return None
        
        sync_status = self.git_service.get_branch_sync_status(branch, self.main_branch)
        
        # Get PR count, show empty string if 0
        pr_count = pr_data.get(branch, {}).get('count', 0) if pr_data else 0
        pr_display = str(pr_count) if pr_count > 0 else ""
        
        details = BranchDetails(
            name=branch,
            last_commit_date=self.git_service.get_last_commit_date(branch),
            age_days=self.git_service.get_branch_age(branch),
            status=status,
            has_local_changes=False,  # TODO: Implement this
            has_remote=self.git_service.has_remote_branch(branch),
            sync_status=sync_status,
            pr_status=pr_display,
            notes=None  # Initialize notes as None, it will be updated later if needed
        )
            
        return details

    def cleanup(self):
        """Clean up branches."""
        self.process_branches(cleanup_enabled=True)

    def update_main(self):
        """Update the main branch from remote."""
        return self.git_service.update_main_branch(self.main_branch)

    def debug(self, message: str) -> None:
        """Print debug message if debug mode is enabled."""
        if self.config.get('debug', False):
            print(f"[BranchKeeper] {message}")
