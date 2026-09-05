# 🌿 Git Branch Keeper (GBK)

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

**Review and clean up Git branches from your terminal.** GBK identifies merged and stale branches, shows their local/remote status, and lets you review cleanup in an interactive terminal interface or a plain CLI report.

Works with any Git repository, including GitHub, GitLab, Bitbucket, and local repositories. Optional GitHub authentication adds pull-request status and protection for branches with open PRs.

## Install and try it

Requires **Git**, **Python 3.12+**, and [pipx](https://pipx.pypa.io/stable/installation/).

```bash
pipx install 'git+https://github.com/lsdcapital/git-branch-keeper.git'
cd your-git-repo
gbk
```

Install directly from GitHub; GBK is not currently published on PyPI. The commands `gbk`, `git gbk`, and `git-branch-keeper` all launch the same tool.

Navigate with **↑/↓**, use **Space** to mark or unmark a branch, then **Enter** to review the deletion confirmation. Press **q** to quit. Remote deletion is off by default.

For a read-only first look:

```bash
gbk --cli --filter merged
```

![GBK terminal interface showing active, merged, and stale branches with local-only deletion selected](docs/images/terminal.svg)

*The actual GBK interface, rendered with sample branch data.*

## What it helps with

- **Find cleanup candidates:** Detect merged changes, including squash and rebase workflows, and identify inactive branches.
- **Review before deleting:** See branch status, inspect details, and preview cleanup with a dry run.
- **Manage worktrees:** Show worktree state and protect the worktree you are running in.
- **Keep shared branches safe:** Remote deletion requires explicit opt-in; protected branches and open GitHub PRs receive additional checks.
- **Recover a deletion:** Use the deletion journal to restore branches while their commits are still available.

## Common commands

```bash
# Review merged branches interactively
gbk --filter merged

# Preview cleanup without deleting anything
gbk --cli --filter merged --dry-run

# Delete eligible local branches after confirmation
gbk --cli --filter merged --delete

# Review branches inactive for at least 90 days
gbk --filter stale --stale-days 90

# Restore the most recently deleted branch
gbk undo
```

Plain CLI mode is read-only unless cleanup is requested. A stale branch may contain unmerged work. Deleting remote branches requires `--remote` in the CLI or the **d** scope toggle in the TUI. The `--force` option skips confirmations and can override some protections; see the [safety guide](docs/usage.md) before using it.

## Terminal shortcuts

| Key | Action |
| --- | --- |
| ↑ / ↓ | Navigate branches |
| Space | Mark or unmark a branch |
| a | Mark all deletable branches |
| Enter | Review deletion of marked branches |
| d | Toggle local-only / local + remote deletion |
| i | Show branch details |
| r | Fetch/prune the selected remote and refresh |
| q | Quit |

## Optional GitHub integration

GBK works without GitHub authentication. To enable GitHub PR detection and protection, use an authenticated GitHub CLI session or configure `GITHUB_TOKEN` / `GH_TOKEN`. See [authentication and configuration](docs/usage.md) for setup details.

## More documentation

- [Full command reference, configuration, safety, and undo](docs/usage.md)
- [Contributing and development setup](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Report a bug or request a feature](https://github.com/lsdcapital/git-branch-keeper/issues)

## Install from source for development

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```bash
git clone https://github.com/lsdcapital/git-branch-keeper.git
cd git-branch-keeper
uv sync --dev
uv run gbk --help
```

Built with GitPython, Textual, Rich, and PyGithub. Licensed under the [MIT License](LICENSE).
