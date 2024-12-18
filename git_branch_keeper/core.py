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
import requests
from rich.console import Console
from rich.progress import Progress
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
        
        self.repo = self._get_repo()
        self.main_branch = self._get_main_branch()
        self.stats = {
            "active": 0,
            "merged": 0,
            "stale": 0,
            "skipped_pr": 0
        }
        self.interrupted = False
        self._setup_github_api()
        self._remote_refs_cache = None
        self._branch_age_cache = {}
        self._branch_merge_cache = {}
        self._branch_sync_cache = {}

    def _setup_github_api(self):
        """Setup GitHub API access if possible."""
        self.github_token = self.config.get("github_token") or os.environ.get("GITHUB_TOKEN")
        self.github_api_url = None
        self.github_repo = None

        try:
            remote_url = self.repo.remotes.origin.url
            self.debug(f"Remote URL: {remote_url}")
            
            if "github.com" not in remote_url:
                self.debug("Not a GitHub repository")
                return

            # Parse GitHub repository from remote URL
            if remote_url.startswith("git@"):
                # Handle SSH URL format (git@github.com:org/repo.git)
                path = remote_url.split("github.com:", 1)[1]
            else:
                # Handle HTTPS URL format (https://github.com/org/repo.git)
                parsed_url = urlparse(remote_url)
                path = parsed_url.path.strip("/")
            
            if path.endswith(".git"):
                path = path[:-4]

            if not self.github_token:
                # Automatically run in bypass mode if no token found
                self.bypass_github = True
                console.print("[yellow]No GitHub token found. Running with reduced GitHub functionality[/yellow]")
                self.github_repo = path
                return

            self.github_repo = path
            self.github_api_url = f"https://api.github.com/repos/{path}"
            self.debug(f"Detected GitHub repository: {path}")
            self.debug(f"GitHub API URL: {self.github_api_url}")
            console.print(f"[dim]GitHub integration enabled for: {path}[/dim]")
        except Exception as e:
            self.debug(f"Error setting up GitHub API: {e}")

    def _get_github_branch_url(self, branch_name: str, type: str = "pulls") -> str:
        """Get the GitHub URL for a branch.
        
        Args:
            branch_name: Name of the branch
            type: Either 'pulls' for PR view or 'tree' for branch view
        """
        if not self.github_repo:
            self.debug("No GitHub repo configured")
            return ""
        
        try:
            # First check if branch has an outgoing PR
            if type == "pulls":
                # For main branch, show PRs targeting it
                if branch_name == self.main_branch:
                    return f"https://github.com/{self.github_repo}/pulls?q=is%3Apr+is%3Aopen+base%3A{branch_name}"
                
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
                prs = response.json()
                
                # If there's an outgoing PR, return its URL
                if prs:
                    return prs[0]["html_url"]
                
                # Otherwise return the PR search URL
                return f"https://github.com/{self.github_repo}/pulls?q=is%3Apr+head%3A{branch_name}"
            else:  # tree view
                return f"https://github.com/{self.github_repo}/tree/{branch_name}"
            
        except Exception as e:
            self.debug(f"Error generating GitHub URL for {branch_name}: {e}")
            return f"https://github.com/{self.github_repo}"

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
            return git.Repo(self.repo_path)
        except git.InvalidGitRepositoryError:
            console.print("[red]Error: Not a Git repository[/red]")
            sys.exit(1)

    def _get_main_branch(self):
        """Determine the main branch name."""
        try:
            # Try each protected branch name
            for name in self.protected_branches:
                if name in [ref.name for ref in self.repo.refs]:
                    return name
            
            # If none found, return the first protected branch
            return self.protected_branches[0]
        except Exception as e:
            self.debug(f"Error determining main branch: {e}")
            return "main"

    def _is_protected_branch(self, branch_name: str) -> bool:
        """Check if a branch is protected."""
        return branch_name in self.protected_branches

    def _has_remote_branch(self, branch_name: str) -> bool:
        """Check if a branch exists in the remote with caching."""
        try:
            if self._remote_refs_cache is None:
                refs = list(self.repo.remotes.origin.refs)
                self._remote_refs_cache = [ref.name for ref in refs]
                if self.verbose:
                    print(f"Initialized remote refs cache with {len(self._remote_refs_cache)} refs")
            
            remote_ref = f"origin/{branch_name}"
            exists = remote_ref in self._remote_refs_cache
            
            if self.verbose:
                print(f"Looking for: {remote_ref}")
                print(f"Found in remote refs: {exists}")
            
            return exists
            
        except Exception as e:
            if self.verbose:
                print(f"Error checking remote branch {branch_name}: {e}")
            return False

    def _get_branch_status(self, branch_name: str, local_branches, remote_branches) -> str:
        """Get status of a branch with early exits."""
        try:
            # Quick checks first
            if branch_name == self.main_branch or self._is_protected_branch(branch_name):
                return "active"
            
            if self._should_ignore_branch(branch_name):
                return "ignored"
            
            # Only check merge status if needed
            if self.status_filter in ["merged", "all"]:
                if self._is_merged(branch_name):
                    return "merged"
            
            # Only check staleness if needed
            if self.status_filter in ["stale", "all"]:
                if self.min_stale_days > 0:
                    age_days = self._get_branch_age(branch_name)
                    if age_days >= self.min_stale_days:
                        return "stale"
            
            return "active"
        except Exception as e:
            self.debug(f"Error getting branch status: {e}")
            return "unknown"

    def _should_process_branch(self, branch, status):
        """Determine if a branch should be processed based on status filter."""
        if self.status_filter == "all":
            return status in ["merged", "stale"]
        return status == self.status_filter

    def _get_branch_age(self, branch_name: str) -> int:
        """Get the age of a branch in days."""
        try:
            branch = self.repo.heads[branch_name]
            commit_date = datetime.fromtimestamp(branch.commit.committed_date)
            age = (datetime.now() - commit_date).days
            return age
        except Exception as e:
            if self.verbose:
                print(f"Error calculating age for {branch_name}: {e}")
            return 0

    def _is_merged(self, branch_name: str) -> bool:
        """Check if a branch is merged into main with caching."""
        try:
            if branch_name in self._branch_merge_cache:
                return self._branch_merge_cache[branch_name]

            # Main branch is never considered merged
            if branch_name == self.main_branch:
                self._branch_merge_cache[branch_name] = False
                return False

            branch = self.repo.heads[branch_name]
            
            # First check if branch is merged into local main
            main = self.repo.heads[self.main_branch]
            is_merged = self.repo.is_ancestor(branch.commit, main.commit)
            
            # Then check if branch is merged into remote main
            if not is_merged:
                try:
                    remote_main = self.repo.refs[f'origin/{self.main_branch}']
                    is_merged = self.repo.is_ancestor(branch.commit, remote_main.commit)
                    if is_merged and self.verbose:
                        print(f"Branch {branch_name} is merged into remote main")
                except Exception as e:
                    if self.verbose:
                        print(f"Error checking remote main: {e}")

            if self.verbose:
                # Log commit counts for debugging
                behind_commits = list(self.repo.iter_commits(f'{branch_name}..{main.name}'))
                ahead_commits = list(self.repo.iter_commits(f'{main.name}..{branch_name}'))
                print(f"Branch {branch_name} is {len(behind_commits)} commits behind and {len(ahead_commits)} commits ahead of main")
                
                try:
                    remote_behind = list(self.repo.iter_commits(f'{branch_name}..origin/{main.name}'))
                    remote_ahead = list(self.repo.iter_commits(f'origin/{main.name}..{branch_name}'))
                    print(f"Branch {branch_name} is {len(remote_behind)} commits behind and {len(remote_ahead)} commits ahead of remote main")
                except Exception as e:
                    if self.verbose:
                        print(f"Error checking remote commit counts: {e}")

            # Cache the result
            self._branch_merge_cache[branch_name] = is_merged
            return is_merged

        except Exception as e:
            if self.verbose:
                print(f"Error checking if branch {branch_name} is merged: {e}")
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

    def _should_ignore_branch(self, branch_name: str) -> bool:
        """Check if a branch should be ignored based on ignore patterns."""
        return any(fnmatch(branch_name, pattern) for pattern in self.ignore_patterns)

    def delete_branch(self, branch_name: str, reason: str) -> bool:
        """
        Delete a branch or show what would be deleted in dry-run mode.
        
        Args:
            branch_name: Name of the branch to delete
            reason: Reason for deletion
            
        Returns:
            bool: True if branch was deleted (or would be in dry-run), False otherwise
        """
        try:
            # Check if branch exists in remote
            remote_exists = self._has_remote_branch(branch_name)

            if self._has_open_pr(branch_name):
                console.print(f"[yellow]Skipping {branch_name} - Has open PR[/yellow]")
                self.stats["skipped_pr"] += 1
                return False

            if self.dry_run:
                if remote_exists:
                    console.print(f"Would delete local and remote branch {branch_name} ({reason})")
                else:
                    console.print(f"Would delete local branch {branch_name} ({reason})")
                return True

            # Delete local branch
            if branch_name == self.repo.active_branch.name:
                # Cannot delete current branch
                console.print(f"[yellow]Cannot delete current branch {branch_name}[/yellow]")
                return False

            # Delete remote branch first if it exists
            if remote_exists:
                try:
                    self.repo.git.push("origin", "--delete", branch_name)
                    console.print(f"Deleted remote branch {branch_name}")
                except Exception as e:
                    console.print(f"[red]Error deleting remote branch {branch_name}: {e}[/red]")
                    return False

            # Delete local branch (this will also clean up any upstream tracking)
            self.repo.git.branch("-D", branch_name)
            console.print(f"Deleted local branch {branch_name}")
            return True

        except Exception as e:
            console.print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
            return False

    def _print_branch_table(self, local_branches, progress=None):
        """Print table of branches with their status."""
        if self.verbose:
            print("\n>>> Starting _print_branch_table")
        
        table = Table(title="Local Branches", show_header=True, header_style="bold")
        table.add_column("Branch", style="cyan")
        table.add_column("Last Commit Date")
        table.add_column("Age (days)")
        table.add_column("Status")
        table.add_column("Remote")
        table.add_column("Sync Status")
        table.add_column("PRs")

        branches_to_process = []
        chunk_size = 20
        
        # Get the GitHub repo URL from remote
        remote_url = self.repo.remotes.origin.url
        github_base_url = None
        if 'github.com' in remote_url:
            if remote_url.startswith('git@'):
                # SSH format: git@github.com:org/repo.git
                org_repo = remote_url.split(':')[1].replace('.git', '')
                github_base_url = f"https://github.com/{org_repo}"
            else:
                # HTTPS format: https://github.com/org/repo.git
                github_base_url = remote_url.replace('.git', '')
        
        # Get all remote branches
        remote_branches = self._get_remote_branches() or []
        
        for i in range(0, len(local_branches), chunk_size):
            chunk = local_branches[i:i + chunk_size]
            if self.verbose:
                print(f"\n>>> Processing chunk {i//chunk_size + 1} ({len(chunk)} branches)")
            
            for branch_name in chunk:
                if self.verbose:
                    print(f"\n>>> Processing branch: {branch_name}")
                
                # Get branch status using the comprehensive check
                status = self._get_branch_status(branch_name, local_branches, remote_branches)
                age_days = self._get_branch_age(branch_name)
                
                if self.verbose:
                    print(f"Status for {branch_name}: {status}")
                    
                is_merged = status == "merged"
                sync_status = self._get_branch_sync_status(branch_name)
                has_remote = sync_status != "local-only" and sync_status != "merged-remote-deleted"
                
                # Only check PRs if branch isn't merged and isn't ignored
                pr_count = 0
                if status not in ["merged", "ignored"] and hasattr(self, 'github_api_url'):
                    pr_count = self._get_pr_count(branch_name)
                
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
                elif self.bypass_github:
                    pr_display = "[dim]disabled[/dim]"
                
                # Determine if branch would be cleaned up
                would_clean = (
                    branch_name != self.repo.active_branch.name and  # Don't clean current branch
                    branch_name in local_branches and  # Must be a local branch
                    not self._is_protected_branch(branch_name) and  # Not protected
                    not self._should_ignore_branch(branch_name) and  # Not ignored
                    (
                        (self.status_filter == "merged" and is_merged) or  # Merged filter
                        (self.status_filter == "stale" and status == "stale") or  # Stale filter
                        (self.status_filter == "all" and (is_merged or status == "stale"))  # All filter
                    )
                )
                
                # Add row to table with conditional styling
                row_style = "yellow" if would_clean else None
                table.add_row(
                    branch_display + (" *" if branch_name == self.repo.active_branch.name else ""),
                    self._get_last_commit_date(branch_name),
                    str(age_days),
                    status,
                    "✓" if has_remote else "✗",
                    sync_status,
                    pr_display,
                    style=row_style
                )
                
                if progress is not None:
                    progress.update(progress.tasks[1].id, advance=1)
                
                if would_clean:
                    branches_to_process.append(branch_name)

        # Print the table
        console.print(table)
        
        # Print legend
        console.print("\n✓ = Has remote branch  ✗ = Local only")
        console.print("↑ = Unpushed commits  ↓ = Commits to pull")
        console.print("* = Current branch  Yellow = Would be cleaned up")
        console.print(f"\nBranches older than {self.min_stale_days} days are marked as stale\n")
        
        return branches_to_process

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
            
            # Debug remote refs
            console.print("\n[yellow]Fetching remote refs...[/yellow]")
            remote_refs = list(self.repo.remotes.origin.refs)
            console.print(f"[yellow]Found {len(remote_refs)} remote refs[/yellow]")
            console.print("[yellow]First 5 remote refs:[/yellow]")
            for ref in remote_refs[:5]:
                console.print(f"  {ref.name}")

            self.debug(f"Protected branches: {self.protected_branches}")
            self.debug(f"Ignore patterns: {self.ignore_patterns}")
        
            remote_branches = self._get_remote_branches()
            branches_to_process = self._print_branch_table(local_branches)
        else:
            with Progress(transient=True) as progress:
                # First progress bar for fetching
                fetch_task = progress.add_task("[cyan]Fetching from origin...", total=1)
                remote_branches = self._get_remote_branches()
                progress.update(fetch_task, completed=1)
                
                if remote_branches is None:
                    remote_branches = []

                if self.show_filter == "remote" and not remote_branches:
                    console.print("[yellow]No remote branches found[/yellow]")
                    return

                # Second progress bar for processing
                process_task = progress.add_task("[cyan]Processing branches...", total=len(local_branches))
                branches_to_process = self._print_branch_table(local_branches, progress)
                progress.update(process_task, completed=len(local_branches))

        # Print summary
        console.print("\nSummary:")
        if self.show_filter == "local":
            console.print(f"- Found {len(local_branches)} local branch(es)")
        elif self.show_filter == "remote":
            console.print(f"- Found {len(remote_branches)} remote branch(es)")
        else:  # all
            console.print(f"- Found {len(local_branches)} local and {len(remote_branches)} remote branch(es)")
        
        if len(branches_to_process) > 0:
            merged_count = sum(1 for branch in branches_to_process if self._is_merged(branch))
            stale_count = len(branches_to_process) - merged_count
            
            if self.status_filter == "all":
                if merged_count > 0 and stale_count > 0:
                    console.print(f"- Found {merged_count} merged and {stale_count} stale branch(es) that would be cleaned up")
                elif merged_count > 0:
                    console.print(f"- Found {merged_count} merged branch(es) that would be cleaned up")
                elif stale_count > 0:
                    console.print(f"- Found {stale_count} stale branch(es) that would be cleaned up")
            else:
                console.print(f"- Found {len(branches_to_process)} {self.status_filter} branch(es) that would be cleaned up")
        else:
            console.print("No branches need cleanup!")

        # Handle cleanup if enabled
        if cleanup_enabled and branches_to_process:
            if self.force_mode:
                self._delete_branches(branches_to_process)
            else:
                self._confirm_and_delete_branches(branches_to_process)

    def cleanup(self):
        """Clean up branches."""
        self.process_branches(cleanup_enabled=True)

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
                merge_base = self.repo.merge_base(branch.commit, main.commit)
                if not merge_base:
                    return False
                return merge_base[0].hexsha == branch.commit.hexsha
            else:  # stale
                # Check if branch is stale (no commits in stale_days)
                last_commit = datetime.fromtimestamp(branch.commit.committed_date, tz=timezone.utc)
                days_old = (datetime.now(timezone.utc) - last_commit).days
                return days_old >= self.min_stale_days
        except Exception as e:
            self.debug(f"Error checking branch {branch_name}: {e}")
            return False

    def _get_branch_sync_status(self, branch_name: str) -> str:
        """Get the sync status between local and remote branch."""
        try:
            # Check if branch is merged first
            if self._is_merged(branch_name):
                if self.verbose:
                    print(f"\n>>> _get_branch_sync_status called for: {branch_name}")
                    print(f"Branch {branch_name} is merged, checking if remote exists...")
                # If branch is merged but remote still exists, show sync status
                if self._has_remote_branch(branch_name):
                    if self.verbose:
                        print("Remote exists, checking sync status...")
                    return self._check_sync_status(branch_name)
                else:
                    if self.verbose:
                        print("Remote is gone (normal for merged branches)")
                    return "merged-remote-deleted"
            
            # Not merged, check remote as normal
            if not self._has_remote_branch(branch_name):
                return "local-only"
            
            return self._check_sync_status(branch_name)
                
        except Exception as e:
            if self.verbose:
                print(f"Error getting sync status for {branch_name}: {e}")
            return "unknown"

    def _check_sync_status(self, branch_name: str) -> str:
        """Helper method to check sync status between local and remote."""
        try:
            local_branch = self.repo.refs[branch_name]
            remote_ref = self.repo.refs[f"origin/{branch_name}"]
            
            commits_behind = list(self.repo.iter_commits(f'{branch_name}..origin/{branch_name}'))
            commits_ahead = list(self.repo.iter_commits(f'origin/{branch_name}..{branch_name}'))
            
            ahead_count = len(commits_ahead)
            behind_count = len(commits_behind)
            
            if ahead_count > 0 and behind_count > 0:
                return f"diverged ↑{ahead_count} ↓{behind_count}"
            elif ahead_count > 0:
                return f"ahead ↑{ahead_count}"
            elif behind_count > 0:
                return f"behind ↓{behind_count}"
            else:
                return "synced"
                
        except (IndexError, KeyError) as e:
            return "remote-only"

    def _get_remote_branches(self):
        """Get list of all remote branches."""
        try:
            self.debug("Fetching from origin...")
            self.repo.remotes.origin.fetch()
            
            # Get all remote branches, excluding HEAD and tags
            # Only include refs that start with 'origin/' but exclude 'origin/HEAD' and 'origin/tags'
            all_refs = [ref.name for ref in self.repo.remotes.origin.refs]
            self.debug(f"All remote refs: {all_refs}")
            
            branches = [
                ref.name.split('/', 1)[1]  # Remove 'origin/' prefix
                for ref in self.repo.remotes.origin.refs 
                if ref.name.startswith('origin/') 
                and not ref.name.endswith('HEAD')
                and not ref.name.startswith('origin/tags/')
                and not '/tags/' in ref.name
                and not ref.name.startswith('refs/tags/')
            ]
            self.debug(f"Filtered remote branches: {branches}")
            return branches
        except Exception as e:
            self.debug(f"Error getting remote branches: {e}")
            return []

    def _get_branch_details(self, branch):
        """Get detailed information about a branch."""
        try:
            if not self._has_remote_branch(branch):
                return None
                
            origin_ref = self.repo.refs[f"origin/{branch}"]
            local_ref = None
            try:
                local_ref = self.repo.refs[branch]
            except (KeyError, git.GitCommandError):
                pass
            
            if local_ref:
                behind_count = len(list(self.repo.iter_commits(f"{local_ref}..{origin_ref}")))
                ahead_count = len(list(self.repo.iter_commits(f"{origin_ref}..{local_ref}")))
                
                self.debug(f"Remote branch {branch} vs local: ahead by {ahead_count} and behind by {behind_count}")
                
                if ahead_count > 0 and behind_count > 0:
                    return f"diverged ↕️{ahead_count}↑{behind_count}↓"
                elif ahead_count > 0:
                    return f"ahead ↑{ahead_count}"
                elif behind_count > 0:
                    return f"behind ↓{behind_count}"
                else:
                    return "synced"
            else:
                return "remote-only"
                
        except (KeyError, git.GitCommandError) as e:
            self.debug(f"Error getting branch details for {branch}: {e}")
            return None

    def _get_pr_count(self, branch_name: str) -> int:
        """Get the number of PRs for a branch."""
        # Skip PR check if branch is merged
        if self._is_merged(branch_name):
            return 0

        if self.bypass_github or not (self.github_api_url and self.github_token):
            if self.verbose:
                self.debug(f"Skipping PR check for {branch_name} - GitHub integration disabled")
            return 0

        try:
            # Get the repo name from the remote URL
            remote_url = self.repo.remotes.origin.url
            if self.verbose:
                self.debug(f"Remote URL: {remote_url}")
            
            # Extract org and repo from URL (handles both HTTPS and SSH formats)
            org_repo = remote_url.split(':')[-1].split('.git')[0]
            if '/' in org_repo:
                org, repo = org_repo.split('/')[-2:]
            else:
                org = self.github_org
                repo = org_repo
            
            if self.verbose:
                self.debug(f"Checking PRs for {org}/{repo} branch: {branch_name}")

            # Check for PRs where this branch is the source (head)
            params = {
                "head": f"{org}:{branch_name}",
                "state": "open"
            }
            headers = {"Authorization": f"token {self.github_token}"}
            response = requests.get(f"{self.github_api_url}/pulls", params=params, headers=headers)
            response.raise_for_status()
            outgoing_prs = len(response.json())
            
            if self.verbose:
                self.debug(f"Found {outgoing_prs} outgoing PRs for {branch_name}")
                if outgoing_prs > 0:
                    prs = response.json()
                    for pr in prs:
                        self.debug(f"PR #{pr['number']}: {pr['html_url']}")
            
            # Only check incoming PRs for protected branches
            incoming_prs = 0
            if self._is_protected_branch(branch_name):
                params = {
                    "base": branch_name,
                    "state": "open"
                }
                response = requests.get(f"{self.github_api_url}/pulls", params=params, headers=headers)
                response.raise_for_status()
                incoming_prs = len(response.json())
                
                if self.verbose:
                    self.debug(f"Found {incoming_prs} incoming PRs for protected branch {branch_name}")
                    if incoming_prs > 0:
                        prs = response.json()
                        for pr in prs:
                            self.debug(f"Incoming PR #{pr['number']}: {pr['html_url']}")
            
            return outgoing_prs + incoming_prs
            
        except Exception as e:
            self.debug(f"Error checking PRs for {branch_name}: {e}")
            if self.verbose:
                self.debug(f"Full error: {str(e)}")
            return 0

    def _get_last_commit_date(self, branch_name: str) -> str:
        """Get the formatted date of the last commit on a branch."""
        try:
            branch = self.repo.heads[branch_name]
            commit_date = datetime.fromtimestamp(branch.commit.committed_date)
            return commit_date.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            print(f"Error getting last commit date for {branch_name}: {e}")
            return "unknown"
