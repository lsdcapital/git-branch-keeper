# Data Loss Risk Documentation & Mitigation Plan

> This document tracks identified data loss risks and potential data corruption scenarios in git-branch-keeper. Each item includes detailed analysis, code references, and recommended mitigations.

## Priority Levels

- **🔴 Critical**: Could result in permanent loss of branch history or uncommitted work
- **🟠 High**: Could cause unexpected branch deletion or data integrity issues
- **🟡 Medium**: Could cause confusion or workaround needs, minor data loss scenarios
- **🟢 Low**: Edge cases with low probability or minimal impact

---

## Critical Risks

### 🔴 1. Local Branch Deleted Before Remote Protection Check

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: Critical
**Impact**: User loses local branch even when remote is protected (non-recoverable locally)

**Code Location**: `git_branch_keeper/services/git_service.py:1082-1136` (`delete_branch` method)

**Problem Description**:
The deletion logic deletes the local branch first (line 1094), then attempts remote deletion. If the remote deletion fails due to branch protection, the local branch is already deleted and cannot be recovered through this tool.

```python
# Line 1094 - Local deletion happens FIRST
repo.delete_head(branch_name, force=True)

# Lines 1097-1118 - Remote deletion AFTER local is gone
if has_remote:
    try:
        remote.push(refspec=f":{branch_name}")
    except git.exc.GitCommandError as e:
        if "protected" in str(e).lower():
            console.print(f"Warning: Remote branch is protected...")
            # But local is already deleted!
```

**Scenario**:
1. User runs cleanup on a merged branch
2. Branch exists both locally and remotely
3. Remote branch is protected (GitHub branch protection rules)
4. Tool deletes local branch ✓
5. Tool fails to delete remote branch (protected) ✗
6. User has no local copy; branch is still on remote

**Recommended Fix**:
- Check remote branch protection **before** deleting local
- Or: Delete remote first, then local
- Or: Implement transaction-like behavior with rollback capability

**Implementation Complexity**: Medium (requires checking branch protection status via GitHub API)

---

### 🔴 2. Merge Detection False Positives (Squash Merge Heuristic)

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: Critical
**Impact**: Active branches mistakenly identified as merged and deleted

**Code Location**: `git_branch_keeper/services/git_service.py:913-945` (Method 0: `_check_squash_merge`)

**Problem Description**:
The squash merge detection uses a string comparison heuristic that could have false positives:

```python
def _check_squash_merge(self, branch_name: str, main_branch: str) -> bool:
    # ...
    branch_diff = repo.git.diff(f"{main_branch}...{branch_name}", "--no-color")

    for commit in repo.iter_commits(main_branch, max_count=100):
        commit_diff = repo.git.show(commit.hexsha, "--no-color", "--format=")

        # ⚠️ PROBLEMATIC: Simple string containment check
        if len(branch_diff) > 50 and branch_diff in commit_diff:
            logger.debug(f"Found squash merge in commit {commit.hexsha}")
            return True
    return False
```

