# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

git-branch-keeper is a Git branch management tool that helps developers identify and clean up merged and stale branches while protecting branches with open pull requests. It works with any Git repository (GitHub, GitLab, Bitbucket, or local). It uses GitPython for repository operations and PyGithub for optional GitHub API integration.

## Key Commands

### Development Setup
```bash
# Install dependencies with uv (creates venv automatically)
uv sync --dev

# Run the tool
uv run git-branch-keeper [options]

# Or activate the virtual environment first
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
git-branch-keeper [options]
```

### Common Usage
```bash
# Interactive TUI (default, safest)
git-branch-keeper --filter merged

# Plain CLI report (read-only)
git-branch-keeper --cli --filter merged

# Preview what would be deleted (dry run)
git-branch-keeper --cli --filter merged --dry-run

# Delete merged branches with confirmation (CLI mode)
git-branch-keeper --cli --filter merged --delete

# Delete with force (no confirmation - DANGEROUS!)
git-branch-keeper --cli --filter merged --delete --force

# Debug mode for troubleshooting
git-branch-keeper --debug
```

**Important**:
- CLI mode (`--cli` / `--no-interactive`) is read-only by default
- Deletion requires explicit `--delete` (deprecated `--cleanup` remains an alias; legacy `--force` also implies delete)
- For branches with local and remote refs, deletion is **local-only by default**; the
  matching remote is kept unless `--remote` is passed (`config.delete_remote`)
- Use `--dry-run` to preview cleanup candidates without prompts or changes
- Branches that exist only on the remote are analyzed by default. Positively merged
  remote-only refs are cleanup candidates; stale/unmerged ones are never deleted.
  `--no-remote-branches` restores the local-only view. The `--remote` flag separately
  controls matching-remote deletion for branches that also exist locally.

## Architecture

The codebase follows a service-oriented architecture:

