"""Configuration handling for git-branch-keeper"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List


def load_config(config_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Load configuration from JSON file.

    Args:
        config_file: Optional path to config file. If not provided,
                    looks in current directory first, then user's home directory.

    Returns:
        Dict containing configuration values
    """
    if config_file:
        config_path = Path(config_file)
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                print(f"Loaded config from {config_file}")
                return config
            except json.JSONDecodeError as e:
                print(f"Error loading config file: {e}")
                return {}
            except Exception as e:
                print(f"Error reading config file: {e}")
                return {}

    # Look for config in current directory
    current_config = Path("git-branch-keeper.json")
    if current_config.exists():
        try:
            config = json.loads(current_config.read_text())
            print("Loaded config from git-branch-keeper.json")
            return config
        except Exception:
            pass

    # Look for config in home directory
    home_config = Path.home() / ".git-branch-keeper.json"
    if home_config.exists():
        try:
            config = json.loads(home_config.read_text())
            print(f"Loaded config from {home_config}")
            return config
        except Exception:
            pass

    return {}


@dataclass
class Config:
    """Configuration for git-branch-keeper with validation."""

    # Branch filtering
    protected_branches: List[str] = field(default_factory=lambda: ['main', 'master'])
    ignore_patterns: List[str] = field(default_factory=list)
    main_branch: str = 'main'
    status_filter: str = 'all'  # all, merged, stale

    # Stale branch threshold
    stale_days: int = 30

    # Execution modes
    interactive: bool = True
    dry_run: bool = True
    force: bool = False
    verbose: bool = False
    debug: bool = False
    refresh: bool = False  # Force refresh, bypass cache
    sequential: bool = False  # Force sequential processing (disable parallelism)
    workers: Optional[int] = None  # Number of parallel workers (None = auto-detect)

    # GitHub integration
    github_token: Optional[str] = None
    max_prs_to_fetch: int = 500

    # Sorting options
    sort_by: str = 'age'  # name, age, date, status
    sort_order: str = 'asc'  # asc, desc

    def __post_init__(self):
        """Validate configuration after initialization."""
        self._validate_stale_days()
        self._validate_status_filter()
        self._validate_main_branch()
        self._validate_protected_branches()
        self._validate_max_prs()
        self._validate_sort_by()
        self._validate_sort_order()

    def _validate_stale_days(self):
        """Validate stale_days is positive."""
        if self.stale_days <= 0:
            raise ValueError(f"stale_days must be positive, got {self.stale_days}")

    def _validate_status_filter(self):
        """Validate status_filter is one of allowed values."""
        allowed = ['all', 'merged', 'stale']
        if self.status_filter not in allowed:
            raise ValueError(
                f"status_filter must be one of {allowed}, got '{self.status_filter}'"
            )

    def _validate_main_branch(self):
        """Validate main_branch is not empty."""
        if not self.main_branch or not self.main_branch.strip():
            raise ValueError("main_branch cannot be empty")
        self.main_branch = self.main_branch.strip()

    def _validate_protected_branches(self):
        """Validate protected_branches list."""
        if not isinstance(self.protected_branches, list):
            raise ValueError("protected_branches must be a list")

        # Ensure main_branch is in protected_branches
        if self.main_branch not in self.protected_branches:
            self.protected_branches.append(self.main_branch)

    def _validate_max_prs(self):
        """Validate max_prs_to_fetch is positive."""
        if self.max_prs_to_fetch <= 0:
            raise ValueError(
                f"max_prs_to_fetch must be positive, got {self.max_prs_to_fetch}"
            )

    def _validate_sort_by(self):
        """Validate sort_by is one of allowed values."""
        allowed = ['name', 'age', 'date', 'status']
        if self.sort_by not in allowed:
            raise ValueError(
                f"sort_by must be one of {allowed}, got '{self.sort_by}'"
            )

    def _validate_sort_order(self):
        """Validate sort_order is one of allowed values."""
        allowed = ['asc', 'desc']
        if self.sort_order not in allowed:
            raise ValueError(
                f"sort_order must be one of {allowed}, got '{self.sort_order}'"
            )

    def to_dict(self) -> dict:
        """Convert config to dictionary for backward compatibility."""
        return {
            'protected_branches': self.protected_branches,
            'ignore_patterns': self.ignore_patterns,
            'main_branch': self.main_branch,
            'status_filter': self.status_filter,
            'stale_days': self.stale_days,
            'interactive': self.interactive,
            'dry_run': self.dry_run,
            'force': self.force,
            'verbose': self.verbose,
            'debug': self.debug,
            'refresh': self.refresh,
            'sequential': self.sequential,
            'workers': self.workers,
            'github_token': self.github_token,
            'max_prs_to_fetch': self.max_prs_to_fetch,
            'sort_by': self.sort_by,
            'sort_order': self.sort_order,
        }

    def get(self, key: str, default=None):
        """Get config value by key for backward compatibility."""
        return getattr(self, key, default)

    @classmethod
    def from_dict(cls, config_dict: dict) -> 'Config':
        """Create Config from dictionary."""
        # Extract only known fields
        known_fields = {
            'protected_branches', 'ignore_patterns', 'main_branch', 'status_filter',
            'stale_days', 'interactive', 'dry_run', 'force', 'verbose', 'debug',
            'refresh', 'sequential', 'workers', 'github_token', 'max_prs_to_fetch',
            'sort_by', 'sort_order'
        }

        filtered = {k: v for k, v in config_dict.items() if k in known_fields}
        return cls(**filtered)
