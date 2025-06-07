"""Version information for git-branch-keeper."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("git-branch-keeper")
except importlib.metadata.PackageNotFoundError:
    # Package not installed, use development version
    __version__ = "0.1.0-dev"