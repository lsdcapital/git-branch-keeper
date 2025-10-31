"""Version information for git-branch-keeper."""

try:
    from git_branch_keeper._version import __version__
except ImportError:
    # Fallback for development without tags or when running from source
    __version__ = "0.0.0+unknown"
