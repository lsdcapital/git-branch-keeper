"""GitHub API integration service"""
import os
from typing import Optional
from urllib.parse import urlparse
import requests
from rich.console import Console

console = Console()

class GitHubService:
    def __init__(self, repo, config: dict):
        """Initialize the service."""
        self.repo = repo
        self.config = config
        self.verbose = config.get('verbose', False)
        self.github_token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
        self.github_api_url = None
        self.github_repo = None
        self.github_enabled = False

    def setup_github_api(self, remote_url: str) -> None:
        """Setup GitHub API access if possible."""
        try:
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
                print("No GitHub token found. Running with reduced GitHub functionality")
                self.github_repo = path
                return

            self.github_repo = path
            self.github_api_url = f"https://api.github.com/repos/{path}"
            self.debug(f"Detected GitHub repository: {path}")
            self.debug(f"GitHub API URL: {self.github_api_url}")
            print(f"GitHub integration enabled for: {path}")

            self.github_enabled = True
        except Exception as e:
            self.github_enabled = False
            self.debug(f"GitHub API setup failed: {e}")

    def has_open_pr(self, branch_name: str) -> bool:
        """Check if a branch has any open PRs."""
        if not self.github_enabled:
            return False

        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            url = f"{self.github_api_url}/pulls"
            params = {
                "head": f"{self.github_repo.split('/')[0]}:{branch_name}",
                "state": "all"  # Check both open and closed PRs
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            prs = response.json()
            
            # Check if any PR was merged
            for pr in prs:
                if pr.get('merged_at'):
                    if self.verbose:
                        self.debug(f"Branch {branch_name} has a merged PR: #{pr['number']}")
                    return True
                    
            return False
        except Exception as e:
            self.debug(f"Error checking PR status for {branch_name}: {e}")
            return False

    def get_github_branch_url(self, branch_name: str, url_type: str = "pulls") -> str:
        """Get the GitHub URL for a branch."""
        if not self.github_repo:
            self.debug("No GitHub repo configured")
            return ""

        base_url = f"https://github.com/{self.github_repo}"
        
        # For main/protected branches, show all PRs
        if branch_name in self.config.get('protected_branches', ['main', 'master']):
            return f"{base_url}/pulls"
        
        # For other branches, show PRs filtered by branch
        if url_type == "pulls":
            return f"{base_url}/pulls?q=is%3Apr+head%3A{branch_name}"
        else:
            return f"{base_url}/tree/{branch_name}"

    def get_pr_count(self, branch_name: str) -> int:
        """Get the number of open PRs for or targeting a branch."""
        if not self.github_enabled:
            return 0

        if not (self.github_api_url and self.github_token):
            return 0

        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            # Check PRs where this branch is the source (head)
            url = f"{self.github_api_url}/pulls"
            params = {
                "head": f"{self.github_repo.split('/')[0]}:{branch_name}",
                "state": "open"
            }
            head_response = requests.get(url, headers=headers, params=params)
            head_response.raise_for_status()
            
            # For main/protected branches, also check PRs targeting this branch
            if branch_name in self.config.get('protected_branches', ['main', 'master']):
                params = {
                    "base": branch_name,
                    "state": "open"
                }
                base_response = requests.get(url, headers=headers, params=params)
                base_response.raise_for_status()
                return len(head_response.json()) + len(base_response.json())
            
            return len(head_response.json())
        except Exception as e:
            self.debug(f"Error checking PR count for {branch_name}: {e}")
            return 0

    def get_pr_status(self, branch_name: str) -> tuple[int, bool]:
        """Get PR count and merged status for a branch."""
        if not self.github_enabled:
            return 0, False

        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            url = f"{self.github_api_url}/pulls"
            params = {
                "head": f"{self.github_repo.split('/')[0]}:{branch_name}",
                "state": "all"
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            prs = response.json()
            
            open_count = sum(1 for pr in prs if pr['state'] == 'open')
            was_merged = any(pr.get('merged_at') for pr in prs)
            
            return open_count, was_merged
        except Exception as e:
            self.debug(f"Error getting PR status for {branch_name}: {e}")
            return 0, False

    def was_merged_via_pr(self, branch_name: str) -> bool:
        """Check if a branch was ever merged via PR."""
        if not self.github_enabled:
            return False

        try:
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            url = f"{self.github_api_url}/pulls"
            params = {
                "head": f"{self.github_repo.split('/')[0]}:{branch_name}",
                "state": "closed"  # Only check closed PRs
            }
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            prs = response.json()
            
            # Check if any PR was merged
            was_merged = any(pr.get('merged_at') for pr in prs)
            if was_merged and self.verbose:
                self.debug(f"Branch {branch_name} was merged via PR")
            return was_merged
        except Exception as e:
            self.debug(f"Error checking PR merge status for {branch_name}: {e}")
            return False

    def is_configured(self) -> bool:
        """Check if GitHub is properly configured with a token."""
        return bool(self.github_token and self.github_api_url and self.github_repo)

    def debug(self, message: str) -> None:
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            console.print(f"[GitHub] {message}") 