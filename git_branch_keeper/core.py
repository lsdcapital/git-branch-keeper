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

    def __init__(
            self,
            repo_path=".",
            config=None,
            verbose=False,
            bypass_github=False,
            show_filter="local",
            interactive=False,
            dry_run=False,
            min_stale_days=30,
            prune_remote=False,
            protected_branches=None,
            ignore_patterns=None,
            force_mode=False,
            status_filter="all"
    ):
        try:
            self.repo_path = repo_path
            self.config = config or {}
            self.verbose = verbose
            self.bypass_github = bypass_github
            self.show_filter = show_filter
            self.interactive = interactive
            self.dry_run = dry_run
            self.min_stale_days = min_stale_days
            self.prune_remote = prune_remote
            self.protected_branches = protected_branches or ["main", "master"]
            self.ignore_patterns = ignore_patterns or []
            self.force_mode = force_mode
            self.status_filter = status_filter
            
            # Initialize services
            try:
                self.git_service = GitService(repo_path=self.repo_path, verbose=self.verbose)
            except ValueError as e:
                console.print(f"[red]Error: {str(e)}[/red]")
                sys.exit(1)
            
            self.github_service = GitHubService(config=self.config, verbose=self.verbose)
            self.display_service = DisplayService(verbose=self.verbose)
            self.branch_status_service = BranchStatusService(
                git_service=self.git_service,
                github_service=self.github_service,
                config={
                    'protected_branches': self.protected_branches,
                    'ignore_patterns': self.ignore_patterns,
                    'stale_days': self.min_stale_days,
                    'status_filter': self.status_filter
                }
            )
            
            # Get repo and main branch
            self.repo = self.git_service.repo
            self.main_branch = self.git_service.get_main_branch(self.protected_branches)
            
            # Initialize stats
            self.stats = {
                "active": 0,
                "merged": 0,
                "stale": 0,
                "skipped_pr": 0
            }
            self.interrupted = False
            self._setup_github_api()
        except Exception as e:
            console.print(f"[red]Error initializing BranchKeeper: {e}[/red]")
            sys.exit(1)

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
            if self.github_service.has_open_pr(branch_name):
                console.print(f"[yellow]Skipping {branch_name} - Has open PR[/yellow]")
                self.stats["skipped_pr"] += 1
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

    def _print_branch_table(self, local_branches, progress=None):
        """Prepare branch data and use display service to show table."""
        branches_data = []
        
        for branch_name in local_branches:
            status = self.branch_status_service.get_branch_status(branch_name, self.main_branch).value
            age_days = self.git_service.get_branch_age(branch_name)
            has_remote = self.git_service.has_remote_branch(branch_name)
            
            branch_data = {
                'name': branch_name,
                'status': status,
                'age_days': age_days,
                'has_remote': has_remote,
                'sync_status': self.git_service.get_branch_sync_status(branch_name, self.main_branch),
                'pr_count': 0,
                'would_clean': self.branch_status_service.should_process_branch(branch_name, BranchStatus(status)),
                'is_current': branch_name == self.repo.active_branch.name,
                'last_commit_date': self.git_service.get_last_commit_date(branch_name),
                'stale_days': self.min_stale_days,
                'github_disabled': not self.github_service.github_enabled,
            }
            
            if self.github_service.github_enabled and not self.bypass_github:
                branch_data['pr_count'] = self.github_service.get_pr_count(branch_name)
            
            branches_data.append(branch_data)
            
            if progress:
                progress.update(progress.tasks[1].id, advance=1)

        # Get GitHub base URL for links
        remote_url = self.repo.remotes.origin.url
        github_base_url = None
        if 'github.com' in remote_url:
            if remote_url.startswith('git@'):
                org_repo = remote_url.split(':')[1].replace('.git', '')
                github_base_url = f"https://github.com/{org_repo}"
            else:
                github_base_url = remote_url.replace('.git', '')

        return self.display_service.print_branch_table(branches_data, github_base_url)

    def process_branches(self, cleanup_enabled=False):
        """Process all branches and handle deletions."""
        if self.verbose:
            console.print("\n[yellow]Starting branch processing...[/yellow]")
        
        if cleanup_enabled and self.force_mode and self.status_filter == "all":
            console.print("[red]Error: Force mode requires specific status (merged or stale)[/red]")
            return

        # Get all local branches
        local_branches = [head.name for head in self.repo.heads]
        if not local_branches:
            console.print("[yellow]No branches found[/yellow]")
            return

        current = self.repo.active_branch.name
        if current not in local_branches:
            local_branches.append(current)

        # Debug output only in verbose mode
        if self.verbose:
            console.print(f"\n[yellow]Found {len(local_branches)} local branches[/yellow]")
            console.print(f"[yellow]Current branch: {current}[/yellow]")
            
            remote_refs = list(self.repo.remotes.origin.refs)
            console.print(f"[yellow]Found {len(remote_refs)} remote refs[/yellow]")
            self.debug(f"Protected branches: {self.protected_branches}")
            self.debug(f"Ignore patterns: {self.ignore_patterns}")
        
        remote_branches = self.git_service.get_remote_branches()
        branches_to_process = self._print_branch_table(local_branches)

        # Handle cleanup if enabled
        if cleanup_enabled and branches_to_process:
            if self.force_mode:
                for branch in branches_to_process:
                    self.delete_branch(branch, "force mode")
            else:
                # Interactive mode
                for branch in branches_to_process:
                    if self.interactive:
                        response = input(f"\nDelete branch {branch}? [y/N] ")
                        if response.lower() == 'y':
                            self.delete_branch(branch, "user confirmed")
                    else:
                        self.delete_branch(branch, "auto cleanup")

    def cleanup(self):
        """Clean up branches."""
        self.process_branches(cleanup_enabled=True)

    def update_main(self):
        """Update the main branch from remote."""
        return self.git_service.update_main_branch(self.main_branch)

    def debug(self, message: str) -> None:
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            print(f"[BranchKeeper] {message}")
