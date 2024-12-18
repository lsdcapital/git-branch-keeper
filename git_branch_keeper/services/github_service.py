"""GitHub API integration service"""
import os
from typing import Optional
from urllib.parse import urlparse
import requests

class GitHubService:
    def __init__(self, config: dict = None, verbose: bool = False):
        self.config = config or {}
        self.verbose = verbose
        self.github_token = self.config.get("github_token") or os.environ.get("GITHUB_TOKEN")
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
        """Check if a branch has an open PR."""
        if not self.github_enabled:
            return False

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

    def get_github_branch_url(self, branch_name: str, url_type: str = "pulls") -> str:
        """Get the GitHub URL for a branch."""
        if not self.github_repo:
            self.debug("No GitHub repo configured")
            return ""

        base_url = f"https://github.com/{self.github_repo}"
        
        if url_type == "pulls":
            return f"{base_url}/pulls?q=is%3Apr+head%3A{branch_name}"
        else:
            return f"{base_url}/tree/{branch_name}"

    def get_pr_count(self, branch_name: str) -> int:
        """Get the number of open PRs for a branch."""
        if not self.github_enabled:
            return 0

        if not (self.github_api_url and self.github_token):
            return 0

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
            return len(response.json())
        except Exception as e:
            self.debug(f"Error checking PR count for {branch_name}: {e}")
            return 0

    def debug(self, message: str) -> None:
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            print(f"[GitHub] {message}") 