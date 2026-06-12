"""Deletion journal - records every deleted branch so deletions are recoverable.

Every branch deletion appends a JSON line to ~/.git-branch-keeper/deletions.jsonl
containing the branch's tip SHA. As long as the commit object still exists
(git keeps unreachable objects for ~90 days by default), the branch can be
restored with `git-branch-keeper undo`.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from git_branch_keeper.utils.logging import get_logger

# Import fcntl for POSIX file locking (Unix/Linux/macOS)
try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

logger = get_logger(__name__)


class DeletionJournal:
    """Append-only journal of branch deletions, shared across repositories."""

    def __init__(self, repo_path: str, journal_file: Optional[Path] = None):
        """Initialize the journal for a repository.

        Args:
            repo_path: Path to the git repository (used to scope entries)
            journal_file: Override journal location (mainly for tests)
        """
        self.repo_path = str(Path(repo_path).resolve())
        self.journal_file = journal_file or (Path.home() / ".git-branch-keeper" / "deletions.jsonl")

    def record_deletion(
        self,
        branch_name: str,
        sha: str,
        had_remote: bool,
        remote_deleted: bool,
        remote_name: str = "origin",
    ) -> None:
        """Record a branch deletion. Never raises - journaling must not block deletion."""
        self._append(
            {
                "action": "deleted",
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "repo": self.repo_path,
                "branch": branch_name,
                "sha": sha,
                "had_remote": had_remote,
                "remote_deleted": remote_deleted,
                "remote": remote_name,
            }
        )

    def record_restore(self, branch_name: str, sha: str) -> None:
        """Record that a branch was restored from the journal."""
        self._append(
            {
                "action": "restored",
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "repo": self.repo_path,
                "branch": branch_name,
                "sha": sha,
            }
        )

    def deletions(self) -> List[Dict]:
        """Return deletion entries for this repository, oldest first."""
        return [e for e in self._read_entries() if e.get("action") == "deleted"]

    def _append(self, entry: Dict) -> None:
        try:
            self.journal_file.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, sort_keys=True)
            with open(self.journal_file, "a", encoding="utf-8") as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(line + "\n")
                    f.flush()
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            logger.debug(f"Journaled {entry['action']} of {entry['branch']} ({entry.get('sha')})")
        except Exception as e:
            # Journaling is best-effort; never let it break a deletion or restore
            logger.warning(f"Could not write deletion journal: {e}")

    def _read_entries(self) -> List[Dict]:
        """Read all entries for this repository, skipping corrupt lines."""
        if not self.journal_file.exists():
            return []
        entries = []
        try:
            with open(self.journal_file, "r", encoding="utf-8") as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug("Skipping corrupt journal line")
                            continue
                        if entry.get("repo") == self.repo_path and entry.get("branch"):
                            entries.append(entry)
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning(f"Could not read deletion journal: {e}")
        return entries
