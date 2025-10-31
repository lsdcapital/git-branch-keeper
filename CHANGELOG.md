# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