- **core.py**: Main BranchKeeper class that orchestrates all operations
- **services/**:
  - `git_service.py`: Handles Git operations (branch listing, deletion, merge detection)
  - `github_service.py`: GitHub API integration for PR status
  - `branch_status_service.py`: Determines branch status (merged, stale, has PR)
  - `git/refs.py`: `BranchRefResolver` — maps a branch name to the rev git should be
    handed (`<name>` or `<remote>/<name>`); see "Remote-only branches"
  - `deletion_journal.py`: Records every deleted branch (with tip SHA) to `~/.git-branch-keeper/deletions.jsonl`; powers `git-branch-keeper undo` (see `cli/undo.py`)
  - `display_service.py`: Terminal UI using Rich library
- **models/branch.py**: Data models for branch information and status
- **config.py**: Configuration management with JSON file support
- **ui/**: Interactive TUI (Terminal User Interface) using Textual framework
  - `app.py`: Main BranchKeeperApp with DataTable-based interface
  - `screens.py`: Modal dialogs (ConfirmScreen, InfoScreen, TabbedInfoScreen)
  - `widgets.py`: Custom widgets (NonExpandingHeader)

### TUI Architecture

The TUI uses Textual framework with an event-driven design to handle keyboard interactions:

**Key Event Handling Pattern:**
- Main app uses `on_data_table_row_selected()` event handler for Enter key instead of a binding
- This is because DataTable has a built-in Enter binding that would conflict with app-level bindings
- No `priority=True` flags on bindings to avoid modal conflicts
- This separation prevents Enter key conflicts between the table and modal dialogs

**`ConfirmScreen` deliberately has no `enter` binding.** Textual resolves the *focused
widget's* bindings before the screen's, and `Button` binds `enter` → `press`. Since
`AUTO_FOCUS = "#no"` focuses the safe option, Enter activates Cancel; a screen-level
`enter` → confirm binding would be unreachable while a button is focused but would fire
if focus moved into the scrollable message body, making Enter mean two different things.
Confirming is `y` (no widget in the chain binds it), Tab-then-Enter, or a click. Do not
"restore" the Enter binding without also moving focus off the destructive button — that
combination is what made the old dialog confirm deletion on a stray Enter.

All three modals inherit shared chrome from `BaseModal` (`ui/screens.py`). Its rules
select by CSS **class** (`.modal-dialog`, `.modal-body`, `.modal-buttons`) and subclasses
override by **id**, so overrides win on plain specificity rather than on `DEFAULT_CSS`
merge order. Two traps: a rule targeting the screen itself must literally start with
`BaseModal` (Textual scopes each rule to its declaring class, so `ModalScreen { … }`
becomes the descendant selector `BaseModal ModalScreen { … }` and matches nothing), and
`TabbedInfoScreen` must keep `max-width: 100%` / `max-height: 100%` or the base's 90%/80%
caps clamp its 90%x80% dialog to 81%x64%.

**Background Operations:**
- Async workers use `@work` decorator for non-blocking operations
- DataTable has a built-in loading indicator for async data fetching
- Cache service enables fast initial load with background refresh

**Marks are keyed by branch name; rows are not.** `_row_key_for_branch()` keys
worktree rows as `name:path` so the DataTable can tell them apart, but
`marked_branches` / `force_marked_branches` hold plain names. That is deliberate -
marking a branch lights up its worktree row too, and deletion removes the worktree
and then the branch as one operation. It also means anything *counting* candidates
must count names, and must ask `_plan_deletable()` rather than filtering rows with
`is_deletable()`: the planner additionally drops the checked-out branch and adds
branches unblocked by removing a clean worktree. Counting rows made the status bar
advertise `Deletable: 1 | Marked: 0` for a current branch nothing could ever mark -
routine when GBK runs from a linked worktree, where the checked-out branch is a
feature branch. See `tests/test_tui_app.py::test_status_bar_*`.

**Cached rows are painted before the real analysis lands**, so startup applies two
results in a row. The second apply preserves marks (the user may have marked
something meanwhile) but passes `mark_newly_deletable=True`, which auto-marks only
branches that were *not* candidates in the first pass. Without it, a branch merged
since the previous run renders as `merged` yet stays unmarked until the next
launch, because the cached pass never offered it. `action_refresh` (`r`) preserves
marks unconditionally - an unmark there is a deliberate choice.

## Important Patterns

### Merge Detection Strategy
`MergeDetector` (`services/git/merge_detector.py`) uses three principled, git-native
checks, ordered cheapest-first. Each maps to a real merge style; see
`tests/test_merge_detection_accuracy.py` for the full matrix.
1. **Reachability** (`_check_reachable`, `git merge-base --is-ancestor`) — branch tip
   reachable from main. Covers ordinary merge commits and fast-forward merges.
2. **Patch-equivalence** (`_check_patch_equivalent`, `git cherry`) — every commit unique
   to the branch has a patch-identical commit already in main. Covers rebase-merges,
   cherry-picks, and single-commit squashes (work in main under different SHAs). This is
   what catches rebase-merges, which the older diff-only approach missed.
3. **Combined patch-id exact match** (`_check_squash_merge`, last resort) — branch's
   combined diff has the same stable patch-id as a first-parent commit on main since
   the branch fork point (capped by `squash_scan_limit`, default 500). Covers
   multi-commit squash merges (N commits collapsed into 1, so no per-commit patch-id
   match).

**Unstarted branches are excluded before any of this runs.** A branch created from main
that was never committed to has no commits of its own, so check 1 matches trivially — its
tip *is* an ancestor of main — and it would be reported `merged`/`merged-git`, asserting a
merge that never happened and making a freshly-cut branch (e.g. a Conductor `git worktree
add -b` workspace whose work is still uncommitted) a cleanup candidate.
`MergeDetector.is_unstarted_branch()` catches these first, and `BranchStatusService` returns
`BranchStatus.UNSTARTED` / `SyncStatus.NO_COMMITS`. That status is deliberately absent from
every `[STALE, MERGED]` check in the codebase, so such branches are never deletable.

Do not reduce that check to `git rev-list --count main..branch == 0`. **A fast-forward-merged
branch also has zero unique commits** — its commits became main's — and refs alone cannot
separate the two, since a fast-forward leaves no merge commit and no record that those
commits were ever the branch's. The reflog is that record: an unstarted branch has exactly
one entry (`branch: Created from …`), a fast-forward-merged one also has its `commit:`
entries. So the check requires *positive* proof and returns False whenever the reflog is
missing, expired, disabled, or shows the branch moved — falling back to ordinary merge
detection. Erring toward `merged` is safe (the deletion guards below re-verify); erring
toward `unstarted` would hide a genuinely merged branch behind a never-deletable label.
See `tests/test_unstarted_branches.py`, whose `TestMergedBranchesStayMerged` is the
regression guard for exactly this.

**Squash detection has two confidence levels.** An *exact* combined patch-id match counts as
merged/deletable. A *fuzzy* high-similarity substring match does NOT mark the branch
merged — diff-text containment doesn't prove the work is in main (it may have been
reverted). Instead it sets `MergeDetector._likely_squash_merged` (exposed via
`is_likely_squash_merged()`), which surfaces a "possible squash-merge - verify before
deleting" note. A heuristic guess must never make a branch auto-deletable.

**A partial result from check 2 is kept, not discarded.** `git cherry` answers per
commit, but merging requires *all* of them to be `-`. When the answer is mixed, the
branch is correctly not merged — and the mix is itself the finding: work reached main
and a commit was left behind. That is the ordinary shape of an orphan in a squash-merge
repo, where a commit pushed moments after the PR merges never lands. `_check_patch_equivalent`
records `(landed, total)` in `MergeDetector._partial_merge` (exposed via
`get_partial_merge()`), surfaced as a "partially merged: 1/2 commits in main, 1 not
landed" note. Advisory only — a partial merge is not a merge, and nothing about this
makes a branch deletable.

Recorded only when `landed > 0`. "0/N commits in main" is true of every in-progress
branch in every repo, so emitting it would put a note on every active branch and cost
the Notes column the scannability that makes the partial case stand out. The distinction
matters because `active` is GBK's *no-signal* bucket, not a "someone is working on this"
claim: without this note a 1-of-2-landed branch renders exactly like a branch cut an hour
ago. Note that the git-native path is the only one that reports this when GitHub auth is
absent — the PR-derived "merged but local tip differs from PR head" note covers a similar
case but requires a token. See `tests/test_partial_merge_note.py`.

When GitHub auth is available (`github_token`, `GITHUB_TOKEN`, `GH_TOKEN`, or
authenticated `gh` CLI), PR metadata is fetched inside each branch processing worker
rather than as a separate prefetch phase. GitHub-enabled branch workers are capped below
PyGithub/urllib3's default API connection pool size to avoid connection-pool warning
spam. Open PRs keep branches active/protected. A merged PR is authoritative only when
the local branch tip still matches the PR head SHA; if the local tip differs, GBK adds
a warning note and falls through to the git-native checks above.

### Remote-only branches

GBK enumerates `refs/remotes/<remote>/*` alongside `refs/heads/*` **by default**
(`include_remote_branches`, opt out with `--no-remote-branches`). Enumerating local heads
alone made GBK blind to the actual problem: branch accumulation is a *remote* phenomenon,
because local branches are self-limiting - you notice them. On one real repo GBK analysed
5 branches while 41 existed on origin, and reported "nothing to clean up" for a repo
carrying ~37 prunable remote branches. Remote-tracking refs are only as fresh as the last
`git fetch`; GBK does not fetch implicitly.

**Identity stays the plain name.** `BranchDetails.name` is `feat/mermaid`, never
`origin/feat/mermaid`, so `--ignore`/`--protected` patterns, cache keys, TUI marks, and
the deletion journal all keep working unchanged. Location is an attribute:

| `has_local` | `has_remote` | meaning |
|---|---|---|
| T | T | ordinary tracked branch |
| T | F | local-only (`SyncStatus.LOCAL_ONLY`) |
| F | T | remote-only (`SyncStatus.REMOTE_ONLY`) |

**Every git call must go through `BranchRefResolver` (`services/git/refs.py`).** It maps a
branch name to a *rev string* - the plain name when a local head exists, else
`<remote>/<name>` - which satisfies every existing call shape (`repo.refs[rev]`,
`git cherry`, `git rev-list`, `git merge-base`, `git diff`). This is not cosmetic:
`GIT_ERRORS` includes `LookupError` and `IndexError` subclasses it, so
`repo.refs["remote-only-name"]` raises, gets swallowed, and the row degrades to
age 0 / not-merged **silently**. MergeDetector's memo and its `_partial_merge` /
`_likely_squash_merged` / `_unstarted_unverifiable` collections stay keyed by the plain
name; only the rev handed to git changes.

The resolver snapshots local and selected-remote refs once per analysis. Resolution is
called several times per branch and analysis runs concurrently, so rebuilding the complete
remote-ref list for every lookup is quadratic. Branch discovery refreshes the snapshot;
the deletion boundary refreshes it again before touching a ref.

The easy sites to miss are the ones outside merge detection, because they fail *quietly
enough to look answered*: `get_comparison_to_main()`, `get_branch_commits()`, and
`get_divergence_info()` all interpolate the branch into a `main..<branch>` revision range.
Unresolved, `git rev-list main..feat/x` is a `bad revision` fatal, `GIT_ERRORS` turns it
into `comparison_to_main: {checked: false}`, and the JSON row still renders - just with no
ahead/behind and a silently downgraded confidence. On one real repo that was 37 of 42 rows.
`get_file_status_detailed()` and `get_diff()` are the other pair: the TUI's Files and Diff
tabs call them directly, bypassing the analysis path, so each needs its own remote-only
short-circuit rather than relying on the one in `get_branch_status_details`.

**Only positively merged remote-only branches are deletable.** Stale age is not enough
when the only mutation is `git push <remote> --delete`; `BranchValidationService` and the
force-mode planner therefore admit `MERGED` only. They are cleanup candidates regardless
of `config.delete_remote`, because there is no local ref and hence no local-only action.
The TUI calls this out in both the status bar and confirmation dialog.

`BranchKeeper._delete_remote_only_branch()` re-runs merge detection at the destructive
boundary and captures the remote-tracking tip. `GitOperations.delete_remote_only_branch()`
then deletes with `--force-with-lease=<ref>:<expected-sha>`, so a ref that advanced on the
actual remote after the last fetch is preserved. Successful deletion journals that SHA
with `remote_deleted=true`; `undo` can recreate the local branch and optionally push the
remote ref back.

**The temp-worktree probe is skipped** for them (`get_branch_status_details`). A branch
with no local checkout cannot have uncommitted work, and the probe costs a checkout per
branch - running it for 38 remote branches would make GBK unusable.

**`cache_service._get_current_branch_states()` must enumerate the same namespace** as
`_get_filtered_branches()`, which is why `CacheService` takes `include_remote`. If it
returned `repo.heads` alone, every remote row would be pruned on each run. A SHA alone is
not sufficient cache identity: local+remote -> remote-only (or the reverse) can keep the
same effective tip. Stable cache entries are therefore refreshed whenever `has_local` or
`has_remote` changes as well as when the tip moves.

A remote-only branch with **zero unique commits** is the ambiguous case: there is no local
reflog to consult (`git reflog show origin/foo` records this clone's *fetches*), so
`is_unstarted_branch()`'s usual positive proof is unobtainable.
`_is_on_main_first_parent()` stands in as far as it can - a tip *off* main's first-parent
line reached main through a merge commit and is reported `merged` on that content-based
evidence. That still cannot prove the branch *name* existed when the content landed (it
could have been created later at the same commit), so the row carries an explicit
provenance note tracked by `remote_history_is_unverifiable()`. A tip *on* the line is
reported `UNSTARTED` plus a note explaining that never-started and fast-forward-merged are
indistinguishable, tracked via `unstarted_is_unverifiable()`. See
`tests/test_remote_only_branches.py`.

### Worktrees

`WorktreeService` (`services/git/worktrees.py`) owns all worktree state. Two rules
matter and are easy to get wrong:

**`is_main` is not "not ours".** GBK can be run from inside a linked worktree, in
which case the *main* working tree holds some other branch that must still be
protected. Use `get_other_worktrees()` / `find_worktree_for_branch()`, which
compare against `get_current_worktree_path()` (the real path of `repo.working_dir`),
not `wt.is_main`. `is_main` is only for "can this worktree be removed" — Git
refuses to remove the main working tree, and `remove_worktree()` refuses the one
GBK is running in (Git does *not*; with `--force` it would delete the directory
out from under the process). Worktree rows in the table are therefore linked
worktrees other than our own; branches in the main/current worktree are still
protected via `in_worktree` in `_apply_dynamic_worktree_status`.

**The cache is shared, and stale by design.** `get_worktree_info()` caches Git's
worktree list, and `GitOperations` injects a single `WorktreeService` into
`BranchQueries` so both see the same cache — two instances would drift and a
refresh would be invisible to one of them. Before anything destructive, re-read
with `refresh=True`: analysis caches worktree membership and the TUI then sits on
it for as long as the user takes to review. `BranchKeeper.delete_branch()`
refreshes twice — before the status probe (which would otherwise fail with
`already used by worktree at ...` while trying to build a temp worktree) and again
immediately before the delete. See `tests/test_worktree_toctou.py` and
`tests/test_worktree_linked_checkout.py`.

### Deletion Safety

Four independent guards stand between "analysis said merged" and an irreversible
delete. They exist because analysis results are reused across time - from the
on-disk cache between runs, and across however long the TUI waits for the user.

1. **Cached rows are tied to a commit.** `_serialize_branch` records `tip_sha`, and
   `get_stale_branches()` re-analyses any branch whose tip has moved or whose entry
   predates the field. Without this a MERGED branch is cached as `stable` and never
   re-examined, so the ordinary "merge the PR, keep working on the branch" flow
   would delete the new commits.
2. **Merge status is re-verified live.** `BranchKeeper.delete_branch()` re-runs
   Git detection with `force_refresh=True` for anything being deleted as `merged`.
   If Git cannot prove a squash/rebase merge, it re-fetches PR data and accepts it
   only when the PR is still merged and its head SHA exactly matches the current
   effective branch tip. The `MergeDetector` memo is only invalidated when *main*
   moves, so it can outlive changes to the branch itself.
3. **Git gets the last word when it can.** `_git_can_verify_deletion()` uses
   `git branch -d` when the branch is an ancestor of HEAD, and only falls back to
   `-D` where `-d` structurally cannot succeed (rebase/squash merges, and stale
   branches, which are unmerged by definition). Do not "simplify" this back to an
   unconditional `-D`.
4. **Remote-only deletion is leased to the analyzed tip.** There is no local ref for
   `git branch -d` to verify, so the remote push carries an exact expected SHA. If
   the upstream branch advanced after analysis or after the last fetch, deletion
   fails instead of discarding unseen work.

Deletions are journaled to `~/.git-branch-keeper/deletions.jsonl` and scoped by
`DeletionJournal._repo_key()` - the main working tree, derived from `common_dir`,
so every worktree of a repo shares one scope. Do not scope it by the invocation
path: deletions made from a linked worktree would be invisible to `undo` run from
the repo root, and orphaned outright once that worktree is removed.

A merged PR is authoritative only when `head_matches_local is True`. `None` means
the comparison could not run and must fall through to the git-native checks.

### Error Handling
- Services use exceptions for error propagation
- The core BranchKeeper class handles errors gracefully with user-friendly messages
- Debug mode provides detailed stack traces

### Configuration
Configuration follows a hierarchy:
1. Command-line specified config file
2. `git-branch-keeper.json` in current directory
3. `.git-branch-keeper.json` in home directory

## Development Notes

- The project uses type hints throughout for better code clarity
- Rich library is used for all terminal output and formatting
- GitPython is the primary interface for Git operations
- **GitHub integration is OPTIONAL**: The tool works on any Git repo without GitHub auth
  - Without auth: Branch analysis, merge detection, and cleanup work normally
  - With auth (`github_token`, `GITHUB_TOKEN`, `GH_TOKEN`, or authenticated `gh` CLI; GitHub only): Adds PR detection and protection against deleting branches with open PRs
- Test framework: pytest (run with `make test`); CI runs the full suite on Python 3.10-3.13
- TUI has tests too: pure marking/validation logic in `tests/test_tui_marking.py`, and
  Textual `run_test()` pilot harness tests in `tests/test_tui_app.py` (async, `asyncio_mode = "auto"`)
- Branch deletions are journaled and recoverable via `git-branch-keeper undo` as long as the commit objects exist
