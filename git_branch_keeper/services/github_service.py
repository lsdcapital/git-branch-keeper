"""GitHub API integration service"""
import os
from typing import Optional, List, Dict
from urllib.parse import urlparse
from github import Github
from rich.console import Console

console = Console()

class GitHubService:
    def __init__(self, repo, config: dict):
        """Initialize the service."""
        self.repo = repo
        self.config = config
        self.verbose = config.get('verbose', False)
        self.debug_mode = config.get('debug', False)
        self.github_token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
        self.github_api_url = None
        self.github_repo = None
        self.github_enabled = False
        self.github = None
        self.gh_repo = None

    def setup_github_api(self, remote_url: str) -> None:
        """Setup GitHub API access."""
        try:
            if "github.com" not in remote_url:
                if self.debug_mode:
                    print("[GitHub] Not a GitHub repository")
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

            # Get GitHub token from environment
            self.github_token = os.getenv('GITHUB_TOKEN')
            if not self.github_token:
                if self.debug_mode:
                    print("[GitHub] No GitHub token found. Running with reduced GitHub functionality")
                return

            # Initialize GitHub API
            self.github = Github(self.github_token)
            self.gh_repo = self.github.get_repo(self.github_repo)
            self.github_enabled = True

            if self.debug_mode:
                print(f"[GitHub] GitHub API URL: {self.gh_repo.url}")
                print(f"[GitHub] GitHub integration enabled for: {path}")

        except Exception as e:
            if self.debug_mode:
                print(f"[GitHub] Failed to setup GitHub API: {e}")
            self.github_enabled = False

    def get_branch_pr_status(self, branch_name: str) -> dict:
        """Get detailed PR status for a branch."""
        if not self.github_enabled:
            return {'has_pr': False, 'state': 'unknown'}

        try:
            pulls = list(self.gh_repo.get_pulls(
                state='all',
                head=f"{self.github_repo.split('/')[0]}:{branch_name}"
            ))
            
            if not pulls:
                return {'has_pr': False, 'state': 'no_pr'}
                
            latest_pr = max(pulls, key=lambda pr: pr.created_at)
            
            return {
                'has_pr': True,
                'state': 'merged' if latest_pr.merged else 'closed' if latest_pr.state == 'closed' else 'open',
                'pr_number': latest_pr.number,
                'closed_at': latest_pr.closed_at.isoformat() if latest_pr.closed_at else None,
                'merged_at': latest_pr.merged_at.isoformat() if latest_pr.merged_at else None
            }
        except Exception as e:
            if self.debug_mode:
                print(f"[GitHub] Error getting PR status for {branch_name}: {e}")
            return {'has_pr': False, 'state': 'error'}

    def has_open_pr(self, branch_name: str) -> bool:
        """Check if a branch has any open PRs."""
        if not self.github_enabled:
            return False

        try:
            pulls = self.gh_repo.get_pulls(state='open', head=f"{self.github_repo.split('/')[0]}:{branch_name}")
            return pulls.totalCount > 0
        except Exception as e:
            if self.debug_mode:
                print(f"[GitHub] Error checking PR status for {branch_name}: {e}")
            return False

    def get_pr_count(self, branch_name: str) -> int:
        """Get the number of open PRs for or targeting a branch."""
        if not self.github_enabled:
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
            if self.debug_mode:
                print(f"[GitHub] Error checking PR count for {branch_name}: {e}")
            return 0

    def get_pr_status(self, branch_name: str) -> tuple[int, bool]:
        """Get PR count and merged status for a branch."""
        if not self.github_enabled:
            return 0, False

        try:
            pulls = list(self.gh_repo.get_pulls(state='all', head=f"{self.github_repo.split('/')[0]}:{branch_name}"))
            open_count = sum(1 for pr in pulls if pr.state == 'open')
            was_merged = any(pr.merged for pr in pulls)

            if was_merged and self.debug_mode:
                merged_pr = next(pr for pr in pulls if pr.merged)
                print(f"[GitHub] Found merged PR #{merged_pr.number} for {branch_name}")

            return open_count, was_merged
        except Exception as e:
            if self.debug_mode:
                print(f"[GitHub] Error getting PR status for {branch_name}: {e}")
            return 0, False

    def was_merged_via_pr(self, branch_name: str) -> bool:
        """Check if a branch was ever merged via PR."""
        if not self.github_enabled:
            return False

        try:
            pr_status = self.get_branch_pr_status(branch_name)
            return pr_status['has_pr'] and pr_status['state'] == 'merged'
        except Exception as e:
            if self.debug_mode:
                print(f"[GitHub] Error checking PR status for {branch_name}: {e}")
            return False

    def get_bulk_pr_data(self, branch_names: List[str]) -> Dict[str, Dict]:
        """Get PR data for multiple branches in a single request."""
        if not self.github_enabled:
            return {}

        try:
            result = {}
            # Get both open and closed PRs
            pulls = list(self.gh_repo.get_pulls(state='all'))
            
            for branch_name in branch_names:
                branch_prs = [pr for pr in pulls if pr.head.ref == branch_name]
                open_prs = sum(1 for pr in branch_prs if pr.state == 'open')
                merged_prs = any(pr.merged for pr in branch_prs)
                closed_prs = any(pr.state == 'closed' and not pr.merged for pr in branch_prs)
                
                result[branch_name] = {
                    'count': open_prs,
                    'merged': merged_prs,
                    'closed': closed_prs
                }
                
                if self.debug_mode:
                    if merged_prs:
                        print(f"[GitHub] Branch {branch_name} has merged PR")
                    elif closed_prs:
                        print(f"[GitHub] Branch {branch_name} has closed (unmerged) PR")
                    elif open_prs:
                        print(f"[GitHub] Branch {branch_name} has {open_prs} open PR(s)")
            
            if self.debug_mode:
                print(f"[GitHub] Pre-fetched PR data for {len(result)} branches")
            
            return result
            
        except Exception as e:
            if self.debug_mode:
                print(f"[GitHub] Error getting bulk PR data: {e}")
            return {}