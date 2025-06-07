"""Git operations service"""
import git
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console
import re

console = Console()

class GitService:
    """Service for Git operations."""

    def __init__(self, repo, config):
        """Initialize the service."""
        try:
            if isinstance(repo, str):
                self.repo = git.Repo(repo)
            else:
                self.repo = repo
        except Exception as e:
            raise Exception(f"Error initializing git repo: {e}")
        
        self.config = config
        self.verbose = config.get('verbose', False)
        self.debug_mode = config.get('debug', False)
        self.remote = self.repo.remote('origin')
        self._merge_status_cache = {}  # Cache for merge status checks
        # Add counters for merge detection methods
        self.merge_detection_stats = {
            'method0': 0,  # Squash merge detection
            'method1': 0,  # Fast rev-list
            'method2': 0,  # Ancestor check
            'method3': 0,  # Commit message search
            'method4': 0,  # All commits exist
        }
        if self.verbose:
            self.debug("Git service initialized")

    def has_remote_branch(self, branch_name: str) -> bool:
        """Check if the branch has a remote tracking branch."""
        try:
            # First check if the remote ref exists
            remote_ref_name = f"origin/{branch_name}"
            if remote_ref_name not in [ref.name for ref in self.remote.refs]:
                return False

            # Then try to get the remote branch
            try:
                self.repo.refs[f"origin/{branch_name}"]
                return True
            except (IndexError, KeyError):
                return False
        except Exception as e:
            if self.verbose:
                self.debug(f"Error checking remote branch {branch_name}: {e}")
            return False

    def get_branch_age(self, branch_name: str) -> int:
        """Get age of branch in days."""
        try:
            commit = self.repo.refs[branch_name].commit
            commit_time = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            age = datetime.now(timezone.utc) - commit_time
            return age.days
        except Exception as e:
            if self.verbose:
                self.debug(f"Error getting branch age for {branch_name}: {e}")
            return 0

    def get_branch_sync_status(self, branch_name: str, main_branch: str) -> str:
        """Get sync status of branch with remote."""
        try:
            # Skip merge checks for protected branches
            if branch_name in self.config.get('protected_branches', ['main', 'master']):
                if not self.has_remote_branch(branch_name):
                    return "local-only"
                
                # For protected branches, just check sync status
                ahead = list(self.repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
                behind = list(self.repo.iter_commits(f"{branch_name}..origin/{branch_name}"))
                
                if ahead and behind:
                    return "diverged"
                elif ahead:
                    return f"ahead {len(ahead)}"
                elif behind:
                    return f"behind {len(behind)}"
                else:
                    return "synced"

            # For non-protected branches, check PR status first
            if hasattr(self, 'github_service'):
                pr_status = self.github_service.get_branch_pr_status(branch_name)
                if pr_status['has_pr']:
                    if pr_status['state'] == 'merged':
                        return "merged-pr"
                    elif pr_status['state'] == 'closed':
                        # PR was closed without merging
                        return "closed-unmerged"
            
            if self.is_branch_merged(branch_name, main_branch):
                return "merged-git"
            
            # Check if branch exists on remote
            if not self.has_remote_branch(branch_name):
                return "local-only"

            # Only proceed with sync check if remote branch exists
            local_branch = self.repo.refs[branch_name]
            remote_branch = self.repo.refs[f"origin/{branch_name}"]
            
            ahead = list(self.repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
            behind = list(self.repo.iter_commits(f"{branch_name}..origin/{branch_name}"))
            
            if ahead and behind:
                return "diverged"
            elif ahead:
                return f"ahead {len(ahead)}"
            elif behind:
                return f"behind {len(behind)}"
            else:
                return "synced"
        except Exception as e:
            if self.verbose:
                self.debug(f"Error checking sync status for {branch_name}: {e}")
            return "local-only"  # Return local-only instead of unknown for better UX

    def get_last_commit_date(self, branch_name: str) -> str:
        """Get the date of the last commit on a branch."""
        try:
            commit = self.repo.refs[branch_name].commit
            dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception as e:
            if self.verbose:
                self.debug(f"Error getting last commit date for {branch_name}: {e}")
            return "unknown"

    def update_main_branch(self, main_branch: str) -> bool:
        """Update the main branch from remote."""
        import git_branch_keeper.core as core
        
        try:
            core.in_git_operation = True
            if self.verbose:
                self.debug(f"Updating {main_branch} from remote...")
            self.remote.pull(main_branch)
            return True
        except Exception as e:
            if self.verbose:
                self.debug(f"Error updating {main_branch}: {e}")
            return False
        finally:
            core.in_git_operation = False

    def get_remote_branches(self) -> list:
        """Get list of remote branches."""
        import git_branch_keeper.core as core
        
        try:
            core.in_git_operation = True
            if self.verbose:
                self.debug("Fetching remote branches...")
            self.remote.fetch()
            branches = [ref.name for ref in self.remote.refs]
            if self.verbose:
                self.debug(f"Found {len(branches)} remote branches")
            return branches
        except Exception as e:
            if self.verbose:
                self.debug(f"Error fetching remote branches: {e}")
            return []
        finally:
            core.in_git_operation = False

    def get_branch_status_details(self, branch_name: str) -> dict:
        """Get detailed status of a branch."""
        import git_branch_keeper.core as core
        
        try:
            core.in_git_operation = True
            current = self.repo.active_branch.name
            if current != branch_name:
                self.repo.git.checkout(branch_name)
            
            status = self.repo.git.status('--porcelain')
            
            if current != branch_name:
                self.repo.git.checkout(current)
            
            return {
                'modified': bool([line for line in status.split('\n') if line.startswith(' M')]),
                'untracked': bool([line for line in status.split('\n') if line.startswith('??')]),
                'staged': bool([line for line in status.split('\n') if line.startswith('M ')])
            }
        except Exception as e:
            if self.verbose:
                self.debug(f"Error getting status details for {branch_name}: {e}")
            return {'modified': False, 'untracked': False, 'staged': False}
        finally:
            core.in_git_operation = False

    def debug(self, message: str, source: str = "Git") -> None:
        """Print debug message if debug mode is enabled."""
        if self.debug_mode:
            print(f"[{source}] {message}")

    def is_tag(self, ref_name: str) -> bool:
        """Check if a reference is a tag."""
        try:
            return ref_name in [tag.name for tag in self.repo.tags]
        except Exception as e:
            if self.verbose:
                self.debug(f"Error checking if {ref_name} is a tag: {e}")
            return False

    def get_merge_stats(self) -> str:
        """Get a summary of which methods detected merges."""
        total = sum(self.merge_detection_stats.values())
        if total == 0:
            return "No merges detected"
        
        stats = []
        method_names = {
            'method0': 'Squash merge',
            'method1': 'Fast rev-list',
            'method2': 'Tip reachable',
            'method3': 'Merge commit',
            'method4': 'Ancestor check'
        }
        
        for method, count in self.merge_detection_stats.items():
            if count > 0:
                stats.append(f"{method_names[method]}: {count}")
        
        return f"Merges detected by: {', '.join(stats)}"

    def is_branch_merged(self, branch_name: str, main_branch: str) -> bool:
        """Check if a branch is merged using multiple methods, ordered by speed."""
        # Check cache first
        cache_key = f"{branch_name}:{main_branch}"
        if cache_key in self._merge_status_cache:
            return self._merge_status_cache[cache_key]

        try:
            # Skip if it's a tag
            if self.is_tag(branch_name):
                if self.debug_mode:
                    self.debug(f"Skipping tag: {branch_name}")
                self._merge_status_cache[cache_key] = False
                return False

            # Method 0: Check for squash merge by comparing changes
            self.debug("[Method 0] Checking for squash merge...")
            try:
                # Get all commits on the branch that aren't on main
                branch_commits = list(self.repo.iter_commits(f'{main_branch}..{branch_name}'))
                if not branch_commits:
                    # No unique commits, might already be merged
                    return False
                
                # Get the combined diff of all branch commits
                branch_diff = self.repo.git.diff(f'{main_branch}...{branch_name}', '--no-color')
                
                if branch_diff:
                    # Search recent commits in main for similar changes
                    for commit in self.repo.iter_commits(main_branch, max_count=100):
                        try:
                            # Get the diff introduced by this commit
                            commit_diff = self.repo.git.show(commit.hexsha, '--no-color', '--format=')
                            
                            # Check if the diffs are substantially similar
                            # This is a heuristic - if the branch diff is contained in the commit diff,
                            # it's likely a squash merge
                            if len(branch_diff) > 50 and branch_diff in commit_diff:
                                self.debug(f"[Method 0] Found squash merge in commit {commit.hexsha}")
                                self.merge_detection_stats['method0'] += 1
                                self._merge_status_cache[cache_key] = True
                                return True
                        except:
                            continue
            except git.exc.GitCommandError as e:
                self.debug(f"[Method 0] Error checking squash merge: {e}")

            # Method 0.5: Check if branch was deleted on remote (common after PR merge)
            self.debug("[Method 0.5] Checking if branch was deleted on remote...")
            try:
                # If branch exists locally but not on remote, it might have been merged and deleted
                if not self.has_remote_branch(branch_name):
                    # Check if it ever existed on remote by looking at tracking info
                    try:
                        tracking = self.repo.git.config('--get', f'branch.{branch_name}.merge')
                        if tracking:
                            self.debug(f"[Method 0.5] Branch {branch_name} was tracking remote but remote is gone (likely merged)")
                            # Don't count this as definitive merge, but it's a strong hint
                            # Continue to other methods for confirmation
                    except:
                        pass
            except:
                pass

            # Method 1: Fast check using rev-list (fastest)
            self.debug("[Method 1] Using fast rev-list check...")
            try:
                result = self.repo.git.rev_list('--count', f'{main_branch}..{branch_name}')
                if result == '0':
                    self.debug(f"[Method 1] Branch {branch_name} is merged (fast rev-list)")
                    self.merge_detection_stats['method1'] += 1
                    self._merge_status_cache[cache_key] = True
                    return True
            except git.exc.GitCommandError:
                pass

            # Method 2: Check if branch tip is ancestor of main (also fast)
            self.debug("[Method 2] Checking if branch tip is ancestor...")
            try:
                branch_tip = self.repo.refs[branch_name].commit
                is_ancestor = self.repo.is_ancestor(branch_tip, self.repo.refs[main_branch].commit)
                if is_ancestor:
                    self.debug(f"[Method 2] Branch {branch_name} is merged (tip is ancestor)")
                    self.merge_detection_stats['method2'] += 1
                    self._merge_status_cache[cache_key] = True
                    return True
            except:
                pass

            # Method 3: Check merge commit messages (slower but catches merge commits)
            self.debug("[Method 3] Checking merge commit messages...")
            merge_patterns = [
                f"Merge branch '{branch_name}'",
                f'Merge pull request .* from .*/{branch_name}',
                f'Merge pull request .* from .*:{branch_name}'
            ]
            for commit in self.repo.iter_commits(main_branch, max_count=100):
                for pattern in merge_patterns:
                    if re.search(pattern, commit.message):
                        self.debug(f"[Method 3] Found merge commit: {commit.message.splitlines()[0]}")
                        self.merge_detection_stats['method3'] += 1
                        self._merge_status_cache[cache_key] = True
                        return True

            # Method 4: Full commit history check (slowest)
            self.debug("[Method 4] Checking full commit history...")
            try:
                branch_commits = set(self.repo.git.rev_list(branch_name).split())
                main_commits = set(self.repo.git.rev_list(main_branch).split())
                if branch_commits.issubset(main_commits):
                    self.debug(f"[Method 4] Branch {branch_name} is merged (all commits in main)")
                    self.merge_detection_stats['method4'] += 1
                    self._merge_status_cache[cache_key] = True
                    return True
            except Exception as e:
                self.debug(f"[Method 4] Error checking commit history: {e}")

            self._merge_status_cache[cache_key] = False
            return False
        except Exception as e:
            self.debug(f"Error checking if branch is merged: {e}")
            self._merge_status_cache[cache_key] = False
            return False

    def delete_branch(self, branch_name: str, dry_run: bool = False) -> bool:
        """Delete a branch locally and remotely if it exists."""
        import git_branch_keeper.core as core
        
        try:
            # Set flag to indicate we're in a git operation
            core.in_git_operation = True
            
            # Delete local branch
            if not dry_run:
                console.print(f"Deleting local branch {branch_name}...")
                self.repo.delete_head(branch_name, force=True)
            
            # Delete remote branch if it exists
            if self.has_remote_branch(branch_name):
                if not dry_run:
                    console.print(f"Deleting remote branch {branch_name}...")
                    self.remote.push(refspec=f":{branch_name}")
                    console.print(f"[green]Deleted branch {branch_name} (local and remote)[/green]")
                else:
                    console.print(f"[yellow]Would delete branch {branch_name} (local and remote)[/yellow]")
            else:
                if not dry_run:
                    console.print(f"[green]Deleted branch {branch_name} (local only)[/green]")
                else:
                    console.print(f"[yellow]Would delete branch {branch_name} (local only)[/yellow]")
            
            return True
            
        except Exception as e:
            console.print(f"[red]Error deleting branch {branch_name}: {e}[/red]")
            return False
        finally:
            # Clear the flag
            core.in_git_operation = False