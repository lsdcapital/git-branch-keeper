"""Branch name -> git rev resolution.

GBK identifies a branch by its plain name (``feat/mermaid``) whether it exists locally,
on the remote, or both. Git does not: ``refs/heads/feat/mermaid`` and
``refs/remotes/origin/feat/mermaid`` are different refs, and a name that exists only on
the remote resolves to neither under its plain form.

Getting that wrong fails *silently*, which is why this indirection is mandatory rather
than cosmetic. ``GIT_ERRORS`` includes ``LookupError``, and ``IndexError`` subclasses it,
so ``repo.refs["feat/mermaid"]`` for a remote-only branch raises, is swallowed by the
surrounding ``except GIT_ERRORS``, and the branch degrades to age 0 / not-merged with
nothing but a debug log to show for it. Every git call that takes a branch name must go
through :meth:`BranchRefResolver.resolve` and hand git a rev string it can resolve.

Local and selected-remote refs are snapshotted once per analysis. Resolution happens
several times per branch and analysis is parallel, so scanning every remote ref for every
lookup would become quadratic. Discovery refreshes the snapshot; destructive operations
refresh it again immediately before acting.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

import git

from git_branch_keeper.exceptions import GIT_ERRORS
from git_branch_keeper.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BranchRefState:
    """Live location and effective tip for one plain branch name."""

    tip_sha: str
    has_local: bool
    has_remote: bool


class BranchRefResolver:
    """Resolves plain branch names to revs git can act on."""

    def __init__(self, repo_path: str, remote_name: str = "origin"):
        self.repo_path = repo_path
        self.remote_name = remote_name
        self._snapshot: dict[str, BranchRefState] | None = None
        self._snapshot_lock = Lock()

    def _get_repo(self):
        """Fresh git.Repo per call, matching the thread-safety pattern used elsewhere."""
        return git.Repo(self.repo_path)

    def _scan(self, repo) -> dict[str, BranchRefState]:
        """Read local and selected-remote refs once, with the local tip winning."""
        local = {head.name: head.commit.hexsha for head in repo.heads}
        remote: dict[str, str] = {}
        prefix = f"{self.remote_name}/"

        try:
            for ref in repo.remote(self.remote_name).refs:
                if not ref.name.startswith(prefix):
                    continue
                name = ref.name[len(prefix) :]
                if name != "HEAD":
                    remote[name] = ref.commit.hexsha
        except GIT_ERRORS as e:
            # A repository without the selected remote still has a perfectly useful
            # local snapshot.
            logger.debug(f"[refs] Could not enumerate {self.remote_name} refs: {e}")

        return {
            name: BranchRefState(
                tip_sha=local[name] if name in local else remote[name],
                has_local=name in local,
                has_remote=name in remote,
            )
            for name in local.keys() | remote.keys()
        }

    def snapshot(self, repo=None, *, refresh: bool = False) -> dict[str, BranchRefState]:
        """Return a shared ref snapshot, rescanning only at explicit boundaries.

        Branch analysis asks the same location questions several times per branch and
        does so concurrently. Scanning every remote ref for every question turns that
        into quadratic work. Discovery refreshes this snapshot once per analysis;
        deletion refreshes it again immediately before acting.
        """
        with self._snapshot_lock:
            cached = self._snapshot
        if cached is not None and not refresh:
            return cached

        try:
            repo = repo or self._get_repo()
            scanned = self._scan(repo)
        except GIT_ERRORS as e:
            logger.debug(f"[refs] Could not build ref snapshot: {e}")
            scanned = {}

        with self._snapshot_lock:
            self._snapshot = scanned
        return scanned

    def invalidate(self) -> None:
        """Discard the snapshot after a ref-mutating operation."""
        with self._snapshot_lock:
            self._snapshot = None

    def has_local(self, branch_name: str, repo=None, *, refresh: bool = False) -> bool:
        """Whether ``refs/heads/<branch_name>`` exists."""
        state = self.snapshot(repo, refresh=refresh).get(branch_name)
        return bool(state and state.has_local)

    def has_remote(self, branch_name: str, repo=None, *, refresh: bool = False) -> bool:
        """Whether ``refs/remotes/<remote>/<branch_name>`` exists."""
        state = self.snapshot(repo, refresh=refresh).get(branch_name)
        return bool(state and state.has_remote)

    def is_remote_only(self, branch_name: str, repo=None, *, refresh: bool = False) -> bool:
        """Whether the branch exists on the remote but has no local head."""
        state = self.snapshot(repo, refresh=refresh).get(branch_name)
        return bool(state and state.has_remote and not state.has_local)

    def resolve(self, branch_name: str, repo=None, *, refresh: bool = False) -> str:
        """Return a rev git can resolve for this branch name.

        The local head wins when both exist: it is what the user can check out, delete,
        and lose work on, and its tip may be ahead of the remote.

        A name that is neither local nor remote is returned unchanged, so callers fail
        the same way they did before this indirection existed rather than against a
        fabricated ``origin/`` ref that never existed.
        """
        state = self.snapshot(repo, refresh=refresh).get(branch_name)
        if state and state.has_local:
            return branch_name
        if state and state.has_remote:
            return f"{self.remote_name}/{branch_name}"
        return branch_name

    def remote_only_branch_names(self, repo=None, *, refresh: bool = True) -> list[str]:
        """Plain names of branches that exist on the remote but not locally.

        ``<remote>/HEAD`` is excluded: it is a symbolic pointer to the default branch,
        not a branch of its own, and would otherwise show up as a phantom row named
        ``HEAD``.
        """
        states = self.snapshot(repo, refresh=refresh)
        return sorted(
            name for name, state in states.items() if state.has_remote and not state.has_local
        )
