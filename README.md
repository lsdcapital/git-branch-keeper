# 🌿 git-branch-keeper

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A smart Git branch management tool that helps keep your repository clean and organized. Branch and merge analysis works on **any Git repository** — GitHub, GitLab, Bitbucket, or purely local. Pull-request detection and protection are an optional extra that currently work with **GitHub only**. Stop manually tracking which branches are safe to delete—let `git-branch-keeper` do the heavy lifting.

## ✨ Features

- 🖥️ **Interactive TUI** - Beautiful terminal interface for managing branches with keyboard shortcuts
- 📊 **Smart Detection** - Automatically identifies merged and stale branches
- 🔍 **Optional GitHub Integration** - Protects branches with open pull requests (GitHub only, requires a token)
- 🌍 **Host-agnostic core** - Branch analysis, merge detection, and cleanup work on any Git repo (GitHub, GitLab, Bitbucket, or local); PR detection is GitHub-only
- 🌳 **Worktree Support** - Handles git worktrees intelligently
- ⚡ **Fast & Efficient** - Caching and parallel processing for large repositories
- 🎨 **Rich Output** - Color-coded status with detailed information
- 🔒 **Safety First** - Protected branches, confirmation prompts, and dry-run mode
- 📝 **Flexible Filtering** - View all, merged, or stale branches
- 🔄 **Sync Awareness** - Shows ahead/behind status for remote tracking

## 📸 Screenshots

### Interactive TUI Mode
```
┌────────────────────────────────────────────────────────────────────────┐
│ Git Branch Keeper                                         v0.1.0       │
├────────────────────────────────────────────────────────────────────────┤
│   ✓  feature/old-feature    merged     2024-01-15    45    ✗  synced  │
│   ✗  feature/new-work       active     2024-03-20     2    ✓  ahead 3 │
│   ✓  bugfix/old-bug        merged     2023-12-10    90    ✓  synced  │
├────────────────────────────────────────────────────────────────────────┤
│ Total: 15 | Protected: 2 | Deletable: 8 | Marked: 2                   │
├────────────────────────────────────────────────────────────────────────┤
│ (q) Quit (d) Delete (space) Mark (a) Mark All (i) Info (r) Refresh   │
└────────────────────────────────────────────────────────────────────────┘
```

<!-- TODO: Add actual screenshots here once deployed -->

## 🚀 Installation

### Using pipx (Recommended)
```bash
pipx install git-branch-keeper
```

### Using pip
```bash
pip install git-branch-keeper
```

### From Source
```bash
git clone https://github.com/lsdcapital/git-branch-keeper.git
cd git-branch-keeper
uv sync --dev
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

## 🎯 Quick Start

### Interactive Mode (Default)
Launch the beautiful TUI to interactively manage your branches:

```bash
cd your-git-repo
git-branch-keeper
```

Use keyboard shortcuts to navigate and manage branches:
- `↑/↓` - Navigate branches
- `space` - Mark/unmark branch for deletion
- `a` - Mark all deletable branches
- `d` - Delete marked branches
- `i` - Show detailed branch info
- `r` - Refresh branch data
- `q` - Quit

### CLI Mode
For scripting and automation, use the non-interactive CLI mode:

```bash
# Preview what would be deleted (RECOMMENDED for first run)
git-branch-keeper --no-interactive --filter merged --dry-run

# View merged branches in interactive TUI (default, safest)
git-branch-keeper --filter merged

# Delete merged branches with confirmation prompts (deletes local only, keeps remote)
git-branch-keeper --no-interactive --filter merged

# Also delete the remote branch (affects collaborators)
git-branch-keeper --no-interactive --filter merged --remote

