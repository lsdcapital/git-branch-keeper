---
name: Bug Report
about: Report a bug or issue with git-branch-keeper
title: '[BUG] '
labels: bug
assignees: ''
---

## Bug Description

A clear and concise description of what the bug is.

## Steps to Reproduce

1. Run command `git-branch-keeper ...`
2. Select/click on '...'
3. See error

## Expected Behavior

A clear and concise description of what you expected to happen.

## Actual Behavior

A clear and concise description of what actually happened.

## Error Messages

```
Paste any error messages or stack traces here
```

## Screenshots

If applicable, add screenshots or terminal output to help explain your problem.

## Environment

- **OS:** [e.g. macOS 14.0, Ubuntu 22.04, Windows 11]
- **Python version:** [e.g. 3.11.5]
- **git-branch-keeper version:** [run `git-branch-keeper --version`]
- **Git version:** [run `git --version`]
- **Installation method:** [e.g. pipx, pip, from source]

## Repository Information (if relevant)

- Number of branches: [approximate count]
- Using worktrees: [yes/no]
- GitHub integration: [enabled/disabled]
- Repository type: [public/private/organization]

## Configuration

If relevant, share your configuration (with sensitive data removed):

```json
{
  "protected_branches": ["main"],
  "stale_days": 30,
  ...
}
```

## Additional Context

Add any other context about the problem here.

## Debug Output

If possible, run with `--debug` flag and paste relevant output:

```
git-branch-keeper --debug ...
```
