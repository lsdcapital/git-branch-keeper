# TODO — Review Findings (2026-09-26)

Findings from a full review of deletion safety, merge detection, and the TUI/CLI layer.
Each item was traced in code; items marked *(repro)* were reproduced in a throwaway repo.
Ordered by severity. Check the box and add the fixing commit when done.

## 🔴 High — can delete unmerged work or misreport a deletion

- [x] **1. Whitespace-only commits make an unmerged branch look squash-merged** *(repro)*
  - `services/git/merge_detector.py:207`, `:277`, `:680` pass
    `--ignore-space-change --ignore-blank-lines` when building patch-id diffs.
  - A post-squash commit that only re-indents code (behavior change in Python/YAML)
    vanishes from the combined diff, so the branch exact-matches the squash commit.
    The deletion-time re-check uses the same flags, and `-D` is used because the branch
    is not an ancestor of HEAD.
  - Fix: drop both flags in all three places (`git patch-id` already normalizes
    intra-line whitespace). Add a regression test.
  - **Done:** flags dropped *and* `git patch-id --verbatim` — the default patch-id strips
    all whitespace, so a re-indent inside a file the branch adds still collided. Requires
    git ≥ 2.39 (README updated); older git disables squash detection rather than guess.
    Test: `test_merge_detection_accuracy.py::test_whitespace_only_commit_after_squash_is_not_merged`.

