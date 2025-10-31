"""GitHub API integration service"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Tuple, TYPE_CHECKING, Union
from urllib.parse import urlparse
from github import Github, Auth
from rich.console import Console

from git_branch_keeper.logging_config import get_logger

if TYPE_CHECKING:
    from github.Repository import Repository
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)


class GitHubService:
    def __init__(self, repo_path: str, config: Union["Config", dict]):
        """Initialize the service.

        Note: GitHub token is required and validated before this service is initialized.
        """
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get("verbose", False)
        self.debug_mode = config.get("debug", False)
        self.github_token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
        self.github_api_url: Optional[str] = None
        self.github_repo: Optional[str] = None
        self.github: Optional[Github] = None
        self.gh_repo: Optional["Repository"] = None

    def setup_github_api(self, remote_url: str) -> None:
        """Setup GitHub API access.

        Note: This assumes remote is a GitHub URL and token exists (validated in core.py).
        """
        try:
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

            # Initialize GitHub API (token is guaranteed to exist)
            assert self.github_token is not None, "GitHub token must be set"
            self.github = Github(auth=Auth.Token(self.github_token))
            self.gh_repo = self.github.get_repo(self.github_repo)

            logger.debug(f"[GitHub] GitHub API URL: {self.gh_repo.url}")
            logger.debug(f"[GitHub] GitHub integration enabled for: {path}")

        except Exception as e:
            logger.error(f"[GitHub] Failed to setup GitHub API: {e}")
            raise  # Re-raise since this is now a critical error

    def has_open_pr(self, branch_name: str) -> bool:
        """Check if a branch has any open PRs.

        Note: gh_repo and github_repo are guaranteed to be set after setup_github_api.
        """
        try:
            assert self.gh_repo is not None
            assert self.github_repo is not None

            pulls = self.gh_repo.get_pulls(
                state="open", head=f"{self.github_repo.split('/')[0]}:{branch_name}"
            )
            return pulls.totalCount > 0
        except Exception as e:
            logger.debug(f"[GitHub] Error checking PR status for {branch_name}: {e}")
            return False

    def _fetch_single_branch_pr_data(self, branch_name: str) -> Tuple[str, Dict]:
        """Fetch PR data for a single branch. Returns (branch_name, pr_data_dict)."""
        try:
            # These should never be None when this method is called (guarded by get_bulk_pr_data)
            assert self.github_repo is not None
            assert self.gh_repo is not None

            org_name = self.github_repo.split("/")[0]

            if branch_name in self.config.get("protected_branches", ["main", "master"]):
                # For protected branches, fetch PRs targeting this branch
                branch_prs = list(self.gh_repo.get_pulls(state="all", base=branch_name))
                open_prs = sum(1 for pr in branch_prs if pr.state == "open")
                # Protected branches are merge targets, not branches to be merged
                merged_prs = False
                closed_prs = False
            else:
                # For other branches, fetch PRs from this branch
                branch_prs = list(
                    self.gh_repo.get_pulls(state="all", head=f"{org_name}:{branch_name}")
                )
                open_prs = sum(1 for pr in branch_prs if pr.state == "open")
                # Check if this branch was merged via PR
                merged_prs = any(pr.merged for pr in branch_prs)
                closed_prs = any(pr.state == "closed" and not pr.merged for pr in branch_prs)

            pr_data = {"count": open_prs, "merged": merged_prs, "closed": closed_prs}

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
            return (branch_name, {"count": 0, "merged": False, "closed": False})

    def get_bulk_pr_data(self, branch_names: List[str]) -> Dict[str, Dict]:
        """Get PR data for multiple branches by fetching PRs in parallel.

        Note: gh_repo is guaranteed to be set after setup_github_api.
        """
        if not branch_names:
            return {}

        try:
            assert self.gh_repo is not None
            result = {}

            # Use parallel fetching with ThreadPoolExecutor
            # Benefits from Python 3.14 free-threading when available
            from git_branch_keeper.threading_utils import get_optimal_worker_count

            max_workers = min(10, get_optimal_worker_count())  # Cap at 10 for API rate limiting

            logger.debug(
                f"[GitHub] Fetching PR data for {len(branch_names)} branches using {max_workers} workers"
            )

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

    def close(self) -> None:
        """Close the GitHub API connection to clean up resources."""
        if self.github:
            try:
                self.github.close()
                logger.debug("[GitHub] Closed GitHub API connection")
            except Exception as e:
                logger.debug(f"[GitHub] Error closing GitHub API connection: {e}")
