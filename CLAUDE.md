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

# Preview what would be deleted (dry run)
git-branch-keeper --no-interactive --filter merged --dry-run

# Delete merged branches with confirmation (CLI mode)
git-branch-keeper --no-interactive --filter merged

# Delete with force (no confirmation - DANGEROUS!)
git-branch-keeper --no-interactive --filter merged --force

# Debug mode for troubleshooting
git-branch-keeper --debug
```

**Important**:
- CLI mode (`--no-interactive`) deletes branches by default (with confirmation)
- Deletion is **local-only by default**; the remote branch is kept unless `--remote` is passed (`config.delete_remote`)
- Always use `--dry-run` first to preview changes
- The `--cleanup` flag is deprecated (cleanup is now the default behavior in CLI mode)

## Architecture

The codebase follows a service-oriented architecture:

- **core.py**: Main BranchKeeper class that orchestrates all operations
- **services/**:
  - `git_service.py`: Handles Git operations (branch listing, deletion, merge detection)
  - `github_service.py`: GitHub API integration for PR status
  - `branch_status_service.py`: Determines branch status (merged, stale, has PR)
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
- Modal screens (ConfirmScreen, InfoScreen) have isolated `BINDINGS` with Enter/Escape for confirm/cancel
- No `priority=True` flags on bindings to avoid modal conflicts
- This separation prevents Enter key conflicts between the table and modal dialogs

**Background Operations:**
- Async workers use `@work` decorator for non-blocking operations
- DataTable has a built-in loading indicator for async data fetching
- Cache service enables fast initial load with background refresh

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
3. **Combined-diff exact match** (`_check_squash_merge`, last resort) — branch's combined
   diff equals a single commit on main. Covers multi-commit squash merges (N commits
   collapsed into 1, so no per-commit patch-id match).

**Squash detection has two confidence levels.** An *exact* combined-diff match counts as
merged/deletable. A *fuzzy* high-similarity substring match does NOT mark the branch
merged — diff-text containment doesn't prove the work is in main (it may have been
reverted). Instead it sets `MergeDetector._likely_squash_merged` (exposed via
`is_likely_squash_merged()`), which surfaces a "possible squash-merge - verify before
deleting" note. A heuristic guess must never make a branch auto-deletable.

When a GitHub token is present, a merged PR (from the API) is authoritative and is used
ahead of these git checks in `BranchStatusService`.

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
- **GitHub integration is OPTIONAL**: The tool works on any Git repo without a GitHub token
  - Without token: Branch analysis, merge detection, and cleanup work normally
  - With token (GitHub only): Adds PR detection and protection against deleting branches with open PRs
- Test framework: pytest (run with `make test`); CI runs the full suite on Python 3.9-3.13
- TUI has tests too: pure marking/validation logic in `tests/test_tui_marking.py`, and
  Textual `run_test()` pilot harness tests in `tests/test_tui_app.py` (async, `asyncio_mode = "auto"`)
- Branch deletions are journaled and recoverable via `git-branch-keeper undo` as long as the commit objects exist