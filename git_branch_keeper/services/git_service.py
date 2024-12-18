"""Git operations service"""
import git
from typing import List, Optional
from datetime import datetime
from pathlib import Path

class GitService:
    def __init__(self, repo_path: str = ".", verbose: bool = False):
        self.repo_path = repo_path
        self.verbose = verbose
        self.repo = self._get_repo()
        self._remote_refs_cache = None
        self._branch_merge_cache = {}

    def _get_repo(self) -> git.Repo:
        """Initialize Git repository from current directory."""
        try:
            # Try to find git repo by walking up the directory tree
            repo_path = Path(self.repo_path).resolve()
            while repo_path != repo_path.parent:
                try:
                    return git.Repo(repo_path, search_parent_directories=True)
                except git.InvalidGitRepositoryError:
                    repo_path = repo_path.parent
            
            # If we get here, we didn't find a git repo
            raise git.InvalidGitRepositoryError(f"No git repository found in {self.repo_path} or its parent directories")
        except Exception as e:
            self.debug(f"Error initializing git repo: {e}")
            raise ValueError("Not a Git repository")

    def get_main_branch(self, protected_branches: List[str]) -> str:
        """Determine the main branch name."""
        try:
            # Try each protected branch name
            for name in protected_branches:
                if name in [ref.name for ref in self.repo.refs]:
                    return name
            
            # If none found, return the first protected branch
            return protected_branches[0]
        except Exception as e:
            self.debug(f"Error determining main branch: {e}")
            return "main"

    def has_remote_branch(self, branch_name: str) -> bool:
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

    def is_merged(self, branch_name: str, main_branch: str) -> bool:
        """Check if a branch is merged into main with caching."""
        try:
            if branch_name in self._branch_merge_cache:
                return self._branch_merge_cache[branch_name]

            # Main branch is never considered merged
            if branch_name == main_branch:
                self._branch_merge_cache[branch_name] = False
                return False

            branch = self.repo.heads[branch_name]
            main = self.repo.heads[main_branch]
            
            # First check if branch is merged into local main
            is_merged = self.repo.is_ancestor(branch.commit, main.commit)
            
            # Then check if branch is merged into remote main
            if not is_merged:
                try:
                    remote_main = self.repo.refs[f'origin/{main_branch}']
                    is_merged = self.repo.is_ancestor(branch.commit, remote_main.commit)
                    if is_merged and self.verbose:
                        print(f"Branch {branch_name} is merged into remote main")
                except Exception as e:
                    if self.verbose:
                        print(f"Error checking remote main: {e}")

            # Cache the result
            self._branch_merge_cache[branch_name] = is_merged
            return is_merged

        except Exception as e:
            if self.verbose:
                print(f"Error checking if branch {branch_name} is merged: {e}")
            return False

    def get_branch_age(self, branch_name: str) -> int:
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

    def debug(self, message: str) -> None:
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            print(f"[Git] {message}") 

    def get_branch_sync_status(self, branch_name: str, main_branch: str = None) -> str:
        """Get the sync status between local and remote branch."""
        try:
            # Check if branch is merged first
            if self.is_merged(branch_name, main_branch):
                if self.verbose:
                    print(f"\n>>> get_branch_sync_status called for: {branch_name}")
                    print(f"Branch {branch_name} is merged, checking if remote exists...")
                # If branch is merged but remote still exists, show sync status
                if self.has_remote_branch(branch_name):
                    if self.verbose:
                        print("Remote exists, checking sync status...")
                    return self._check_sync_status(branch_name)
                else:
                    if self.verbose:
                        print("Remote is gone (normal for merged branches)")
                    return "merged-remote-deleted"
            
            # Not merged, check remote as normal
            if not self.has_remote_branch(branch_name):
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

    def get_last_commit_date(self, branch_name: str) -> str:
        """Get the formatted date of the last commit on a branch."""
        try:
            branch = self.repo.heads[branch_name]
            commit_date = datetime.fromtimestamp(branch.commit.committed_date)
            return commit_date.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            print(f"Error getting last commit date for {branch_name}: {e}")
            return "unknown"

    def delete_branch(self, branch_name: str, dry_run: bool = False) -> bool:
        """Delete a branch locally and remotely."""
        try:
            # Check if branch exists in remote
            remote_exists = self.has_remote_branch(branch_name)

            if dry_run:
                return True

            # Delete remote branch first if it exists
            if remote_exists:
                try:
                    self.repo.git.push("origin", "--delete", branch_name)
                except Exception as e:
                    self.debug(f"Error deleting remote branch {branch_name}: {e}")
                    return False

            # Delete local branch
            self.repo.git.branch("-D", branch_name)
            return True

        except Exception as e:
            self.debug(f"Error deleting branch {branch_name}: {e}")
            return False

    def update_main_branch(self, main_branch: str) -> bool:
        """Update the main branch from remote."""
        try:
            self.repo.git.checkout(main_branch)
            self.repo.git.pull("origin", main_branch)
            return True
        except git.GitCommandError as e:
            self.debug(f"Error updating main branch: {e}")
            return False

    def get_remote_branches(self) -> List[str]:
        """Get list of all remote branches."""
        try:
            self.debug("Fetching from origin...")
            self.repo.remotes.origin.fetch()
            
            # Get all remote branches, excluding HEAD and tags
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