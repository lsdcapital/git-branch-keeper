# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Deletion journal & `undo`**: every deleted branch is recorded with its tip SHA in
  `~/.git-branch-keeper/deletions.jsonl`; `git-branch-keeper undo [BRANCH]` restores it
  (and `undo --list` shows recent deletions)
- **Opt-in remote deletion**: deletion is now local-only by default; pass `--remote` to
  also delete the branch on the remote
- **Remote auto-detection**: the remote is no longer hardcoded to `origin` — a single
  non-`origin` remote (e.g. `upstream`) is detected and used automatically
- TUI test coverage (pure marking-logic unit tests plus Textual `run_test` harness tests)

### Fixed
- **Rebase-merged branches are now detected.** Merge detection was rewritten around three
  git-native checks — reachability (`merge-base --is-ancestor`), patch-equivalence
  (`git cherry`), and combined-diff for multi-commit squashes. The previous diff-only
  approach missed rebase-merges entirely (branches whose commits are in main under
  different SHAs). Also removed the merge-commit-message regex (redundant, and it
  interpolated branch names into a regex unescaped) and two redundant reachability checks.

### Changed
- Fuzzy squash-merge matches are now advisory only: a high-similarity (non-exact) patch
  match surfaces a "possible squash-merge - verify before deleting" note instead of
  marking the branch merged/deletable (prevents deleting unmerged work). Exact diff
  matches still count as merged.
- CI now runs the full pytest suite and enforces mypy; test matrix is Python 3.9-3.13
- Minimum supported Python raised to 3.9 (was 3.8; 3.8 was already broken at import)
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
