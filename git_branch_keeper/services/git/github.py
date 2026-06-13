"""GitHub API integration service"""

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from datetime import datetime
from typing import Optional, List, Dict, Tuple, TYPE_CHECKING, Union
from urllib.parse import urlparse
from github import Github, Auth
from rich.console import Console

from git_branch_keeper.utils.logging import get_logger

if TYPE_CHECKING:
    from github.Repository import Repository
    from git_branch_keeper.config import Config

console = Console()
logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_gh_cli_token() -> Optional[str]:
    """Return a token from the GitHub CLI if it is installed and authenticated.

    This is intentionally non-interactive: prompts are disabled so GBK never hangs
    waiting for `gh auth login`. If `gh` is unavailable or unauthenticated, return
    None and let the app continue in local-only mode.
    """
    if os.environ.get("GBK_DISABLE_GH_AUTH_FALLBACK"):
        return None

    gh_path = shutil.which("gh")
    if not gh_path:
        return None

    try:
        env = {**os.environ, "GH_PROMPT_DISABLED": "1"}
        result = subprocess.run(
            [gh_path, "auth", "token", "--hostname", "github.com"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
            env=env,
        )
    except Exception as e:
        logger.debug(f"[GitHub] Could not read token from gh CLI: {e}")
        return None

    if result.returncode != 0:
        logger.debug(f"[GitHub] gh auth token unavailable: {result.stderr.strip()}")
        return None

    token = result.stdout.strip()
    if token:
        logger.debug("[GitHub] Using token from gh CLI authentication")
        return token
    return None


def resolve_github_token(config: Union["Config", dict]) -> Optional[str]:
    """Resolve a GitHub token from config, environment, or gh CLI auth."""
    return (
        config.get("github_token")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or get_gh_cli_token()
    )


class GitHubService:
    def __init__(self, repo_path: str, config: Union["Config", dict]):
        """Initialize the service.

        Note: GitHub integration is optional. Call setup_github_api() to enable.
        """
        self.repo_path = repo_path
        self.config = config
        self.verbose = config.get("verbose", False)
        self.debug_mode = config.get("debug", False)
        self.github_token = resolve_github_token(config)
        self.github_api_url: Optional[str] = None
        self.github_repo: Optional[str] = None
        self.github: Optional[Github] = None
        self.gh_repo: Optional["Repository"] = None

    def is_enabled(self) -> bool:
        """Check if GitHub integration is enabled and configured."""
        return self.gh_repo is not None

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

        Returns False if GitHub integration is not enabled.
        """
        if not self.is_enabled():
            logger.debug(f"[GitHub] Skipping PR check for {branch_name} - integration disabled")
            return False

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

    def _empty_pr_data(self) -> Dict:
        """Return the default PR data shape used throughout the app."""
        return {
            "count": 0,
            "merged": False,
            "closed": False,
            "number": None,
            "url": None,
            "head_ref": None,
            "head_sha": None,
            "base_ref": None,
            "merge_commit_sha": None,
            "merged_at": None,
            "head_matches_local": None,
            "local_head_sha": None,
        }

    def _safe_str(self, value) -> Optional[str]:
        """Return a string only for real scalar values; ignore MagicMock placeholders."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return None

    def _safe_int(self, value) -> Optional[int]:
        """Return an int only for real integer values; ignore MagicMock placeholders."""
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    def _safe_isoformat(self, value) -> Optional[str]:
        """Return an ISO timestamp string for datetime-like values."""
        if not isinstance(value, datetime):
            return None
        try:
            return value.isoformat()
        except Exception:
            return None

    def _pr_summary(self, pr) -> Dict:
        """Extract stable scalar metadata from a PyGithub PR object."""
        head = getattr(pr, "head", None)
        base = getattr(pr, "base", None)
        return {
            "number": self._safe_int(getattr(pr, "number", None)),
            "url": self._safe_str(getattr(pr, "html_url", None)),
            "head_ref": self._safe_str(getattr(head, "ref", None)),
            "head_sha": self._safe_str(getattr(head, "sha", None)),
            "base_ref": self._safe_str(getattr(base, "ref", None)),
            "merge_commit_sha": self._safe_str(getattr(pr, "merge_commit_sha", None)),
            "merged_at": self._safe_isoformat(getattr(pr, "merged_at", None)),
        }

    def _latest_pr(self, prs: List) -> Optional[object]:
        """Return the latest PR by updated_at when available, otherwise the first PR."""
        if not prs:
            return None

        def timestamp(pr):
            value = getattr(pr, "updated_at", None) or getattr(pr, "created_at", None)
            if not isinstance(value, datetime):
                return 0
            try:
                return value.timestamp()
            except Exception:
                return 0

        return max(prs, key=timestamp)

    def _fetch_single_branch_pr_data(self, branch_name: str) -> Tuple[str, Dict]:
        """Fetch PR data for a single branch. Returns (branch_name, pr_data_dict)."""
        try:
            # These should never be None when this method is called (guarded by public fetch methods)
            assert self.github_repo is not None
            assert self.gh_repo is not None

            org_name = self.github_repo.split("/")[0]

            if branch_name in self.config.get("protected_branches", ["main", "master"]):
                # For protected branches, fetch PRs targeting this branch. Protected
                # branches are merge targets, not source branches to be cleaned up.
                branch_prs = list(self.gh_repo.get_pulls(state="all", base=branch_name))
                open_pr_list = [pr for pr in branch_prs if pr.state == "open"]
                merged_pr_list = []
                closed_pr_list = []
            else:
                # For other branches, fetch PRs from this branch
                branch_prs = list(
                    self.gh_repo.get_pulls(state="all", head=f"{org_name}:{branch_name}")
                )
                open_pr_list = [pr for pr in branch_prs if pr.state == "open"]
                merged_pr_list = [pr for pr in branch_prs if bool(pr.merged)]
                closed_pr_list = [
                    pr for pr in branch_prs if pr.state == "closed" and not bool(pr.merged)
                ]

            open_prs = len(open_pr_list)
            merged_prs = bool(merged_pr_list)
            closed_prs = bool(closed_pr_list)

            selected_pr = (
                self._latest_pr(open_pr_list)
                or self._latest_pr(merged_pr_list)
                or self._latest_pr(closed_pr_list)
            )
            pr_data = self._empty_pr_data()
            pr_data.update(
                {
                    "count": open_prs,
                    "merged": merged_prs,
                    "closed": closed_prs,
                }
            )
            if selected_pr is not None:
                pr_data.update(self._pr_summary(selected_pr))

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
            return (branch_name, self._empty_pr_data())

    def get_pr_data_for_branch(self, branch_name: str) -> Dict[str, Dict]:
        """Get PR data for one branch.

        Returns an empty dict if GitHub integration is not enabled. This is used
        inside the per-branch processing loop so the progress bar covers both PR
        lookup and local Git analysis for each branch.
        """
        if not self.is_enabled():
            logger.debug(f"[GitHub] Skipping PR fetch for {branch_name} - integration disabled")
            return {}

        fetched_branch, pr_data = self._fetch_single_branch_pr_data(branch_name)
        return {fetched_branch: pr_data}

    def get_bulk_pr_data(self, branch_names: List[str]) -> Dict[str, Dict]:
        """Get PR data for multiple branches by fetching PRs in parallel.

        Returns empty dict if GitHub integration is not enabled.
        Kept for direct callers/tests; normal branch analysis fetches PR data per
        branch inside the main processing loop.
        """
        if not self.is_enabled():
            logger.debug("[GitHub] Skipping bulk PR fetch - integration disabled")
            return {}

        if not branch_names:
            return {}

        try:
            assert self.gh_repo is not None
            result = {}

            # Use parallel fetching with ThreadPoolExecutor
            # Benefits from Python 3.14 free-threading when available
            from git_branch_keeper.utils.threading import get_optimal_worker_count

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
