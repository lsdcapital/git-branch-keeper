"""GitHub API integration service"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Tuple, TYPE_CHECKING, Union
from urllib.parse import urlparse
from github import Github
from rich.console import Console

from git_branch_keeper.logging_config import get_logger

if TYPE_CHECKING:
    from github.Repository import Repository
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)

class GitHubService:
    def __init__(self, repo_path: str, config: Union['Config', dict]):
        """Initialize the service."""
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get('verbose', False)
        self.debug_mode = config.get('debug', False)
        self.github_token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
        self.github_api_url: Optional[str] = None
        self.github_repo: Optional[str] = None
        self.github_enabled = False
        self.github: Optional[Github] = None
        self.gh_repo: Optional['Repository'] = None

    def setup_github_api(self, remote_url: str) -> None:
        """Setup GitHub API access."""
        try:
            if "github.com" not in remote_url:
                if self.debug_mode:
                    logger.debug("[GitHub] Not a GitHub repository")
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

            self.github_repo = path

            # Check if we have a token (already set in __init__ from config or env)
            if not self.github_token:
                if self.debug_mode:
                    logger.debug("[GitHub] No GitHub token found. Running with reduced functionality")
                return

            # Initialize GitHub API
            self.github = Github(self.github_token)
            self.gh_repo = self.github.get_repo(self.github_repo)
            self.github_enabled = True

            logger.debug(f"[GitHub] GitHub API URL: {self.gh_repo.url}")
            logger.debug(f"[GitHub] GitHub integration enabled for: {path}")

        except Exception as e:
            logger.debug(f"[GitHub] Failed to setup GitHub API: {e}")
            self.github_enabled = False

    def get_branch_pr_status(self, branch_name: str) -> dict:
        """Get detailed PR status for a branch."""
        if not self.github_enabled or self.gh_repo is None or self.github_repo is None:
            return {'has_pr': False, 'state': 'unknown'}

        try:
            pulls = list(self.gh_repo.get_pulls(
                state='all',
                head=f"{self.github_repo.split('/')[0]}:{branch_name}"
            ))
            if not pulls:
                return {'has_pr': False, 'state': 'no_pr'}
            
            closed_or_merged_prs = [pr for pr in pulls if pr.state == 'closed' or pr.merged]
            
            if not closed_or_merged_prs:
                latest_pr = max(pulls, key=lambda pr: pr.created_at)
                return {
                    'has_pr': True,
                    'state': 'merged' if latest_pr.merged else 'closed' if latest_pr.state == 'closed' else 'open',
                    'pr_number': latest_pr.number,
                    'closed_at': latest_pr.closed_at.isoformat() if latest_pr.closed_at else None,
                    'merged_at': latest_pr.merged_at.isoformat() if latest_pr.merged_at else None
                }
            
            latest_pr = max(closed_or_merged_prs, key=lambda pr: pr.created_at)
            return {
                'has_pr': True,
                'state': 'merged' if latest_pr.merged else 'closed',
                'pr_number': latest_pr.number,
                'closed_at': latest_pr.closed_at.isoformat() if latest_pr.closed_at else None,
                'merged_at': latest_pr.merged_at.isoformat() if latest_pr.merged_at else None
            }
        except Exception as e:
            logger.debug(f"[GitHub] Error getting PR status for {branch_name}: {e}")
            return {'has_pr': False, 'state': 'error'}

    def has_open_pr(self, branch_name: str) -> bool:
        """Check if a branch has any open PRs."""
        if not self.github_enabled or self.gh_repo is None or self.github_repo is None:
            return False

        try:
            pulls = self.gh_repo.get_pulls(state='open', head=f"{self.github_repo.split('/')[0]}:{branch_name}")
            return pulls.totalCount > 0
        except Exception as e:
            logger.debug(f"[GitHub] Error checking PR status for {branch_name}: {e}")
            return False

    def get_pr_count(self, branch_name: str) -> int:
        """Get the number of open PRs for or targeting a branch."""
        if not self.github_enabled or self.gh_repo is None or self.github_repo is None:
            return 0

        try:
            pulls = self.gh_repo.get_pulls(state='open', head=f"{self.github_repo.split('/')[0]}:{branch_name}")
            count = pulls.totalCount

            # For main/protected branches, also check PRs targeting this branch
            if branch_name in self.config.get('protected_branches', ['main', 'master']):
                base_pulls = self.gh_repo.get_pulls(state='open', base=branch_name)
                count += base_pulls.totalCount

            return count
        except Exception as e:
            logger.debug(f"[GitHub] Error checking PR count for {branch_name}: {e}")
            return 0

    def get_pr_status(self, branch_name: str) -> Tuple[int, bool]:
        """Get PR count and merged status for a branch."""
        if not self.github_enabled or self.gh_repo is None or self.github_repo is None:
            return 0, False

        try:
            pulls = list(self.gh_repo.get_pulls(state='all', head=f"{self.github_repo.split('/')[0]}:{branch_name}"))
            open_count = sum(1 for pr in pulls if pr.state == 'open')
            was_merged = any(pr.merged for pr in pulls)

            if was_merged and self.debug_mode:
                merged_pr = next(pr for pr in pulls if pr.merged)
                logger.debug(f"[GitHub] Found merged PR #{merged_pr.number} for {branch_name}")

            return open_count, was_merged
        except Exception as e:
            logger.debug(f"[GitHub] Error getting PR status for {branch_name}: {e}")
            return 0, False

    def was_merged_via_pr(self, branch_name: str) -> bool:
        """Check if a branch was ever merged via PR."""
        if not self.github_enabled:
            return False

        try:
            pr_status = self.get_branch_pr_status(branch_name)
            return pr_status['has_pr'] and pr_status['state'] == 'merged'
        except Exception as e:
            logger.debug(f"[GitHub] Error checking PR status for {branch_name}: {e}")
            return False

    def _fetch_single_branch_pr_data(self, branch_name: str) -> Tuple[str, Dict]:
        """Fetch PR data for a single branch. Returns (branch_name, pr_data_dict)."""
        try:
            # These should never be None when this method is called (guarded by get_bulk_pr_data)
            assert self.github_repo is not None
            assert self.gh_repo is not None

            org_name = self.github_repo.split('/')[0]

            if branch_name in self.config.get('protected_branches', ['main', 'master']):
                # For protected branches, fetch PRs targeting this branch
                branch_prs = list(self.gh_repo.get_pulls(state='all', base=branch_name))
                open_prs = sum(1 for pr in branch_prs if pr.state == 'open')
            else:
                # For other branches, fetch PRs from this branch
                branch_prs = list(self.gh_repo.get_pulls(
                    state='all',
                    head=f"{org_name}:{branch_name}"
                ))
                open_prs = sum(1 for pr in branch_prs if pr.state == 'open')

            merged_prs = any(pr.merged for pr in branch_prs)
            closed_prs = any(pr.state == 'closed' and not pr.merged for pr in branch_prs)

            pr_data = {
                'count': open_prs,
                'merged': merged_prs,
                'closed': closed_prs
            }

            if self.debug_mode:
                if merged_prs:
                    logger.debug(f"[GitHub] Branch {branch_name} has merged PR")
                elif closed_prs:
                    logger.debug(f"[GitHub] Branch {branch_name} has closed (unmerged) PR")
                elif open_prs:
                    logger.debug(f"[GitHub] Branch {branch_name} has {open_prs} open PR(s)")

            return (branch_name, pr_data)

        except Exception as e:
            logger.debug(f"[GitHub] Error fetching PRs for branch {branch_name}: {e}")
            # Return default values if branch PR fetch fails
            return (branch_name, {
                'count': 0,
                'merged': False,
                'closed': False
            })

    def get_bulk_pr_data(self, branch_names: List[str]) -> Dict[str, Dict]:
        """Get PR data for multiple branches by fetching PRs in parallel."""
        if not self.github_enabled or self.gh_repo is None:
            return {}

        if not branch_names:
            return {}

        try:
            result = {}

            # Use parallel fetching with ThreadPoolExecutor
            # Benefits from Python 3.14 free-threading when available
            from git_branch_keeper.threading_utils import get_optimal_worker_count
            max_workers = min(10, get_optimal_worker_count())  # Cap at 10 for API rate limiting

            logger.debug(f"[GitHub] Fetching PR data for {len(branch_names)} branches using {max_workers} workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_branch = {
                    executor.submit(self._fetch_single_branch_pr_data, branch): branch
                    for branch in branch_names
                }

                for future in as_completed(future_to_branch):
                    branch_name, pr_data = future.result()
                    result[branch_name] = pr_data

            logger.debug(f"[GitHub] Fetched PR data for {len(result)} branches")

            return result

        except Exception as e:
            logger.debug(f"[GitHub] Error getting bulk PR data: {e}")
            return {}