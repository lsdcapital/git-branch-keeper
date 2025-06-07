# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

git-branch-keeper is a Git branch management tool that helps developers identify and clean up merged and stale branches while protecting branches with open pull requests. It uses GitPython for repository operations and PyGithub for GitHub API integration.

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
# Show branch status
git-branch-keeper --filter all

# Clean up merged branches (interactive)
git-branch-keeper --filter merged --cleanup

# Clean up with force (no confirmation)
git-branch-keeper --filter merged --cleanup --force

# Debug mode for troubleshooting
git-branch-keeper --debug
```

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
- GitHub integration is optional but enhances functionality by detecting open PRs
- No test framework is currently set up in the project