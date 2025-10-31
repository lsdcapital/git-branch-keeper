# Contributing to git-branch-keeper

Thank you for your interest in contributing to git-branch-keeper! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Features](#suggesting-features)

## Code of Conduct

This project and everyone participating in it is governed by our commitment to providing a welcoming and inclusive environment. Please be respectful and constructive in all interactions.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally
3. Set up the development environment
4. Create a new branch for your changes
5. Make your changes
6. Test your changes
7. Submit a pull request

## Development Setup

### Prerequisites

- Python 3.8 or higher
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Git
- A GitHub account

### Setting Up Your Environment

1. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/git-branch-keeper.git
   cd git-branch-keeper
   ```

2. Install dependencies with uv:
   ```bash
   uv sync --dev
   ```

3. Activate the virtual environment:
   ```bash
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

4. Verify the installation:
   ```bash
   uv run git-branch-keeper --version
   ```

### Running the Tool Locally

```bash
# Run with debug output
uv run git-branch-keeper --debug

# Run in interactive TUI mode
uv run git-branch-keeper

# Run in CLI mode
uv run git-branch-keeper --no-interactive
```

## How to Contribute

### Types of Contributions

We welcome several types of contributions:

- **Bug fixes** - Fix issues reported by users or found during testing
- **New features** - Implement new functionality
- **Documentation** - Improve or expand documentation
- **Tests** - Add or improve test coverage
- **Performance** - Optimize existing code
- **Refactoring** - Improve code structure without changing behavior

### Finding Issues to Work On

- Look for issues labeled `good first issue` for beginner-friendly tasks
- Issues labeled `help wanted` are open for contribution
- Feel free to open new issues for bugs or feature requests

## Coding Standards

### Code Style

This project follows these style guidelines:

- **Black** for code formatting (line length: 100)
- **Ruff** for linting
- **MyPy** for type checking

Run formatters and linters before committing:

```bash
# Format code
black .

# Lint code
ruff check .

# Type check
mypy git_branch_keeper
```

### Code Guidelines

1. **Type Hints**: Use type hints for all function parameters and return values
   ```python
   def process_branch(branch_name: str, force: bool = False) -> BranchDetails:
       ...
   ```

2. **Docstrings**: Use docstrings for modules, classes, and functions
   ```python
   def get_branch_status(branch: str) -> BranchStatus:
       """Get the status of a branch.

       Args:
           branch: Name of the branch to check

       Returns:
           BranchStatus indicating if merged, stale, or active
       """
       ...
   ```

3. **Error Handling**: Use specific exceptions and handle errors gracefully
   ```python
   try:
       result = git_operation()
   except GitCommandError as e:
       logger.error(f"Git operation failed: {e}")
       raise RuntimeError(f"Failed to complete operation: {e}")
   ```

4. **Logging**: Use the logger instead of print statements
   ```python
   from .logging_config import get_logger

   logger = get_logger(__name__)
   logger.debug("Detailed debug information")
   logger.info("General information")
   logger.warning("Warning message")
   logger.error("Error message")
   ```

### Project Structure

```
git-branch-keeper/
├── git_branch_keeper/
│   ├── __init__.py
│   ├── __version__.py
│   ├── cli.py              # Command-line interface
│   ├── core.py             # Main BranchKeeper class
│   ├── tui.py              # Textual TUI interface
│   ├── args.py             # Argument parsing
│   ├── config.py           # Configuration management
│   ├── constants.py        # Constants and configuration
│   ├── formatters.py       # Output formatting utilities
│   ├── logging_config.py   # Logging setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── branch.py       # Branch data models
│   │   └── worktree.py     # Worktree models
│   └── services/
│       ├── __init__.py
│       ├── git_service.py              # Git operations
│       ├── github_service.py           # GitHub API integration
│       ├── branch_status_service.py    # Branch status logic
│       ├── branch_validation_service.py # Validation rules
│       ├── cache_service.py            # Caching logic
│       └── display_service.py          # Terminal output
├── tests/                  # Test files (to be added)
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── pyproject.toml
└── git-branch-keeper.example.json
```

## Testing

### Running Tests

While the test suite is still being developed, manual testing is crucial:

1. Test in a sample repository:
   ```bash
   cd /path/to/test/repo
   uv run git-branch-keeper --debug
   ```

2. Test different scenarios:
   - Repository with many branches
   - Repository with worktrees
   - Repository with open PRs
   - Repository without GitHub token
   - Dry-run mode
   - Force mode

3. Test both TUI and CLI modes

### Writing Tests

When the test suite is added, please include tests for:
- New features
- Bug fixes
- Edge cases
- Error handling

## Pull Request Process

### Before Submitting

1. Ensure your code follows the coding standards
2. Run formatters and linters:
   ```bash
   black .
   ruff check .
   mypy git_branch_keeper
   ```
3. Test your changes manually
4. Update documentation if needed
5. Update CHANGELOG.md with your changes

### Commit Messages

Write clear, descriptive commit messages:

```
Add support for custom sort orders in TUI

- Implement cycle_sort action with configurable order
- Update status bar to show current sort settings
- Add keyboard shortcut 's' for changing sort
```

Follow this format:
- First line: Brief summary (50 chars or less)
- Blank line
- Detailed description if needed

### Submitting Your PR

1. Push your changes to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

2. Open a pull request on GitHub

3. Fill out the PR template with:
   - Description of changes
   - Related issue number (if applicable)
   - Screenshots (for UI changes)
   - Testing performed

4. Wait for review and address feedback

### PR Review Process

- Maintainers will review your PR within a few days
- Be responsive to feedback and questions
- Make requested changes in new commits
- Once approved, a maintainer will merge your PR

## Reporting Bugs

### Before Reporting

- Check if the issue already exists
- Try with the latest version
- Test with `--debug` flag for detailed output

### Bug Report Template

```markdown
**Describe the bug**
A clear description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Run command '...'
2. Select '....'
3. See error

**Expected behavior**
What you expected to happen.

**Actual behavior**
What actually happened.

**Screenshots**
If applicable, add screenshots or terminal output.

**Environment:**
- OS: [e.g. macOS 14.0, Ubuntu 22.04]
- Python version: [e.g. 3.11.5]
- git-branch-keeper version: [e.g. 0.1.0]
- Git version: [e.g. 2.42.0]

**Additional context**
Any other relevant information.
```

## Suggesting Features

We welcome feature suggestions! When proposing a feature:

1. Check if it's already been suggested
2. Describe the problem it solves
3. Provide examples of how it would work
4. Consider implementation complexity

### Feature Request Template

```markdown
**Is your feature request related to a problem?**
A clear description of what the problem is.

**Describe the solution you'd like**
A clear description of what you want to happen.

**Describe alternatives you've considered**
Other solutions you've thought about.

**Additional context**
Any other context, screenshots, or examples.
```

## Development Tips

### Debugging

Use debug mode for detailed logging:
```bash
uv run git-branch-keeper --debug
```

### Testing GitHub Integration

Set up a test token with minimal permissions:
```bash
export GITHUB_TOKEN="your_test_token"
```

### Testing with Different Repositories

Test with repositories that have:
- Different numbers of branches (few, many, hundreds)
- Different branch naming conventions
- Worktrees
- Open PRs
- Stale branches
- Merged branches

## Questions?

If you have questions about contributing:
- Open an issue on GitHub
- Start a discussion
- Review existing issues and PRs

Thank you for contributing to git-branch-keeper!