- [x] **2. (resolved: documented behavior) TUI force-mark deletes stale/unstarted remote-only branches without merge proof**
  - `ui/app.py:719` accepts `is_force` for remote-only rows; `ui/app.py:1230` passes
    `allow_unmerged_remote=True` → `verify_merge=None` at `core/branch_keeper.py:609`.
  - Contradicts CLAUDE.md: "Stale/unmerged ones are never deleted".
  - Related: `formatters/status.py:105` maps `UNSTARTED` → reason `"merged"`, and an
    unstarted branch is trivially reachable, so it passes the merge re-check; its
    worktree is then removed with `--force`, discarding uncommitted work.
  - **Conflict:** `docs/usage.md:119` documents this as intended ("`f` can force-mark
    stale, merged or unstarted remote-only branches without merge proof" under `--remote`),
    CLAUDE.md says the opposite. Decide which is right before changing code.
  - Independent of that decision: UNSTARTED should never get reason `"merged"` — the
    journal records a merge that never happened.
  - **Decided:** `docs/usage.md` is correct; CLAUDE.md was stale and now documents the
    override. **Done:** UNSTARTED deletes under reason `"unstarted"`. Test:
    `test_unstarted_branches.py::TestForceDeletedUnstartedBranch`.

- [x] **3. Merged PR accepted as proof regardless of base branch**
  - `services/branch_status_service.py:71`, `core/branch_keeper.py:1936`, `:1988`.
    Only `_owned_merged_pr` (`:244`) checks `base_ref == main_branch`.
  - Stacked PR merged into `feat-a` (which never lands) → reported merged/deletable.
  - Fix: require `base_ref == main_branch` everywhere a merged PR counts as proof.
  - **Done:** single rule `merged_pr_is_proof()` in `branch_status_service.py`, used by
    analysis, the status-service fallback, and the deletion-time recheck. Also closed a
    second hole: `_determine_branch_status` treated `head_matches_local=None` (no head SHA
    from the API) as a match. Stacked PRs now get a "PR #N merged into X, not main" note.

- [x] **4. Failed remote push can be reported as success** *(repro)*
  - `services/git/operations.py:457` searches all of `str(e)` for "protected"/"prohibited";
    that string includes the command line, i.e. the branch name.
  - `feature/protected-routes` with a rejected push → "protected, deleted local only".
  - Fix: match only `e.stderr`, against specific rejection text.

- [x] **5. Local branch deleted, then call returns failure** *(repro)*
  - `services/git/operations.py:434-466`: local delete happens before the remote push;
    any other push error re-raises → `False`, but the local branch is already gone.
    Cache/TUI keep showing a branch that no longer exists.
  - Fix: report partial success (local deleted, remote kept) instead of failure.
  - **Done (4+5):** any push failure after the local delete now prints git's stderr and
    returns True with `remote_deleted=false` journaled. Test:
    `test_remote_only_branches.py::test_rejected_remote_push_is_partial_success_not_failure`.
  - Follow-up: in the TUI, "remote kept" is only console output, so a user who asked for
    LOCAL + REMOTE sees a plain success. `delete_branch` should return a result object
    (like `RemoteDeletionResult`) so the TUI can surface it.

## 🟠 Medium

- [x] **6. Markup injection in TUI and CLI output** *(repro)*
  - `ui/screens.py` uses `markup=True` at 8 sites (667–912) for commit messages, diffs,
    file contents, notes. Nothing in the package escapes markup.
  - A diff containing `[/]` crashes the TUI; a commit message containing
    `[@click=app.delete_marked]` renders a link that runs app actions.
  - Also: table Notes/Sync cells (`ui/app.py:631`, `:634`), CLI output
    (`formatters/links.py:80,108`, `services/display_service.py:109`,
    `core/branch_keeper.py:1858,1888`).
  - Note: git forbids `[` in ref names, so CLAUDE.md's `feature/[wip]` example can't
    occur; the real vectors are commit text, paths, and git error output.
  - **Done:** info-screen tabs build `textual.content.Content` via `_m()` (trusted markup
    + `$var` substitution, never parsed) — plain `escape()` is defeated by a trailing
    backslash. Table Notes/Sync cells are `Text`. CLI prints `escape()` error/path values;
    branch URLs are percent-encoded. Tests: `test_tui_marking.py::TestInfoScreenRendersUntrustedTextLiterally`.

- [x] **7. `--dry-run` in the TUI reports deletions that never happened**
  - `cli/main.py:181` only disables auto-marking. Marking + confirming shows
    "deleted N branches (press u to undo)" while nothing was deleted.
  - **Done:** confirm dialog is titled "(dry run)"; afterwards rows and marks stay and the
    status bar says "Dry run: would … - nothing changed". Test:
    `test_tui_app.py::test_dry_run_deletion_keeps_rows_and_says_nothing_changed`.

- [x] **8. Every failure exits 0**
  - `process_branches` catches everything; `cli/main.py:188` returns 0. Partial deletion
    failures and `--cli --delete` without a TTY (swallowed `EOFError`) look like success.
  - **Done:** `process_branches()` returns success; any failed deletion or run error exits 1
    (declining the prompt still exits 0). No TTY at the prompt is now an explicit error
    suggesting `--force` / `--dry-run`. Tests in `test_cli_modes.py`.

- [x] **9. Stale-branch deletion has a window where new commits can be lost**
  - Tip compared once at `core/branch_keeper.py:356`, then seconds of GitHub calls,
    worktree refreshes, and a status probe before `-D`.
  - If `tip_sha` is `None` (old cache rows, failed resolution) the guard is skipped.
  - Fix: delete with `git update-ref -d refs/heads/<name> <expected-sha>`.
  - **Done:** `-D` fallback is `_force_delete_local_branch()` (compare-and-delete against
    the tip validated at the start of `delete_branch`, which also covers `tip_sha=None`
    rows within the call); raises `BranchMovedError` with the real reason. Tests in
    `test_delete_safety.py`. A `None` tip still means no check *between analysis and the
    call* — unavoidable without a recorded SHA.

- [x] **10. `protected_branches` is exact-match only**
  - `services/branch_validation_service.py:51`, `:80`. `--protected 'release/*'`
    protects nothing. Use fnmatch like `ignore_patterns`.
  - **Done:** `utils/patterns.py::is_protected_name()` at all ~24 sites. Test:
    `test_cli_modes.py::test_protected_glob_pattern_blocks_deletion`.

## 🟡 Low

- [x] **11.** Branch checked out from `origin/feat` has one `Created from refs/remotes/…`
  reflog entry → reported `UNSTARTED` after its PR merges (`merge_detector.py:534`).
  Safe direction, but hides a cleanup candidate.
- [x] **12.** Space can mark ACTIVE branches (`_mark_with_hierarchy` has no status check);
  they're silently dropped at confirm with no message.
- [x] **13.** `--force --remote` on a stale local+remote branch fails the whole deletion
  ("Git deletion failed") instead of deleting locally and keeping the remote
  (`operations.py:420-423`).
- [x] **14.** Cache validity ignores main (`cache_service.py:520`): switching `main_branch`
  or force-pushing main leaves stale MERGED rows. Display-only; deletion re-verifies.
- [x] **15.** Remote-only protected branches (e.g. `develop`) bypass `BranchRefResolver`
  and are labeled `LOCAL_ONLY` (`branch_queries.py:113-136`).
- [x] **16.** JSON `recommended_actions` emits bare `git branch -D` / `git push --delete`
  with no lease (`formatters/json_output.py:509-516`).
- [x] **17.** `get_merge_details` interpolates the branch name into a regex unescaped and
  unanchored (`branch_queries.py:524`); `foo` matches `org/foo-bar`.

### Low-item notes

- #11: creation from a `refs/remotes/…` / `<remote>/…` source is not unstarted proof.
- #12: worse than reported — the ACTIVE branch's *worktree* row was also marked and removed
  at confirm (taking ignored files like `.env`). Marking now requires a cleanup candidate,
  and confirm only removes removable worktrees.
- #13: remote kept with "(not merged into main)"; local delete proceeds.
- #14: cache records `main_tip_sha`; discarded on main-branch switch or non-fast-forward.
- #15: one `_remote_sync_status()` helper checks location before ahead/behind.
- #16: commands leased to `expected_sha` (`update-ref -d` / `--force-with-lease`).
- #17: also fixed branch `x` matching a PR from `org/feature/x`.

## Carried over from the previous risk list

- [x] **Force mode skips the uncommitted-changes check** (`core/branch_keeper.py:415`).
  Documented as intentional; revisit whether `--force` should skip confirmation only,
  not safety checks.
  - **Done:** it was real data loss — `--cli --delete --force` ran `git worktree remove
    --force` on dirty worktrees of merged branches. `--force` now only skips the prompt;
    the per-row TUI force-mark remains the explicit override. Test:
    `test_cli_modes.py::test_cli_force_never_discards_uncommitted_worktree_files`.
- [x] **Dead stash helpers** — `stash_changes` / `restore_stashed_changes`
  (`services/git/operations.py:305-340`) have no callers. Remove.

Resolved since the previous list: squash heuristic demoted to advisory; status probe uses
a temp worktree instead of checkout; CLI read-only by default; remote auto-detection;
worktree TOCTOU; cache tied to `tip_sha`.

## Structural

- `core/branch_keeper.py` (2,239 lines) and `ui/app.py` (1,677) have roughly doubled since
  June. Most high-severity bugs sit on paths that accumulated flags
  (`allow_unmerged_remote`, `owned_remote_only`, `force_mode`). Consider splitting the
  deletion planner/executor out of `BranchKeeper`.
