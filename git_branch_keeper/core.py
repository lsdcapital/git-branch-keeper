"""Core functionality for git-branch-keeper"""

import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import git
import requests
from rich.console import Console
from rich.table import Table

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
            interactive: bool = False,
            dry_run: bool = False,
            verbose: bool = False,
            stale_days: int = 30,
            prune_remote: bool = False,
            default_branch: str = "main",
            ignore_branches: Optional[List[str]] = None,
            config: Optional[Dict] = None,
            force_mode: bool = False,
            status_filter: str = "all"
    ):
        self.interactive = interactive
        self.dry_run = dry_run
        self.verbose = verbose
        self.stale_days = stale_days
        self.prune_remote = prune_remote
        self.default_branch = default_branch
        self.ignore_branches = ignore_branches or []
        self.repo = self._get_repo()
        self.main_branch = self._get_main_branch()
        self.stats = {
            "active": 0,
            "merged": 0,
            "stale": 0,
            "skipped_pr": 0
        }
        self.interrupted = False
        self.config = config or {}
        self.force_mode = force_mode
        self.status_filter = status_filter
        self._setup_github_api()

    def _setup_github_api(self):
        """Setup GitHub API access if possible."""
        self.github_token = self.config.get("github_token") or os.environ.get("GITHUB_TOKEN")
        self.github_api_url = None
        self.github_repo = None

        if not self.github_token:
            return

        try:
            remote_url = self.repo.remotes.origin.url
            if "github.com" not in remote_url:
                return

            # Parse GitHub repository from remote URL
            parsed_url = urlparse(remote_url)
            path = parsed_url.path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]

            self.github_repo = path
            self.github_api_url = f"https://api.github.com/repos/{path}"
            console.print(f"Detected GitHub repository: {path}")
            console.print("GitHub PR checking enabled")
        except Exception as e:
            self.debug(f"Error setting up GitHub API: {e}")

    def _git_operation(self, operation):
        """Wrapper for Git operations to handle interrupts safely."""
        global in_git_operation
        in_git_operation = True
        try:
            result = operation()
            in_git_operation = False
            return result
        except Exception as e:
            in_git_operation = False
            raise e

    def _get_repo(self):
        """Initialize Git repository from current directory."""
        try:
            return git.Repo(os.getcwd())
        except git.InvalidGitRepositoryError:
            console.print("[red]Error: Not a Git repository[/red]")
            sys.exit(1)

    def _get_main_branch(self):
        """Determine the main branch name."""
        try:
            # Check if default_branch exists
            if self.default_branch in [ref.name for ref in self.repo.refs]:
                return self.default_branch
            # Try common main branch names
            for name in ["main", "master"]:
                if name in [ref.name for ref in self.repo.refs]:
                    return name
            return self.default_branch
        except Exception as e:
            self.debug(f"Error determining main branch: {e}")
            return self.default_branch

    def debug(self, message: str):
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            console.print(f"[dim]{message}[/dim]")

    def is_branch_merged(self, branch_name: str, check_type: str = "merged") -> bool:
        """
        Check if a branch is merged or stale.
        
        Args:
            branch_name: Name of the branch to check
            check_type: Either "merged" or "stale"
            
        Returns:
            bool: True if branch is merged/stale, False otherwise
        """
        if check_type not in ["merged", "stale"]:
            raise ValueError("check_type must be either 'merged' or 'stale'")

        try:
            branch = self.repo.heads[branch_name]
            main = self.repo.heads[self.main_branch]

            if check_type == "merged":
                # Check if branch is merged into main
                merge_base = self.repo.merge_base(branch, main)
                if not merge_base:
                    return False
                return merge_base[0].hexsha == branch.commit.hexsha
            else:  # stale
                # Check if branch is stale (no commits in stale_days)
                last_commit = datetime.fromtimestamp(branch.commit.committed_date, tz=timezone.utc)
                days_old = (datetime.now(timezone.utc) - last_commit).days
                return days_old >= self.stale_days
        except Exception as e:
            self.debug(f"Error checking branch {branch_name}: {e}")
            return False

    def _has_open_pr(self, branch_name: str) -> bool:
        """Check if a branch has an open PR on GitHub."""
        if not (self.github_api_url and self.github_token):
            return False

        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            url = f"{self.github_api_url}/pulls"
            params = {
                "head": f"{self.github_repo.split('/')[0]}:{branch_name}",
                "state": "open"
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return len(response.json()) > 0
        except Exception as e:
            self.debug(f"Error checking PR status: {e}")
            return False

    def delete_branch(self, branch_name: str, reason: str) -> bool:
        """
        Delete a branch or show what would be deleted in dry-run mode.
        
        Args:
            branch_name: Name of the branch to delete
            reason: Reason for deletion
            
        Returns:
            bool: True if branch was deleted (or would be in dry-run), False otherwise
        """
        if self._has_open_pr(branch_name):
            console.print(f"[yellow]Skipping {branch_name} - Has open PR[/yellow]")
            self.stats["skipped_pr"] += 1
            return False

        if self.dry_run:
            console.print(f"[yellow]Would delete {branch_name} - {reason}[/yellow]")
            return True

        if self.interactive:
            if not console.input(f"Delete {branch_name}? [y/N] ").lower().startswith('y'):
                return False

        # Delete local branch
        try:
            def delete_local():
                self.repo.delete_head(branch_name, force=True)
                console.print(f"[green]Deleted {branch_name} - {reason}[/green]")
                return True
            return self._git_operation(delete_local)
        except git.GitCommandError as e:
            console.print(f"[red]Error deleting local branch: {e}[/red]")
            return False

        # Delete remote branch if it exists
        remote_branch = f"origin/{branch_name}"
        if remote_branch in [ref.name for ref in self.repo.remote().refs]:
            if not self.dry_run:
                def delete_remote():
                    try:
                        self.repo.git.push("origin", "--delete", branch_name)
                        return True
                    except git.GitCommandError as e:
                        console.print(f"[red]Error deleting remote branch: {e}[/red]")
                        return False
                return self._git_operation(delete_remote)

        return True

    def process_branches(self):
        """Process all branches and handle deletions."""
        if self.force_mode and self.status_filter == "all":
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

        # Always show the table summary
        console.print("\n[bold]Branch Status:[/bold]")
        table = Table(title="Local Branches")
        table.add_column("Branch", style="cyan")
        table.add_column("Last Commit Date", style="green")
        table.add_column("Age (days)", justify="center", style="yellow")
        table.add_column("Status", style="magenta")
        table.add_column("Remote", justify="center", style="blue")

        branches_to_process = []
        for branch in local_branches:
            if branch == current:
                self.stats["active"] += 1
                continue
            if branch == self.main_branch:
                continue
            if branch in self.ignore_branches:
                continue

            # Get branch info
            branch_ref = self.repo.heads[branch]
            last_commit = datetime.fromtimestamp(branch_ref.commit.committed_date, tz=timezone.utc)
            days_old = (datetime.now(timezone.utc) - last_commit).days
            remote_exists = f"origin/{branch}" in [ref.name for ref in self.repo.remote().refs]

            # Determine branch status
            status = []
            is_merged = self.is_branch_merged(branch, "merged")
            is_stale = self.is_branch_merged(branch, "stale")
            
            if is_merged:
                status.append("Merged")
            if is_stale:
                status.append(f"Stale ({days_old} days)")
            if not status:
                if days_old == 0:
                    status.append("Updated today")
                elif days_old == 1:
                    status.append("Updated yesterday")
                else:
                    status.append(f"Updated {days_old} days ago")

            status_str = ", ".join(status)

            # Filter based on status
            if self.status_filter != "all":
                if self.status_filter == "merged" and not is_merged:
                    continue
                if self.status_filter == "stale" and not is_stale:
                    continue

            # Add to table
            table.add_row(
                branch,
                last_commit.strftime("%Y-%m-%d %H:%M"),
                str(days_old),
                status_str,
                "✓" if remote_exists else "✗"
            )

            # Add to processing list if it matches our criteria
            if self.force_mode:
                if self.status_filter == "merged" and is_merged:
                    branches_to_process.append((branch, "merged"))
                elif self.status_filter == "stale" and is_stale:
                    branches_to_process.append((branch, "stale"))
            else:
                if is_merged:
                    branches_to_process.append((branch, "merged"))
                    self.stats["merged"] += 1
                elif is_stale:
                    branches_to_process.append((branch, "stale"))
                    self.stats["stale"] += 1

        # Print the table after all rows have been added
        console.print(table)
        console.print("\n[dim]✓ = Has remote branch  ✗ = Local only[/dim]")
        console.print(f"[dim]Branches older than {self.stale_days} days are marked as stale[/dim]")
        console.print()

        # Process branches if not in status-only mode
        if not self.force_mode and branches_to_process:
            console.print("\n[bold]Branches to clean up:[/bold]")
            for branch, reason in branches_to_process:
                self.delete_branch(branch, reason)

    def update_main(self):
        """Update the main branch from remote."""
        try:
            def update():
                self.repo.git.checkout(self.main_branch)
                self.repo.git.pull("origin", self.main_branch)
                return True
            return self._git_operation(update)
        except git.GitCommandError as e:
            console.print(f"[red]Error updating main branch: {e}[/red]")
            return False

    def print_summary(self):
        """Print summary of branch cleanup operation."""
        console.print("\nSummary:")
        console.print(f"- Found {self.stats['active']} active branch(es)")
        if self.stats["merged"] > 0:
            console.print(f"- Found {self.stats['merged']} merged branch(es)")
        if self.stats["stale"] > 0:
            console.print(f"- Found {self.stats['stale']} stale branch(es)")
        if self.stats["skipped_pr"] > 0:
            console.print(f"- Skipped {self.stats['skipped_pr']} branch(es) with open PRs")
        
        if self.stats["merged"] + self.stats["stale"] == 0:
            console.print("No branches to clean up!")
        
        console.print("\nCleanup complete!")
