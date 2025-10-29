"""Git operations service"""
import git
from datetime import datetime, timezone
from contextlib import contextmanager
from rich.console import Console
from typing import Union, TYPE_CHECKING, Dict
import re
from threading import Lock

from git_branch_keeper.models.branch import SyncStatus
from git_branch_keeper.logging_config import get_logger

if TYPE_CHECKING:
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)

class GitService:
    """Service for Git operations."""

    def __init__(self, repo_path: str, config: Union['Config', dict]):
        """Initialize the service.

        Args:
            repo_path: Path to the git repository (string path, not repo object)
            config: Configuration dictionary or Config object
        """
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get('verbose', False)
        self.debug_mode = config.get('debug', False)
        self.remote_name = 'origin'  # Store remote name, not object
        self.in_git_operation = False  # Track if operation is in progress
        self._merge_status_cache: Dict[str, bool] = {}  # Cache for merge status checks
        self._cache_lock = Lock()  # Thread safety for cache access
        # Add counters for merge detection methods
        self.merge_detection_stats = {
            'method0': 0,  # Squash merge detection
            'method1': 0,  # Fast rev-list
            'method2': 0,  # Ancestor check
            'method3': 0,  # Commit message search
            'method4': 0,  # All commits exist
        }
        self._stats_lock = Lock()  # Thread safety for stats access
        logger.info("Git service initialized")

    def _get_repo(self):
        """Get a thread-safe git.Repo instance.

        Creates a new repo instance for each call to ensure thread safety.
        GitPython repos are lightweight - they don't clone, just open the existing repo.

        Returns:
            git.Repo: A fresh repository instance
        """
        return git.Repo(self.repo_path)

    @contextmanager
    def _git_operation(self):
        """Context manager to track git operations."""
        self.in_git_operation = True
        try:
            yield
        finally:
            self.in_git_operation = False

    def _get_from_cache(self, key: str):
        """Thread-safe cache read."""
        with self._cache_lock:
            return self._merge_status_cache.get(key)

    def _set_in_cache(self, key: str, value: bool):
        """Thread-safe cache write."""
        with self._cache_lock:
            self._merge_status_cache[key] = value

    def _check_cache(self, key: str) -> tuple[bool, bool]:
        """Thread-safe cache check. Returns (found, value)."""
        with self._cache_lock:
            if key in self._merge_status_cache:
                return (True, self._merge_status_cache[key])
            return (False, False)

    def _increment_stat(self, method: str):
        """Thread-safe stats increment."""
        with self._stats_lock:
            self.merge_detection_stats[method] += 1

    def has_remote_branch(self, branch_name: str) -> bool:
        """Check if the branch has a remote tracking branch."""
        try:
            repo = self._get_repo()
            remote = repo.remote(self.remote_name)

            # First check if the remote ref exists
            remote_ref_name = f"origin/{branch_name}"
            if remote_ref_name not in [ref.name for ref in remote.refs]:
                return False

            # Then try to get the remote branch
            try:
                repo.refs[f"origin/{branch_name}"]
                return True
            except (IndexError, KeyError):
                return False
        except Exception as e:
            logger.debug(f"Error checking remote branch {branch_name}: {e}")
            return False

    def get_branch_age(self, branch_name: str) -> int:
        """Get age of branch in calendar days."""
        try:
            repo = self._get_repo()
            commit = repo.refs[branch_name].commit
            commit_time = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            now = datetime.now(timezone.utc)

            # Calculate age based on calendar dates for consistent results
            commit_date = commit_time.date()
            today = now.date()
            age = (today - commit_date).days

            return age
        except Exception as e:
            logger.debug(f"Error getting branch age for {branch_name}: {e}")
            return 0

    def get_branch_sync_status(self, branch_name: str, main_branch: str) -> str:
        """Get sync status of branch with remote."""
        try:
            repo = self._get_repo()

            # Skip merge checks for protected branches
            if branch_name in self.config.get('protected_branches', ['main', 'master']):
                if not self.has_remote_branch(branch_name):
                    return SyncStatus.LOCAL_ONLY.value

                # For protected branches, just check sync status
                ahead = list(repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
                behind = list(repo.iter_commits(f"{branch_name}..origin/{branch_name}"))

                if ahead and behind:
                    return SyncStatus.DIVERGED.value
                elif ahead:
                    return f"ahead {len(ahead)}"
                elif behind:
                    return f"behind {len(behind)}"
                else:
                    return SyncStatus.SYNCED.value

            # Check if branch is merged into main
            if self.is_branch_merged(branch_name, main_branch):
                return SyncStatus.MERGED_GIT.value

            # Check if branch exists on remote
            if not self.has_remote_branch(branch_name):
                return SyncStatus.LOCAL_ONLY.value

            # Check ahead/behind status
            ahead = list(repo.iter_commits(f"origin/{branch_name}..{branch_name}"))
            behind = list(repo.iter_commits(f"{branch_name}..origin/{branch_name}"))

            if ahead and behind:
                return SyncStatus.DIVERGED.value
            elif ahead:
                return f"ahead {len(ahead)}"
            elif behind:
                return f"behind {len(behind)}"
            else:
                return SyncStatus.SYNCED.value
        except Exception as e:
            logger.debug(f"Error checking sync status for {branch_name}: {e}")
            return SyncStatus.LOCAL_ONLY.value  # Return local-only instead of unknown for better UX

    def get_last_commit_date(self, branch_name: str) -> str:
        """Get the date of the last commit on a branch."""
        try:
            repo = self._get_repo()
            commit = repo.refs[branch_name].commit
            dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception as e:
            logger.debug(f"Error getting last commit date for {branch_name}: {e}")
            return "unknown"

    def update_main_branch(self, main_branch: str) -> bool:
        """Update the main branch from remote."""
        with self._git_operation():
            try:
                repo = self._get_repo()
                remote = repo.remote(self.remote_name)
                logger.debug(f"Updating {main_branch} from remote...")
                remote.pull(main_branch)
                return True
            except Exception as e:
                logger.debug(f"Error updating {main_branch}: {e}")
                return False

    def get_remote_branches(self) -> list:
        """Get list of remote branches."""
        with self._git_operation():
            try:
                repo = self._get_repo()
                remote = repo.remote(self.remote_name)
                logger.debug("Fetching remote branches...")
                remote.fetch()
                branches = [ref.name for ref in remote.refs]
                logger.debug(f"Found {len(branches)} remote branches")
                return branches
            except Exception as e:
                logger.debug(f"Error fetching remote branches: {e}")
                return []

    def get_branch_status_details(self, branch_name: str) -> dict:
        """Get detailed status of a branch."""
        with self._git_operation():
            try:
                repo = self._get_repo()

                # Handle detached HEAD state
                try:
                    current = repo.active_branch.name
                    is_detached = False
                except TypeError:
                    # Detached HEAD - remember the current commit
                    current = repo.head.commit.hexsha
                    is_detached = True

                # Only checkout if different
                if (not is_detached and current != branch_name) or is_detached:
                    repo.git.checkout(branch_name)

                status = repo.git.status('--porcelain')

                # Restore previous state
                if (not is_detached and current != branch_name) or is_detached:
                    repo.git.checkout(current)

                return {
                    'modified': bool([line for line in status.split('\n') if line.startswith(' M')]),
                    'untracked': bool([line for line in status.split('\n') if line.startswith('??')]),
                    'staged': bool([line for line in status.split('\n') if line.startswith('M ')])
                }
            except Exception as e:
                logger.debug(f"Error getting status details for {branch_name}: {e}")
                return {'modified': False, 'untracked': False, 'staged': False}

    def is_tag(self, ref_name: str) -> bool:
        """Check if a reference is a tag."""
        try:
            repo = self._get_repo()
            # Strip refs/tags/ prefix if present
            tag_name = ref_name.replace('refs/tags/', '')
            return tag_name in [tag.name for tag in repo.tags]
        except Exception as e:
            logger.debug(f"Error checking if {ref_name} is a tag: {e}")
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
        # Check cache first (thread-safe)
        cache_key = f"{branch_name}:{main_branch}"
        found, value = self._check_cache(cache_key)
        if found:
            return value

        try:
            # Skip if it's a tag
            if self.is_tag(branch_name):
                logger.debug(f"Skipping tag: {branch_name}")
                self._set_in_cache(cache_key, False)
                return False

            # Try each detection method in order (fastest first)
            methods = [
                self._check_squash_merge,
                self._check_remote_deletion,
                self._check_fast_revlist,
                self._check_ancestor,
                self._check_merge_commit_message,
                self._check_full_commit_history
            ]

            for method in methods:
                result = method(branch_name, main_branch)
                if result:
                    self._set_in_cache(cache_key, True)
                    return True

            self._set_in_cache(cache_key, False)
            return False
        except Exception as e:
            logger.debug(f"Error checking if branch is merged: {e}")
            self._set_in_cache(cache_key, False)
            return False

    def _check_squash_merge(self, branch_name: str, main_branch: str) -> bool:
        """Method 0: Check for squash merge by comparing diffs."""
        logger.debug("[Method 0] Checking for squash merge...")
        try:
            repo = self._get_repo()
            # Get all commits on the branch that aren't on main
            branch_commits = list(repo.iter_commits(f'{main_branch}..{branch_name}'))
            if not branch_commits:
                return False

            # Get the combined diff of all branch commits
            branch_diff = repo.git.diff(f'{main_branch}...{branch_name}', '--no-color')

            if not branch_diff:
                return False

            # Search recent commits in main for similar changes
            for commit in repo.iter_commits(main_branch, max_count=100):
                try:
                    commit_diff = repo.git.show(commit.hexsha, '--no-color', '--format=')

                    # If the branch diff is contained in the commit diff, likely a squash merge
                    if len(branch_diff) > 50 and branch_diff in commit_diff:
                        logger.debug(f"[Method 0] Found squash merge in commit {commit.hexsha}")
                        self._increment_stat('method0')
                        return True
                except Exception as e:
                    logger.debug(f"[Method 0] Error processing commit {commit.hexsha}: {e}")
                    continue
        except git.exc.GitCommandError as e:
            logger.debug(f"[Method 0] Error checking squash merge: {e}")

        return False

    def _check_remote_deletion(self, branch_name: str, main_branch: str) -> bool:
        """Method 0.5: Check if branch was deleted on remote (hint only)."""
        logger.debug("[Method 0.5] Checking if branch was deleted on remote...")
        try:
            repo = self._get_repo()
            if not self.has_remote_branch(branch_name):
                # Check if it ever existed on remote
                try:
                    tracking = repo.git.config('--get', f'branch.{branch_name}.merge')
                    if tracking:
                        logger.debug(f"[Method 0.5] Branch {branch_name} was tracking remote but remote is gone")
                        # This is a hint, not definitive - continue to other methods
                except Exception as e:
                    logger.debug(f"[Method 0.5] Error getting tracking info: {e}")
        except Exception as e:
            logger.debug(f"[Method 0.5] Error checking remote branch: {e}")

        return False  # Not a definitive check

    def _check_fast_revlist(self, branch_name: str, main_branch: str) -> bool:
        """Method 1: Fast check using rev-list."""
        logger.debug("[Method 1] Using fast rev-list check...")
        try:
            repo = self._get_repo()
            result = repo.git.rev_list('--count', f'{main_branch}..{branch_name}')
            if result == '0':
                logger.debug(f"[Method 1] Branch {branch_name} is merged (fast rev-list)")
                self._increment_stat('method1')
                return True
        except git.exc.GitCommandError:
            pass

        return False

    def _check_ancestor(self, branch_name: str, main_branch: str) -> bool:
        """Method 2: Check if branch tip is ancestor of main."""
        logger.debug("[Method 2] Checking if branch tip is ancestor...")
        try:
            repo = self._get_repo()
            branch_tip = repo.refs[branch_name].commit
            is_ancestor = repo.is_ancestor(branch_tip, repo.refs[main_branch].commit)
            if is_ancestor:
                logger.debug(f"[Method 2] Branch {branch_name} is merged (tip is ancestor)")
                self._increment_stat('method2')
                return True
        except Exception as e:
            logger.debug(f"[Method 2] Error checking ancestor: {e}")

        return False

    def _check_merge_commit_message(self, branch_name: str, main_branch: str) -> bool:
        """Method 3: Check merge commit messages."""
        logger.debug("[Method 3] Checking merge commit messages...")
        merge_patterns = [
            f"Merge branch '{branch_name}'",
            f'Merge pull request .* from .*/{branch_name}',
            f'Merge pull request .* from .*:{branch_name}'
        ]

        repo = self._get_repo()
        for commit in repo.iter_commits(main_branch, max_count=100):
            # Ensure message is a string (GitPython can return bytes)
            message = commit.message if isinstance(commit.message, str) else commit.message.decode('utf-8', errors='ignore')
            for pattern in merge_patterns:
                if re.search(pattern, message):
                    logger.debug(f"[Method 3] Found merge commit: {message.splitlines()[0]}")
                    self._increment_stat('method3')
                    return True

        return False

    def _check_full_commit_history(self, branch_name: str, main_branch: str) -> bool:
        """Method 4: Full commit history check (slowest)."""
        logger.debug("[Method 4] Checking full commit history...")
        try:
            repo = self._get_repo()
            repo = self._get_repo()
            branch_commit_set = set(repo.git.rev_list(branch_name).split())
            main_commit_set = set(repo.git.rev_list(main_branch).split())
            if branch_commit_set.issubset(main_commit_set):
                logger.debug(f"[Method 4] Branch {branch_name} is merged (all commits in main)")
                self._increment_stat('method4')
                return True
        except Exception as e:
            logger.debug(f"[Method 4] Error checking commit history: {e}")

        return False

    def delete_branch(self, branch_name: str, dry_run: bool = False) -> bool:
        """Delete a branch locally and remotely if it exists."""
        with self._git_operation():
            try:
                repo = self._get_repo()

                # Check if remote branch exists before deletion
                has_remote = self.has_remote_branch(branch_name)

                # Delete local branch
                if not dry_run:
                    console.print(f"Deleting local branch {branch_name}...")
                    repo.delete_head(branch_name, force=True)

                # Delete remote branch if it exists
                if has_remote:
                    if not dry_run:
                        try:
                            # Only get remote when we need to push
                            remote = repo.remote(self.remote_name)
                            console.print(f"Deleting remote branch {branch_name}...")
                            remote.push(refspec=f":{branch_name}")
                            console.print(f"[green]Deleted branch {branch_name} (local and remote)[/green]")
                        except git.exc.GitCommandError as e:
                            # Check if it's a protected branch error
                            if 'protected' in str(e).lower() or 'prohibited' in str(e).lower():
                                console.print(f"[yellow]Warning: Remote branch {branch_name} is protected and cannot be deleted remotely[/yellow]")
                                console.print(f"[green]Deleted local branch {branch_name} only[/green]")
                            else:
                                # Re-raise if it's a different error
                                raise
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