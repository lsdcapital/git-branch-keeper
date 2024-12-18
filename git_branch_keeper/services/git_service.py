"""Git operations service"""
import git
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console

console = Console()

class GitService:
    """Service for Git operations."""

    def __init__(self, repo, config: dict):
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
        self.remote = self.repo.remote('origin')
        self._merge_status_cache = {}  # Cache for merge status checks
        # Add counters for merge detection methods
        self.merge_detection_stats = {
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

            # For non-protected branches, check merge status
            if hasattr(self, 'github_service') and self.github_service.was_merged_via_pr(branch_name):
                return "merged-pr"
            
            if self.is_merged_to_main(branch_name, main_branch):
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
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            if self.verbose:
                self.debug(f"Error getting last commit date for {branch_name}: {e}")
            return "unknown"

    def update_main_branch(self, main_branch: str) -> bool:
        """Update the main branch from remote."""
        try:
            if self.verbose:
                self.debug(f"Updating {main_branch} from remote...")
            self.remote.pull(main_branch)
            return True
        except Exception as e:
            if self.verbose:
                self.debug(f"Error updating {main_branch}: {e}")
            return False

    def get_remote_branches(self) -> list:
        """Get list of remote branches."""
        try:
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

    def get_branch_status_details(self, branch_name: str) -> dict:
        """Get detailed status of a branch."""
        try:
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

    def debug(self, message: str) -> None:
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            console.print(f"[Git] {message}")

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
        if self.merge_detection_stats['method1'] > 0:
            stats.append(f"Fast rev-list: {self.merge_detection_stats['method1']}")
        if self.merge_detection_stats['method2'] > 0:
            stats.append(f"Ancestor check: {self.merge_detection_stats['method2']}")
        if self.merge_detection_stats['method3'] > 0:
            stats.append(f"Commit message: {self.merge_detection_stats['method3']}")
        if self.merge_detection_stats['method4'] > 0:
            stats.append(f"All commits exist: {self.merge_detection_stats['method4']}")
        
        return f"Merges detected by: {', '.join(stats)}"

    def is_merged_to_main(self, branch_name: str, main_branch: str = 'main') -> bool:
        """Check if a branch is merged into main branch by checking commit history."""
        try:
            # Skip if it's a tag
            if self.is_tag(branch_name):
                if self.verbose:
                    self.debug(f"Skipping tag: {branch_name}")
                return False

            # Method 1: Fast merge detection using rev-list
            try:
                # Check if there are any commits in main that aren't in the branch
                result = self.repo.git.rev_list('--count', f'{branch_name}..{main_branch}')
                if result != '0':
                    # There are commits in main that aren't in the branch
                    # This means the branch is NOT fully merged
                    if self.verbose:
                        self.debug(f"[Method 1] Branch {branch_name} is not merged (has {result} commits not in main)")
                else:
                    if self.verbose:
                        self.debug(f"[Method 1] Branch {branch_name} is MERGED - no commits in main that aren't in branch")
                    self.merge_detection_stats['method1'] += 1
                    return True
            except git.exc.GitCommandError as e:
                if self.verbose:
                    self.debug(f"[Method 1] Error in fast merge check for {branch_name}: {e}")
                pass

            # Method 2: Check if branch tip is ancestor of main
            try:
                # Alternative approach using rev-list to check ancestry
                result = self.repo.git.rev_list('--count', f'{main_branch}..{branch_name}')
                if result == '0':
                    if self.verbose:
                        self.debug(f"[Method 2] Branch {branch_name} is MERGED - branch is ancestor of main")
                    self.merge_detection_stats['method2'] += 1
                    return True
                else:
                    if self.verbose:
                        self.debug(f"[Method 2] Branch {branch_name} is not merged (has {result} commits not in main)")
            except git.exc.GitCommandError:
                # Error in rev-list, continue checking
                if self.verbose:
                    self.debug(f"[Method 2] Rev-list check failed for {branch_name}")
                pass

            # Method 3: Look for merge commits mentioning the branch
            try:
                # Extract parts of the branch name
                full_name = branch_name
                org_name = self.repo.remotes.origin.url.split('/')[-2].split(':')[-1]
                feature_name = branch_name.split('/')[-1]
                ticket_number = next((part for part in branch_name.split('/') if part.startswith('DEV-')), None)
                
                # Build search patterns
                patterns = [
                    full_name,  # Full branch name
                    feature_name,  # Just the feature name
                    f"/{feature_name}",  # Path ending with feature name
                    f"from {org_name}/{full_name}",  # Full PR merge pattern
                    f"from {org_name}/feature/{ticket_number}" if ticket_number else None,  # PR merge with ticket
                    ticket_number if ticket_number else None,  # Just the ticket number
                    f"Merge branch '{branch_name}'",  # Direct merge message
                    f"Merge pull request.*from.*{feature_name}",  # PR merge with feature name
                ]
                
                # Remove None values
                patterns = [p for p in patterns if p]
                
                if self.verbose:
                    self.debug(f"[Method 3] Searching for patterns in commit messages: {patterns}")
                
                for pattern in patterns:
                    # Look for both merge commits and squash commits
                    for search_type in ['--merges', '--no-merges']:
                        merge_commits = self.repo.git.log(
                            main_branch,
                            search_type,  # Try both merge commits and non-merge commits (squash merges)
                            '--grep', pattern,  # Look for pattern in commit message
                            '--format=%H',  # Only show commit hashes
                            n=1  # Stop after finding one
                        )
                        
                        if merge_commits:
                            if self.verbose:
                                self.debug(f"[Method 3] Branch {branch_name} is MERGED - found {search_type} commit matching '{pattern}'")
                            self.merge_detection_stats['method3'] += 1
                            return True

                # Method 4: Check if all commits from branch exist in main
                try:
                    branch_commits = set(self.repo.git.rev_list(branch_name).split())
                    main_commits = set(self.repo.git.rev_list(main_branch).split())
                    if branch_commits.issubset(main_commits):
                        if self.verbose:
                            self.debug(f"[Method 4] Branch {branch_name} is MERGED - all commits exist in main")
                        self.merge_detection_stats['method4'] += 1
                        return True
                    else:
                        if self.verbose:
                            self.debug(f"[Method 4] Branch {branch_name} is not merged - some commits missing from main")
                except Exception as e:
                    if self.verbose:
                        self.debug(f"[Method 4] Error checking commit existence: {e}")

                if self.verbose:
                    self.debug(f"[Method 3] No merge commits found for {branch_name} in {main_branch}")
                return False

            except Exception as e:
                if self.verbose:
                    self.debug(f"[Method 3] Error checking merge commits for {branch_name}: {e}")
                return False

        except Exception as e:
            if self.verbose:
                self.debug(f"Error checking merge status for {branch_name}: {e}")
            return False