**Problem Scenarios**:
1. Branch A modifies `src/utils.py` with 100 lines of changes
2. Branch B independently modifies `src/utils.py` with 200 lines of changes (including all of Branch A's changes)
3. Both branches are merged separately via PRs (Branch A into main first, then Branch B)
4. When checking Branch B for merge, the tool finds Branch A's diff inside Branch B's commit diff
5. False positive: Branch B is incorrectly marked as squash-merged when checking other branches

**Real-world Example**:
- Feature branch adds utility functions
- Later feature branch adds more utilities (includes all previous ones)
- Both are independent PRs merged normally
- Second branch could be marked as already merged due to diff containment

**Current Safeguard**:
Method detection order has this as first check, but if it returns true, merges are cached and other methods aren't tried.

**Recommended Fix**:
- Use more robust merge detection (check commit ancestry more carefully)
- Only use squash detection after confirming with other methods
- Add configurable threshold or disable this method by default
- Compare commit hashes, not diffs

**Implementation Complexity**: Medium-High (requires rethinking merge detection strategy)

---

### 🔴 3. Branch Checkout Without Proper Isolation

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: Critical
**Impact**: User left on wrong branch if checkout/restore fails; risk of commits on unintended branch

**Code Location**: `git_branch_keeper/services/git_service.py:393-486` (get_branch_status_details method)

**Problem Description**:
When checking branch status, the tool checks out branches in the main working tree. If restoration fails or is interrupted, user could be left on the wrong branch:

```python
try:
    if need_restore:
        repo.git.checkout(branch_name)  # Switch to branch being checked

    status = repo.git.status("--porcelain")

    return {"modified": bool(...), "untracked": bool(...), ...}
finally:
    if need_restore:
        # ⚠️ What if this fails? User is left on wrong branch
        if (not is_detached and current != branch_name) or is_detached:
            repo.git.checkout(current)  # Try to restore - can fail!
```

**Failure Scenarios**:
1. Restoration checkout fails (merge conflict, locked file, etc.)
2. Process is killed between checkout and restore
3. Permission issues on restore
4. Result: User finds themselves on a different branch than expected, risking accidental commits

**Compounding Risk**: Combined with cleanup mode running automatically, user might not notice until they've committed work on the wrong branch.

**Recommended Fix**:
- Use `git worktree` to check branch status without switching branches
- Or: Use `git show` and other non-checkout commands to determine file status
- Or: Implement proper error handling with rollback and user notification
- Store current branch SHA and verify restoration succeeded

**Implementation Complexity**: High (requires architectural change to status checking)

---

## High Severity Risks

### 🟠 4. Default Cleanup Mode (No Confirmation By Default)

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: High
**Impact**: First-time users may accidentally delete branches without realizing

**Code Location**: `git_branch_keeper/config.py:22` and `git_branch_keeper/cli.py:22-24`

**Problem Description**:
The tool defaults to cleanup mode (`dry_run=False`), meaning it will delete branches by default. First-time users might expect a preview/dry-run by default:

```python
# config.py - Default is cleanup, NOT preview
dry_run: bool = False  # Default to cleanup mode

# cli.py - cleanup_enabled defaults to True
keeper.process_branches(cleanup_enabled=not parsed_args.dry_run)
```

**Scenario**:
1. User downloads and tries the tool: `git-branch-keeper --filter merged`
2. Expects to see what would be deleted
3. Instead, branches are immediately deleted (with --force skipping even confirmation)
4. User realizes tool was more aggressive than expected

**Current Safeguard**:
Interactive confirmation (with user prompts) if not in force mode, but first-time users might not expect to be in cleanup mode.

**Recommended Fix**:
- First run detection: on first run, default to dry-run mode
- Or: Require explicit `--cleanup` flag (conservative by default)
- Or: Show prominent warning on first run about cleanup mode
- Or: Implement "safe mode" for first-time users

**Implementation Complexity**: Low-Medium (configuration and first-run detection)

---

### 🟠 5. Stash Restore Failures Leave Changes Unrecoverable

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: High
**Impact**: Uncommitted changes left in stash, user must manually recover

**Code Location**: `git_branch_keeper/services/git_service.py:1041-1080` (stash methods)

**Problem Description**:
If restoring stashed changes fails, the tool warns the user but changes remain in the stash. This could be confusing and lead to perceived data loss:

```python
def restore_stashed_changes(self, was_stashed: bool) -> None:
    if not was_stashed:
        return

    try:
        repo = self._get_repo()
        repo.git.stash("pop")  # ⚠️ If this fails, warning is shown
        logger.debug("Restored stashed changes")
    except Exception as e:
        logger.warning(f"Could not restore stashed changes: {e}")
        logger.warning("Your changes are still in the stash. Run 'git stash pop' manually.")
        raise  # Raises to caller
```

**Failure Scenarios**:
1. Stash pop fails due to merge conflict
2. Stash pop fails due to file permissions
3. Process interrupted during stash restore
4. User sees warning but might not notice, unaware changes are in stash
5. Result: Changes appear "lost" until user manually checks stash

**Compounding Risk**: If user doesn't see the warning (e.g., running in background script), they think changes are gone.

**Recommended Fix**:
- Explicitly list stash contents on failure
- Don't suppress the error - propagate clearly to user
- Provide recovery command in error message
- Implement automated recovery with user confirmation

**Implementation Complexity**: Low-Medium (improved error handling and recovery)

---

### 🟠 6. "origin" Remote Hardcoded - Other Remotes Ignored

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: High (in multi-remote repositories)
**Impact**: Branches on non-"origin" remotes treated as local-only, incorrectly marked for deletion

**Code Location**: `git_branch_keeper/services/git_service.py:37`

**Problem Description**:
The tool hardcodes the remote name as "origin", so branches existing on different remotes aren't recognized:

```python
class GitService:
    def __init__(self, repo_path: str, config: Union["Config", dict]):
        # ...
        self.remote_name = "origin"  # ⚠️ Hardcoded!
```

Used in:
- `has_remote_branch()` (line 285)
- `delete_branch()` (line 1101)
- Any remote operation assumes "origin"

**Scenario**:
1. Repository has remotes: `origin` and `upstream`
2. Branch exists on `upstream` only (or in addition to `origin`)
3. Tool checks `has_remote_branch("my-feature")`
4. Returns false (because it only checks "origin")
5. Branch marked as "local-only"
6. If deleted, branch is lost from `upstream` as well (or user loses tracking)

**Recommended Fix**:
- Make remote name configurable
- Auto-detect primary remote (usually "origin", but fallback to any remote)
- Support multi-remote tracking
- Warn if multiple remotes exist and only checking one

**Implementation Complexity**: Medium (configuration + detection logic)

---

### 🟠 7. Force Mode Bypasses Uncommitted Changes Check

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: High
**Impact**: Branches with uncommitted work deleted without warning in force mode

**Code Location**: `git_branch_keeper/core.py:206-243` (delete_branch method)

**Problem Description**:
When using `--force` flag, the tool skips the uncommitted changes check:

```python
def delete_branch(self, branch_name: str, reason: str, force_mode: bool = False) -> tuple:
    # ...

    # ⚠️ Skipped entirely if force_mode=True
    if not force_mode and (
        status_details.get("modified")
        or status_details.get("untracked")
        or status_details.get("staged")
    ):
        # Safety prompts here
        ...
    else:
        # No prompts in force mode - branch is deleted!
```

**Scenario**:
1. User runs: `git-branch-keeper --filter merged --cleanup --force`
2. A "merged" branch actually has uncommitted work (merge detection false positive, or stale changes)
3. Branch is deleted without prompting about uncommitted changes
4. Uncommitted work is lost

**Compounding Risk**: Force mode also skips confirmation dialogs, so this could be silent data loss.

**Recommended Fix**:
- Always check for uncommitted changes, even in force mode
- Force mode should skip **confirmation** dialogs, not safety checks
- Add `--force-unsafe` for truly no-safety-checks mode with prominent warnings
- Separate "force deletion" from "force safety bypass"

**Implementation Complexity**: Low (logic restructuring)

---

## Medium Severity Risks

### 🟡 8. Worktree Race Condition

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: Medium
**Impact**: Low probability - branch could be checked out in worktree between check and deletion

**Code Location**: `git_branch_keeper/core.py:191-195` and `git_branch_keeper/services/git_service.py:206-244`

**Problem Description**:
Small time window between checking if a branch is in a worktree and attempting to delete it:

```python
# Check status (get_branch_status_details)
status_details = self.git_service.get_branch_status_details(branch_name)

# If no worktree was detected...
if not status_details.get("in_worktree"):
    # ... continue to deletion
    # ⚠️ Race: Branch could be checked out in new worktree HERE

# Try to delete
self.git_service.delete_branch(branch_name)
```

**Scenario** (unlikely but possible):
1. Tool checks branch status - not in worktree
2. Another process creates worktree with this branch (in parallel)
3. Tool attempts to delete branch
4. Deletion might fail due to worktree, or succeed with unintended consequences

**Recommended Fix**:
- Re-check worktree status immediately before deletion
- Use atomic operations where possible
- Implement lock mechanism

**Implementation Complexity**: Low-Medium

---

### 🟡 9. Interactive Input in Non-TTY Could Hang

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: Medium
**Impact**: Process hangs if run in automation context without TTY

**Code Location**: `git_branch_keeper/core.py:238` and `cli.py:42-44`

**Problem Description**:
If running without TTY but interactive mode is triggered, `input()` call could hang:

```python
# cli.py - TTY detection exists but might not always work
use_interactive = parsed_args.interactive or (
    sys.stdin.isatty() and not parsed_args.no_interactive
)

# core.py - But if we get here and there's no TTY...
if self.interactive and not self.tui_mode:
    response = input(f"Still want to delete branch {branch_name}? [y/N] ")
    # ⚠️ Hangs if no TTY available
```

**Scenario**:
1. Running in CI/CD without proper TTY setup
2. TTY detection passes (or is bypassed)
3. Tool prompts for confirmation
4. No input available
5. Process hangs indefinitely

**Recommended Fix**:
- Robust TTY detection with fallback to non-interactive
- Timeout on input() with sensible default
- Error rather than hang if TTY required but unavailable

**Implementation Complexity**: Low-Medium

---

### 🟡 10. Cache Staleness Could Lead to Incorrect Status

**Status**: [ ] Not started | [ ] In progress | [ ] Complete

**Severity**: Medium
**Impact**: Outdated cached status could lead to deletion of branches that have changed

**Code Location**: `git_branch_keeper/services/cache_service.py` (cache operations)

**Problem Description**:
Cached branch status might become outdated between runs. A branch marked as "merged" in cache could have new commits added before cache expires.

**Scenario**:
1. Run 1: Branch marked as merged, cached with status=merged
2. Between runs: Someone pushes commits to the branch
3. Run 2: Cache is used, branch still shows as merged (cached data)
4. Branch is marked for deletion based on stale cache
5. Actual branch has recent work

**Current Safeguard**:
Cache invalidation on certain conditions, but timing could be an issue.

**Recommended Fix**:
- Shorter cache TTL for branches in deleted state
- Always re-check merge status for branches about to be deleted
- Add cache validation step
- Warn when using cached data for deletion decisions

**Implementation Complexity**: Low-Medium

---

## Summary & Priorities

| Priority | Count | Next Steps |
|----------|-------|-----------|
| 🔴 Critical | 3 | Address before next release |
| 🟠 High | 4 | Address in upcoming sprints |
| 🟡 Medium | 3 | Plan improvements |

**Immediate Action Items** (Critical fixes):
- [ ] Fix merge detection false positives
- [ ] Implement worktree-based status checking
- [ ] Change branch deletion order (check remote before local)

**High Priority** (Next release):
- [ ] First-run safety detection
- [ ] Improve stash error recovery
- [ ] Add multi-remote support

---

## Testing Recommendations

For each fix, include tests for:
1. Normal case (happy path)
2. Error cases (permissions, network, etc.)
3. Concurrent/race conditions
4. Integration with full deletion workflow
5. Data recovery scenarios

