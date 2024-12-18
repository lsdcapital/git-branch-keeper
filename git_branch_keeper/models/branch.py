"""Models for branch-related data structures"""
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

class BranchStatus(Enum):
    ACTIVE = "active"
    STALE = "stale"
    MERGED = "merged"

class SyncStatus(Enum):
    SYNCED = "synced"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"

@dataclass
class BranchDetails:
    name: str
    last_commit_date: str
    age_days: int
    status: BranchStatus
    has_local_changes: bool
    has_remote: bool
    sync_status: str
    pr_status: Optional[str] = None 