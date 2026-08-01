# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Added installed command aliases: `gbk` and `git gbk` (via `git-gbk`).
- **Deletion journal & `undo`**: every deleted branch is recorded with its tip SHA in
  `~/.git-branch-keeper/deletions.jsonl`; `git-branch-keeper undo [BRANCH]` restores it
  (and `undo --list` shows recent deletions)
- **Opt-in paired-remote deletion**: when a branch exists locally and remotely, deletion
  is local-only by default; pass `--remote` to also delete its matching remote ref
- **Remote auto-detection**: the remote is no longer hardcoded to `origin` — a single
  non-`origin` remote (e.g. `upstream`) is detected and used automatically
- **Merged remote-only cleanup**: positively merged branches that exist only on the
  selected remote are now recommended and marked for deletion by default. Stale or
  unmerged remote-only refs remain protected; deletion re-verifies the merge, uses an
  exact force-with-lease guard, and journals the tip for `undo`.
- TUI test coverage (pure marking-logic unit tests plus Textual `run_test` harness tests)

### Fixed
- Confirmation dialogs now use compact, action-labelled buttons with keyboard hints in
  the dialog frame instead of oversized Yes/No-style controls.
- The TUI header now shows the repository path next to the application name, making
  similarly named clones and worktrees easy to distinguish without using another row.
- PR cells now show a clickable `#number` for open, merged, and closed-unmerged
  pull requests instead of only showing an open-PR count.
- **TUI dialogs are legible again.** The confirmation dialog's Yes/No buttons stacked
  vertically (the button row was a `Container`, which lays out vertically) and the dialog
  frame was drawn in a dimmed background colour, so the dialog read as the whole screen
  rather than a panel. All three modals now share a `BaseModal` with a visible border,
  a border title, and a horizontal button row.
- Long delete confirmations no longer overflow: the message body scrolls instead of
  pushing the buttons off screen when many branches are marked.
- Branch names and git error text are no longer parsed as Rich markup in modals, so a
  branch such as `feature/[wip]` renders correctly instead of being swallowed as a tag.
- **Rebase-merged branches are now detected.** Merge detection was rewritten around three
  git-native checks — reachability (`merge-base --is-ancestor`), patch-equivalence
  (`git cherry`), and combined-diff for multi-commit squashes. The previous diff-only
  approach missed rebase-merges entirely (branches whose commits are in main under
  different SHAs). Also removed the merge-commit-message regex (redundant, and it
  interpolated branch names into a regex unescaped) and two redundant reachability checks.

### Changed
- **TUI confirmations now use one structured dialog system.** Delete and restore prompts
  share typed question, scope, selection, and warning sections instead of assembling
  dense blocks of prose. The review body scrolls independently while the safe-first
  action row stays visible.
- **TUI confirmation dialogs now focus the safe option, so Enter cancels.** Previously
  focus landed on the destructive button, so Enter confirmed. Confirming is now `y`, or
  Tab to the confirm button and press Enter, or click it. This changes the
  mark-all-then-Enter-Enter flow: the second Enter cancels (marks are kept, so `y`
  still deletes). The dialog shows the keys on its bottom border.
- Fuzzy squash-merge matches are now advisory only: a high-similarity (non-exact) patch
  match surfaces a "possible squash-merge - verify before deleting" note instead of
  marking the branch merged/deletable (prevents deleting unmerged work). Exact diff
  matches still count as merged.
- CI now runs the full pytest suite on Python 3.12-3.14 and runs formatting, linting,
  and mypy once on Python 3.14.
- Minimum supported Python raised to 3.12, allowing all locked dependencies to receive
  current security fixes.
- README clarified: branch/merge analysis works on any Git host; PR detection is GitHub-only

### Added (original release scope)
- Initial public release
- Interactive TUI mode using Textual framework
- CLI mode for scripting and automation
- Smart branch detection (merged, stale, active)
- GitHub integration for PR detection
- Git worktree support
- Branch caching for improved performance
- Parallel processing for large repositories
- Configurable protected branches and ignore patterns
- Multiple sorting options (by name, age, status)
- Dry-run mode for safe preview
- Force-mark feature for branches with uncommitted changes
- Detailed branch information display
- Color-coded status indicators
- Keyboard shortcuts for efficient navigation
- Auto-refresh functionality
- Support for custom main branch names
- Environment variable and config file support for GitHub tokens

### Features

#### Interactive TUI
- Beautiful terminal interface with keyboard shortcuts
- Real-time branch marking and selection
- Confirmation dialogs for safe deletion
- Status bar with repository statistics
- Info modal for detailed branch information
- Legend display for symbols and colors
- Dynamic sorting with visual feedback
- Loading indicators for async operations
- Worktree visualization in branch list

#### CLI Mode
- Non-interactive mode for automation
- Filter by status (all/merged/stale)
- Force deletion without confirmation
- Verbose and debug output modes
- Custom configuration file support

#### Safety Features
- Protected branch configuration
- Open PR detection and protection
- Uncommitted changes detection
- Confirmation prompts before deletion
- Dry-run preview mode
- Detailed error messages

#### Performance
- Branch caching with smart invalidation
- Parallel processing support
- Efficient merge detection strategies
- Bulk GitHub API requests
- Background data loading in TUI

## [0.1.0] - 2024-01-XX

### Added
- Initial development version
- Core branch management functionality
- Basic CLI interface
- GitHub integration
- Configuration system

---

## Release Guidelines

### Version Numbers

We follow [Semantic Versioning](https://semver.org/):
- **MAJOR** version for incompatible API changes
- **MINOR** version for new functionality in a backward compatible manner
- **PATCH** version for backward compatible bug fixes

### Categories

Changes should be grouped under one of these categories:
- **Added** - New features
- **Changed** - Changes in existing functionality
- **Deprecated** - Soon-to-be removed features
- **Removed** - Removed features
- **Fixed** - Bug fixes
- **Security** - Security vulnerability fixes