# Force delete without confirmation (DANGEROUS)
git-branch-keeper --no-interactive --filter merged --force
```

> **⚠️ Safety Note**: The CLI mode (`--no-interactive`) performs cleanup by default. Always use `--dry-run` first to preview changes, especially on your first run!

> **🌐 Remote branches**: By default, deletion is **local-only** — the remote branch is kept. Add `--remote` to also delete it on `origin`. Remote deletions affect collaborators and are harder to undo, so they are opt-in.

## 📖 Usage

### Command Line Options

```bash
git-branch-keeper [OPTIONS]
```

**Display Options:**
- `--filter {all,merged,stale}` - Filter branches by status (default: all)
- `--sort-by {name,age,status}` - Sort branches by field (default: age)
- `--sort-order {asc,desc}` - Sort order (default: desc)
- `--stale-days N` - Days before branch is stale (default: 30)

**Mode Options:**
- `--interactive` / `--no-interactive` - Enable/disable TUI mode
- `--dry-run` - Preview changes without deleting
- `--remote` - Also delete the remote branch (default: local-only, remote is kept)
- `--force` - Delete without confirmation (use with caution!)
- `--refresh` - Bypass cache and refresh all data

**Subcommands:**
- `undo [BRANCH]` - Restore a deleted branch from the journal (most recent if no name given)
- `undo --list` - List recent deletions for this repository

**Configuration:**
- `-c, --config PATH` - Path to config file
- `--main-branch NAME` - Override main branch name
- `--protected BRANCH` - Additional protected branches (repeatable)
- `--ignore PATTERN` - Branch patterns to ignore (repeatable)

**Other:**
- `--debug` - Enable debug logging
- `--version` - Show version information
- `-v, --verbose` - Verbose output

### Understanding Branch Status

| Status | Description | Safe to Delete |
|--------|-------------|----------------|
| `merged` | Changes are fully merged into main branch | ✅ Yes |
| `stale` | No commits in N days (default: 30) | ⚠️ Maybe |
| `active` | Recent commits, not yet merged | ❌ No |

### Understanding Sync Status

| Status | Description |
|--------|-------------|
| `synced` | Local and remote at same commit |
| `ahead X` | Local has X commits not pushed |
| `behind X` | Remote has X commits not pulled |
| `diverged` | Local and remote have different commits |
| `local-only` | No remote branch exists |
| `merged-git` | Detected as merged by git |
| `merged-pr` | Merged via GitHub pull request |

## 🔒 Safety & Best Practices

### Default Behavior

`git-branch-keeper` has **different default behaviors** depending on the mode:

| Mode | When It Activates | Default Behavior | Safety Level |
|------|-------------------|------------------|--------------|
| **Interactive TUI** | When connected to a terminal (default) | User selects branches, confirms before delete | ✅ **SAFE** |
| **CLI Mode** | `--no-interactive` flag | **Deletes branches with confirmation prompts** | ⚠️ **CAUTION** |
| **Force Mode** | `--force` flag | **Deletes immediately without confirmation** | 🔴 **DANGEROUS** |
| **Dry Run** | `--dry-run` flag | Preview only, no deletion | ✅ **SAFE** |
| **Remote deletion** | `--remote` flag | Off by default — deletion is local-only unless opted in | ✅ **SAFE default** |

### ⚠️ Important Safety Warnings

1. **CLI Mode Deletes by Default**: When using `--no-interactive`, the tool will delete branches (with confirmation). If you just want to preview, **always use `--dry-run`**.

2. **Force Mode Skips All Confirmations**: The `--force` flag immediately deletes branches without asking. Deletions are recorded in the deletion journal and can usually be restored with `git-branch-keeper undo` (see below), but don't rely on it — remote deletions affect collaborators immediately.

   **Deletion is local-only by default**: the remote branch is preserved unless you pass `--remote`. This keeps the easily-recoverable case (local, restorable via reflog and `undo`) separate from the harder-to-undo case (remote, visible to collaborators).

3. **First Run Recommendation**: On your first run, use `--dry-run` to understand what would be deleted:
   ```bash
   git-branch-keeper --no-interactive --filter merged --dry-run
   ```

4. **Protected Branches**: Always configure `protected_branches` in your config to prevent accidental deletion of important branches.

5. **GitHub Token Not Required**: The tool works without a GitHub token, but won't protect branches with open PRs if the token is missing.

### Safe Workflow

```bash
# Step 1: Preview changes (RECOMMENDED FIRST STEP)
git-branch-keeper --no-interactive --filter merged --dry-run

# Step 2: Review output carefully, then run actual cleanup
git-branch-keeper --no-interactive --filter merged

# Step 3: Or use interactive TUI for manual control (safest)
git-branch-keeper --filter merged
```

### Undo: Restoring Deleted Branches

Every branch deletion is recorded in a journal at `~/.git-branch-keeper/deletions.jsonl`, including the branch's tip commit SHA. As long as the commit still exists in your repository (git keeps unreachable objects for ~90 days by default), you can restore it:

```bash
# Restore the most recently deleted branch
git-branch-keeper undo

# Restore a specific branch by name
git-branch-keeper undo feature/my-branch

