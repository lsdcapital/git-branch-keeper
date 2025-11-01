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
- Always use `--dry-run` first to preview changes
- The `--cleanup` flag is deprecated (cleanup is now the default behavior in CLI mode)

## Architecture

The codebase follows a service-oriented architecture:

- **core.py**: Main BranchKeeper class that orchestrates all operations
- **services/**:
  - `git_service.py`: Handles Git operations (branch listing, deletion, merge detection)
  - `github_service.py`: GitHub API integration for PR status
  - `branch_status_service.py`: Determines branch status (merged, stale, has PR)
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
The GitService uses multiple methods to detect merged branches, ordered by speed:
1. Squash merge detection by patch comparison
2. Fast rev-list check
3. Ancestor check
4. Merge commit message search
5. Full commit history comparison

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
- Test framework: pytest with 102 tests (run with `make test`)