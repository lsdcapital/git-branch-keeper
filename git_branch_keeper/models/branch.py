"""Models for branch-related data structures"""
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

class BranchStatus(Enum):
    ACTIVE = "active"
    MERGED = "merged"
    STALE = "stale"
    IGNORED = "ignored"
    UNKNOWN = "unknown"

class SyncStatus(Enum):
    SYNCED = "synced"
    LOCAL_ONLY = "local-only"
    REMOTE_ONLY = "remote-only"
    MERGED_REMOTE_DELETED = "merged-remote-deleted"
    UNKNOWN = "unknown"

@dataclass
class BranchDetails:
    name: str
    last_commit_date: datetime
    age_days: int
    status: BranchStatus
    has_remote: bool
    sync_status: SyncStatus
    pr_count: int 