# List recent deletions for this repository
git-branch-keeper undo --list
```

Running `undo` repeatedly walks back through the deletion history, restoring one branch at a time. If the remote branch was also deleted, `undo` offers to push it back (or prints the `git push` command to do it manually).

### What Gets Protected

The tool automatically protects:
- ✅ Branches listed in `protected_branches` (default: `main`, `master`)
- ✅ Branches matching `ignore_patterns`
- ✅ Branches with open pull requests (if GitHub token configured)
- ✅ Current branch you're on
- ✅ Branches in active worktrees

## ⚙️ Configuration

Create a configuration file to customize behavior. The tool looks for config files in this order:

1. Path specified with `--config` flag
2. `git-branch-keeper.json` in current directory
3. `.git-branch-keeper.json` in home directory

### Example Configuration

```json
{
    "protected_branches": ["main", "master", "develop"],
    "ignore_patterns": [
        "release/*",
        "hotfix/*",
        "staging"
    ],
    "stale_days": 30,
    "github_token": "${GITHUB_TOKEN}"
}
```

### Configuration Options

| Option | Type | Description | Default |
|--------|------|-------------|---------|
| `protected_branches` | array | Branches never to delete | `["main", "master"]` |
| `ignore_patterns` | array | Glob patterns to ignore | `[]` |
| `stale_days` | integer | Days before branch is stale | `30` |
| `github_token` | string | GitHub personal access token | `null` |

### GitHub Token Setup (Optional)

**This section is OPTIONAL** - `git-branch-keeper` works on any Git repository without a GitHub token. The token only enables extra GitHub-specific features.

#### What works WITHOUT a GitHub token:
- ✅ Branch detection and analysis
- ✅ Merge status detection (via Git)
- ✅ Stale branch identification
- ✅ Local branch cleanup
- ✅ Works with GitHub, GitLab, Bitbucket, and local repos

#### What REQUIRES a GitHub token (GitHub repos only):
- 🔒 Pull request detection and protection
- 🔒 PR status and metadata display
- 🔒 Protection against deleting branches with open PRs

#### Setup Instructions (for GitHub repos):

1. **Create a token** at https://github.com/settings/tokens/new
   - Select scope: `repo` (for private repos) or `public_repo` (for public only)
   - Select scope: `read:org` (if using organization repos)

2. **Configure the token** (choose one):

   **Option A: Environment Variable (Recommended)**
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   ```

   **Option B: Config File**
   ```json
   {
       "github_token": "ghp_your_token_here"
   }
   ```

   ⚠️ **Security**: Never commit tokens to version control! Add config files to `.gitignore`.

### Pattern Matching

Ignore patterns support glob syntax:
- `feature/*` - All feature branches
- `release/v?.?.*` - Releases like v1.2.3
- `hotfix-*` - All hotfix branches
- `[!main]*` - Everything except main

## 🎨 Examples

### Example 1: Weekly Cleanup
```bash
# Interactive review of all merged branches
git-branch-keeper --filter merged

# Mark branches in TUI, press 'd' to delete
```

### Example 2: Automated Cleanup in CI/CD
```bash
# Delete all merged branches older than 60 days (no confirmation)
git-branch-keeper --no-interactive --filter merged --stale-days 60 --force
```

### Example 3: Safe Exploration
```bash
# See what would be deleted without making changes
git-branch-keeper --filter merged --dry-run
```

### Example 4: Custom Main Branch
```bash
# For repos using 'develop' as main branch
git-branch-keeper --main-branch develop --filter merged
```

### Example 5: Stale Branch Review
```bash
# Find branches inactive for 90+ days
git-branch-keeper --filter stale --stale-days 90
```

## 🏗️ Architecture

Built with modern Python tools:
- **GitPython** - Git repository operations
- **Textual** - Interactive terminal UI
- **Rich** - Beautiful terminal output
- **PyGithub** - GitHub API integration

The project follows a service-oriented architecture:
- `core.py` - Main orchestration
- `services/` - Git, GitHub, caching, and display services
- `models/` - Data models for branches and status
- `tui.py` - Interactive terminal interface

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines on:

- Development setup and prerequisites
- Coding standards and style guidelines
- Testing procedures
- Pull request process
- How to report bugs and suggest features

Quick summary:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes following our code style (Black, Ruff, MyPy)
4. Submit a pull request

For bug reports and feature requests, please [open an issue](https://github.com/lsdcapital/git-branch-keeper/issues).

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with [Textual](https://github.com/Textualize/textual) - Amazing TUI framework
- Inspired by the need to keep Git repositories clean and maintainable
- Thanks to all contributors!

## 💬 Support

- **Bug reports & feature requests**: [Open an issue](https://github.com/lsdcapital/git-branch-keeper/issues)
- **Contributing guidelines**: See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Questions & discussions**: Start a [discussion](https://github.com/lsdcapital/git-branch-keeper/discussions)

## 📚 Related Projects

- [git-extras](https://github.com/tj/git-extras) - Git utilities collection
- [git-trim](https://github.com/foriequal0/git-trim) - Automatic branch cleanup
- [git-gone](https://github.com/lunaryorn/git-gone) - Remove merged branches

---

<div align="center">
Made with ❤️ by <a href="https://github.com/lsdcapital">Stefan Lesicnik</a>
<br>
<sub>Star ⭐ this repo if you find it useful!</sub>
</div>
