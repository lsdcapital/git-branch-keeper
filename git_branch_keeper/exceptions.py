"""Custom exceptions for git-branch-keeper"""

from typing import Optional


class GitBranchKeeperError(Exception):
    """Base exception for all git-branch-keeper errors."""
    pass


class GitOperationError(GitBranchKeeperError):
    """Exception raised for errors in Git operations."""

    def __init__(self, operation: str, branch: Optional[str] = None, message: Optional[str] = None):
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

    def __init__(self, operation: str, message: Optional[str] = None):
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
