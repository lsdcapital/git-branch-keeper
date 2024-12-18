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
        """Check if a branch exists in the remote."""
        try:
            # Even in bypass mode, we want to check remote refs
            # We just skip the API calls
            remote_refs = [ref.name for ref in self.repo.remotes.origin.refs]
            return f'origin/{branch_name}' in remote_refs
        except Exception as e:
            self.debug(f"Error checking remote branch {branch_name}: {e}")
            return False

    def _get_branch_status(self, branch, local_branches, remote_branches):
        """Get status of a branch."""
        try:
            # Main branch and protected branches are always active
            if branch == self.main_branch or self._is_protected_branch(branch):
                return "active"
            
            # Check if branch is merged
            if self._is_merged(branch):
                return "merged"
            
            # Check if branch is stale
            if self.min_stale_days > 0:
                _, age_days = self._get_branch_age(branch)
                if age_days >= self.min_stale_days:
                    return "stale"
            
            return "active"
        except Exception as e:
            self.debug(f"Error getting branch status for {branch}: {e}")
            return "unknown"

    def _should_process_branch(self, branch, status):
        """Determine if a branch should be processed based on status filter."""
        if self.status_filter == "all":
            return status in ["merged", "stale"]
        return status == self.status_filter

    def _get_branch_age(self, branch_name: str) -> Tuple[datetime, int]:
        """Get the age of a branch."""
        try:
            commit = self.repo.refs[branch_name].commit
            commit_date = datetime.fromtimestamp(commit.committed_date)
            age_days = (datetime.now() - commit_date).days
            return commit_date, age_days
        except Exception as e:
            self.debug(f"Error getting branch age for {branch_name}: {e}")
            return datetime.now(), 0  # Return current time and 0 days as fallback

    def _is_merged(self, branch_name: str) -> bool:
        """Check if a branch is merged into main."""
        try:
            # Main branch is never considered merged
            if branch_name == self.main_branch:
                return False

            branch = self.repo.heads[branch_name]
            
            # Always try to fetch first to get latest refs
            try:
                self.repo.remotes.origin.fetch()
            except Exception as e:
                self.debug(f"Error fetching from remote: {e}")

            # Check if this branch's upstream is gone (was likely merged)
            try:
                tracking_branch = branch.tracking_branch()
                if tracking_branch and tracking_branch.name not in [ref.name for ref in self.repo.remotes.origin.refs]:
                    if self.verbose:
                        self.debug(f"Branch {branch_name}'s upstream is gone (likely merged)")
                    return True
            except Exception as e:
                self.debug(f"Error checking tracking branch: {e}")

            # First check if branch is merged into local main
            main = self.repo.heads[self.main_branch]
            if self.repo.is_ancestor(branch.commit, main.commit):
                if self.verbose:
                    self.debug(f"Branch {branch_name} is merged into local main")
                return True

            # Then check if branch is merged into remote main
            try:
                remote_main = self.repo.refs[f'origin/{self.main_branch}']
                if self.repo.is_ancestor(branch.commit, remote_main.commit):
                    if self.verbose:
                        self.debug(f"Branch {branch_name} is merged into remote main")
                    return True
            except Exception as e:
                self.debug(f"Error checking remote main: {e}")

            if self.verbose:
                # Log commit counts for debugging
                behind_commits = list(self.repo.iter_commits(f'{branch.name}..{main.name}'))
                ahead_commits = list(self.repo.iter_commits(f'{main.name}..{branch.name}'))
                self.debug(f"Branch {branch_name} is {len(behind_commits)} commits behind and {len(ahead_commits)} commits ahead of local main")
                
                try:
                    remote_behind = list(self.repo.iter_commits(f'{branch.name}..origin/{main.name}'))
                    remote_ahead = list(self.repo.iter_commits(f'origin/{main.name}..{branch.name}'))
                    self.debug(f"Branch {branch_name} is {len(remote_behind)} commits behind and {len(remote_ahead)} commits ahead of remote main")
                except Exception as e:
                    self.debug(f"Error checking remote commit counts: {e}")
            
            return False

        except Exception as e:
            self.debug(f"Error checking if branch {branch_name} is merged: {e}")
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

    def _print_branch_table(self, local_branches):
        """Print table of branches with their status."""
        # Set table title based on filter
        title = {
            "local": "Local Branches",
            "remote": "Remote-Only Branches",
            "all": "All Branches"
        }[self.show_filter]
        
        table = Table(
            "Branch",
            "Last Commit Date",
            "Age (days)",
            "Status",
            "Remote",
            "Sync Status",
            "PRs",
            title=title,
            title_style="bold",
        )

        branches_to_process = []
        
        # Get all remote branches if needed
        remote_branches = set()
        if self.show_filter in ["remote", "all"]:
            remote_branches = set(self._get_remote_branches() or [])  # Ensure we have a set even if None is returned
            self.debug(f"Remote branches to check: {remote_branches}")
        
        # Show local branches if requested
        if self.show_filter in ["local", "all"]:
            for branch in local_branches:
                try:
                    commit = self.repo.refs[branch].commit
                    commit_date, age_days = self._get_branch_age(branch)
                    
                    if self.verbose:
                        self.debug(f"Branch {branch} age: {age_days} days")

                    remote_exists = self._has_remote_branch(branch)
                    status = self._get_branch_status(branch, local_branches, remote_branches)
                    sync_status = self._get_branch_sync_status(branch)
                    
                    if remote_exists and branch in remote_branches:
                        remote_branches.discard(branch)  # Remove from remote set as we've handled it
                    
                    pr_count = self._get_incoming_prs(branch)
                    
                    # Create branch name with link if it has a remote
                    branch_display = branch
                    if self.github_repo and remote_exists:
                        github_url = self._get_github_branch_url(branch, "tree")
                        branch_display = f"[blue][link={github_url}]{branch}[/link][/blue]"
                    
                    # Create PR count with link if there are PRs
                    pr_display = ""
                    if self.bypass_github:
                        pr_display = "[dim]disabled[/dim]"
                    elif pr_count is not None:
                        if pr_count > 0:
                            github_url = self._get_github_branch_url(branch, "pulls")
                            pr_display = f"[blue][link={github_url}]{pr_count}[/link][/blue]"
                        else:
                            pr_display = "0"

                    # Add to process list if it matches criteria and is a local branch
                    if (branch != self.repo.active_branch.name and 
                        self._should_process_branch(branch, status) and 
                        branch in local_branches):
                        branches_to_process.append((branch, status))

                    # Add row to table with yellow highlight for branches that could be cleaned up
                    style = "yellow" if branch != self.repo.active_branch.name and self._should_process_branch(branch, status) else None
                    table.add_row(
                        branch_display + (" *" if branch == self.repo.active_branch.name else ""),
                        commit_date.strftime("%Y-%m-%d %H:%M"),
                        str(age_days),
                        status,
                        "✓" if remote_exists else "✗",
                        sync_status,
                        pr_display,
                        style=style
                    )

                except Exception as e:
                    self.debug(f"Error processing branch {branch}: {e}")
                    continue

        # Always print the table even if no branches to process
        console.print("")
        console.print(table)
        console.print("\n✓ = Has remote branch  ✗ = Local only")
        console.print("↑ = Unpushed commits  ↓ = Commits to pull")
        if len(branches_to_process) > 0:
            if self.show_filter == "remote":
                console.print("[yellow]Note: Remote branches cannot be cleaned up directly[/yellow]")
            else:
                console.print("* = Current branch  [yellow]Yellow[/yellow] = Would be cleaned up")
        if self.min_stale_days > 0:
            console.print("")  # Add newline before stale days message
            console.print(f"Branches older than {self.min_stale_days} days are marked as stale")

        console.print("")  # Single empty line before summary
        return branches_to_process

    def process_branches(self, cleanup_enabled=False):
        """Process all branches and handle deletions."""
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

        if self.verbose:
            self.debug(f"Found branches: {local_branches}")
            self.debug(f"Current branch: {current}")
            self.debug(f"Protected branches: {self.protected_branches}")
            self.debug(f"Ignore patterns: {self.ignore_patterns}")

        # Get remote branches (if any)
        remote_branches = self._get_remote_branches()
        if remote_branches is None:
            remote_branches = []

        # Show branch table and get branches to process
        if self.show_filter == "remote" and not remote_branches:
            console.print("[yellow]No remote branches found[/yellow]")
            return

        branches_to_process = self._print_branch_table(local_branches) or []  # Ensure we have a list even if None is returned
        
        # Print summary of branch cleanup operation
        console.print("Summary:")
        if self.show_filter == "local":
            console.print(f"- Found {len(local_branches)} local branch(es)")
        elif self.show_filter == "remote":
            console.print(f"- Found {len(remote_branches)} remote branch(es)")
        else:  # all
            console.print(f"- Found {len(local_branches)} local and {len(remote_branches)} remote branch(es)")
            
        if len(branches_to_process) > 0:
            merged_count = sum(1 for _, status in branches_to_process if status == "merged")
            stale_count = sum(1 for _, status in branches_to_process if status == "stale")
            
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
        
        # Only process branches if cleanup is enabled
        if cleanup_enabled and branches_to_process:
            # In force mode, proceed without confirmation
            # Otherwise, if interactive, ask for confirmation first
            if self.force_mode or not self.interactive:
                console.print("\n[bold]Cleaning up branches:[/bold]")
                for branch, reason in branches_to_process:
                    self.delete_branch(branch, reason)
                console.print("\nCleanup complete!")
            else:
                # Interactive mode - ask for confirmation
                console.print("\n[bold]Cleaning up branches:[/bold]")
                branches_deleted = False
                for branch, reason in branches_to_process:
                    if console.input(f"Delete {branch} ({reason})? [y/N] ").lower().startswith('y'):
                        self.delete_branch(branch, reason)
                        branches_deleted = True
                
                if branches_deleted:
                    console.print("\nCleanup complete!")
                else:
                    console.print("\nNo branches were deleted.")
        
        console.print("")  # Add empty line at the end

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
            if not self._has_remote_branch(branch_name):
                return "local-only"

            local_branch = self.repo.refs[branch_name]
            remote_ref = self.repo.refs[f"origin/{branch_name}"]
            
            # Get the counts of commits ahead and behind
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
        except Exception as e:
            self.debug(f"Error getting sync status for {branch_name}: {e}")
            return "unknown"

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

    def _get_incoming_prs(self, branch_name: str) -> int:
        """Get count of open PRs for this branch."""
        if self.bypass_github or not (self.github_api_url and self.github_token):
            return 0

        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            url = f"{self.github_api_url}/pulls"
            
            # Check for PRs where this branch is the source (head)
            params = {
                "head": f"{self.github_repo.split('/')[0]}:{branch_name}",
                "state": "open"
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            outgoing_prs = len(response.json())
            
            # Check for PRs where this branch is the target (base)
            params = {
                "base": branch_name,
                "state": "open"
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            incoming_prs = len(response.json())
            
            return outgoing_prs + incoming_prs
            
        except Exception as e:
            self.debug(f"Error checking PRs for {branch_name}: {e}")
            return 0
