"""Custom exceptions for git-branch-keeper"""

from __future__ import annotations

import git
import urllib3.exceptions
from github import GithubException

#: Exceptions raised when a git query or command fails, rather than when GBK
#: itself is buggy. Caught wherever GBK deliberately falls back to another
#: strategy or a safe default instead of aborting the run:
#:
#: - ``git.GitError``     - non-zero git exit, missing path, invalid repository
#: - ``git.exc.ODBError`` - object database could not resolve a ref
#: - ``LookupError``      - ``repo.refs[name]`` raises IndexError for unknown refs
#: - ``ValueError``       - ``repo.remote(name)`` raises this for unknown remotes
#:
#: Deliberately excludes AttributeError/TypeError/RuntimeError so genuine bugs
#: in GBK surface instead of being silently swallowed as "git failed".
GIT_ERRORS = (git.GitError, git.exc.ODBError, LookupError, ValueError)

#: Exceptions raised when a GitHub API call fails. GitHub integration is optional
#: and advisory, so these are caught wherever GBK degrades to git-only behaviour:
#:
#: - ``GithubException``            - the API answered with an error status
#: - ``OSError``                    - transport failure (``requests`` errors subclass this)
#: - ``urllib3.exceptions.HTTPError`` - connection-pool and timeout errors
GITHUB_ERRORS = (GithubException, OSError, urllib3.exceptions.HTTPError)


class GitBranchKeeperError(Exception):
    """Base exception for all git-branch-keeper errors."""


class GitOperationError(GitBranchKeeperError):
    """Exception raised for errors in Git operations."""

    def __init__(self, operation: str, branch: str | None = None, message: str | None = None):
        self.operation = operation
        self.branch = branch
        self.message = message

        error_msg = f"Git operation '{operation}' failed"
        if branch:
            error_msg += f" for branch '{branch}'"
        if message:
            error_msg += f": {message}"

        super().__init__(error_msg)


class GitHubAPIError(GitBranchKeeperError):
    """Exception raised for errors in GitHub API operations."""

    def __init__(self, operation: str, message: str | None = None):
        self.operation = operation
        self.message = message

        error_msg = f"GitHub API operation '{operation}' failed"
        if message:
            error_msg += f": {message}"

        super().__init__(error_msg)


class BranchNotFoundError(GitOperationError):
    """Exception raised when a branch is not found."""

    def __init__(self, branch: str):
        super().__init__("find_branch", branch, "Branch not found")


class BranchProtectedError(GitOperationError):
    """Exception raised when attempting to modify a protected branch."""

    def __init__(self, branch: str):
        super().__init__("modify_branch", branch, "Branch is protected")


class DetachedHeadError(GitOperationError):
    """Exception raised when repository is in detached HEAD state."""

    def __init__(self):
        super().__init__("check_state", message="Repository is in detached HEAD state")
