# 🌿 git-branch-keeper

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A smart Git branch management tool that helps keep your repository clean and organized. Branch and merge analysis works on **any Git repository** — GitHub, GitLab, Bitbucket, or purely local. Pull-request detection and protection are an optional extra that currently work with **GitHub only**. Stop manually tracking which branches are safe to delete—let `git-branch-keeper` do the heavy lifting.

## ✨ Features

- 🖥️ **Interactive TUI** - Beautiful terminal interface for managing branches with keyboard shortcuts
- 📊 **Smart Detection** - Automatically identifies merged and stale branches
- 🔍 **Optional GitHub Integration** - Protects branches with open pull requests (GitHub only; uses `github_token`, `GITHUB_TOKEN`, `GH_TOKEN`, or an authenticated `gh` CLI)
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
│ Delete scope: LOCAL ONLY — remotes kept [d] | Total: 15 | Marked: 2   │
├────────────────────────────────────────────────────────────────────────┤
│ (q) Quit (d) Delete Scope (space) Mark (a) Mark All (i) Info         │
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

After installation, the canonical command is `git-branch-keeper`. Short aliases are also installed:

```bash
gbk        # same as git-branch-keeper
git gbk    # Git subcommand alias, via git-gbk
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
- `d` - Toggle deletion scope between local-only and local + remote
- `Enter` - Review and delete marked branches
- `i` - Show detailed branch info
- `r` - Refresh branch data
- `q` - Quit

### CLI Mode
For scripting and automation, use the non-interactive CLI mode:

```bash
# View merged branches in interactive TUI (default, safest)
git-branch-keeper --filter merged

# Plain CLI report (read-only)
git-branch-keeper --cli --filter merged

# Preview what would be deleted (RECOMMENDED before cleanup)
git-branch-keeper --cli --filter merged --dry-run

# Delete merged branches with confirmation prompts (deletes local only, keeps remote)
git-branch-keeper --cli --filter merged --delete

# Also delete the remote branch (affects collaborators)
git-branch-keeper --cli --filter merged --delete --remote

# Force delete without confirmation (DANGEROUS)
git-branch-keeper --cli --filter merged --delete --force
```

> **⚠️ Safety Note**: Plain CLI mode (`--cli` / `--no-interactive`) is read-only by default. Pass `--delete` to clean up branches, or `--dry-run` to preview cleanup candidates.

> **🌐 Remote branches**: By default, deletion is **local-only** — the remote branch is kept. In the TUI, the current scope is always shown in the status bar; press `d` to switch between **LOCAL ONLY** and **LOCAL + REMOTE** before confirming. In CLI mode, add `--remote` to also delete it on `origin`. Remote deletions affect collaborators and are harder to undo, so they are opt-in.

> **☁️ Remote-only branches**: Branches that exist on `origin` with no local counterpart are **analyzed and shown by default**, because branch accumulation is mostly a remote problem. They are strictly read-only — git-branch-keeper never deletes a remote-only branch. Pass `--no-remote-branches` for the local-only view. Remote-tracking refs are only as fresh as your last `git fetch`.

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
- `--interactive` - Launch the TUI (default for TTY)
- `--cli` / `--no-interactive` - Use plain CLI output (read-only unless cleanup is requested)
- `--delete` - Cleanup/delete eligible branches in CLI mode (prompts unless `--force` is used)
- `--cleanup` - Deprecated alias for `--delete`
- `--dry-run` - Preview cleanup candidates without deleting or prompting
- `--remote` - Also delete the remote branch (default: local-only, remote is kept)
- `--no-remote-branches` - Only analyze branches that exist locally (default: remote-only branches are analyzed too, read-only)
- `--force` - Delete without confirmation (legacy behavior: also implies `--delete`; use with caution!)
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
| **CLI Report** | `--cli` / `--no-interactive` | Plain branch report, no deletion prompt | ✅ **SAFE** |
| **CLI Cleanup** | `--cli --delete` | Deletes eligible branches with confirmation prompts | ⚠️ **CAUTION** |
| **Force Mode** | `--cli --delete --force` | Deletes immediately without confirmation | 🔴 **DANGEROUS** |
| **Dry Run** | `--dry-run` flag | Preview only, no deletion or prompts | ✅ **SAFE** |
| **Remote deletion** | TUI `d` toggle / CLI `--remote` | Off by default — deletion is local-only unless opted in | ✅ **SAFE default** |

### ⚠️ Important Safety Warnings

1. **CLI Mode Is Read-Only by Default**: `--cli` / `--no-interactive` prints a plain report and does not prompt for cleanup. Add `--delete` when you want cleanup.

2. **Force Mode Skips All Confirmations**: The `--force` flag immediately deletes branches without asking when cleanup is enabled. For legacy compatibility, `--force` also implies `--delete`. Deletions are recorded in the deletion journal and can usually be restored with `git-branch-keeper undo` (see below), but don't rely on it — remote deletions affect collaborators immediately.

   **Deletion is local-only by default**: the remote branch is preserved unless you toggle the TUI deletion scope with `d` or pass `--remote` in CLI mode. This keeps the easily-recoverable case (local, restorable via reflog and `undo`) separate from the harder-to-undo case (remote, visible to collaborators).

3. **First Run Recommendation**: On your first run, use `--dry-run` to understand what would be deleted:
   ```bash
   git-branch-keeper --cli --filter merged --dry-run
   ```

4. **Protected Branches**: Always configure `protected_branches` in your config to prevent accidental deletion of important branches.

5. **GitHub Auth Not Required**: The tool works without GitHub auth, but won't protect branches with open PRs if no token or authenticated `gh` CLI is available.

### Safe Workflow

```bash
# Step 1: Review a read-only CLI report
git-branch-keeper --cli --filter merged

# Step 2: Preview cleanup candidates (RECOMMENDED BEFORE DELETE)
git-branch-keeper --cli --filter merged --dry-run

# Step 3: Review output carefully, then run actual cleanup
git-branch-keeper --cli --filter merged --delete

# Step 4: Or use interactive TUI for manual control (safest)
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
- ✅ Branches with open pull requests (if GitHub auth is available)
- ✅ Current branch you're on
- ✅ Branches checked out in any other worktree, including the main working tree
- ✅ The worktree GBK is running in (never removed, even with `--force`)
- ✅ Branches that exist only on the remote (analyzed and reported, never deleted)

You can run GBK from inside a linked worktree. It protects whatever the *other*
worktrees have checked out - including the main working tree, which often holds a
feature branch rather than `main` - and only ever offers to remove linked
worktrees that are neither the main working tree nor the one you're standing in.

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
| `github_token` | string | GitHub personal access token. If omitted, GBK also tries `GITHUB_TOKEN`, `GH_TOKEN`, then `gh auth token` | `null` |
| `squash_scan_limit` | integer | First-parent commits on main to scan for exact squash patch-id matches | `500` |
| `include_remote_branches` | boolean | Analyze branches that exist only on the remote (read-only; they are never deleted) | `true` |

### GitHub Auth Setup (Optional)

**This section is OPTIONAL** - `git-branch-keeper` works on any Git repository without GitHub auth. GitHub auth only enables extra GitHub-specific PR features.

#### What works WITHOUT GitHub auth:
- ✅ Branch detection and analysis
- ✅ Merge status detection (via Git)
- ✅ Stale branch identification
- ✅ Local branch cleanup
- ✅ Works with GitHub, GitLab, Bitbucket, and local repos

#### What REQUIRES GitHub auth (GitHub repos only):
- 🔒 Pull request detection and protection
- 🔒 PR status and metadata display
- 🔒 Protection against deleting branches with open PRs

#### Setup Instructions (for GitHub repos):

Choose one auth source. GBK tries them in this order: config `github_token`, `GITHUB_TOKEN`, `GH_TOKEN`, then `gh auth token`.

1. **Use the GitHub CLI**
   ```bash
   gh auth login
   gh auth status
   ```

2. **Or create a token** at https://github.com/settings/tokens/new
   - Select scope: `repo` (for private repos) or `public_repo` (for public only)
   - Select scope: `read:org` (if using organization repos)

3. **Configure the token** (choose one):

   **Option A: Environment Variable**
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   # or
   export GH_TOKEN="ghp_your_token_here"
   ```

   **Option B: Config File**
   ```json
   {
       "github_token": "ghp_your_token_here"
   }
   ```

   ⚠️ **Security**: Never commit tokens to version control! Add config files to `.gitignore`.

### How Branch Analysis Works

CLI, TUI, and JSON output all use the same analysis path. Each branch is processed as one work item, so the single `Processing branches (...)` progress bar includes GitHub PR lookup (when enabled), local Git merge checks, dirty/worktree checks, and status calculation. When GitHub integration is enabled, GBK caps branch workers below PyGithub's default API connection pool size to avoid urllib3 connection-pool warnings.

For each branch, GBK uses this order:

1. **GitHub PR metadata** (only when GitHub auth is available):
   - open PRs keep the branch active/protected;
   - a merged PR marks the branch `merged-pr` only when the local branch tip still matches the PR head SHA;
   - if the PR is merged but the local tip differs, GBK shows a note and falls through to local Git checks.
2. **Local Git merge detection**:
   - reachability (`merge-base --is-ancestor`) for merge commits/fast-forwards;
   - patch-equivalence (`git cherry`) for rebases/cherry-picks/single-commit squashes;
   - exact combined patch-id scan for multi-commit squash merges, capped by `squash_scan_limit`.
3. **Stale/active classification** based on branch age when no merge proof is found.
4. **Safety checks** such as protected branches, current branch, worktrees, open PRs, and uncommitted file state determine whether a branch is deletable.

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

# Mark branches in the TUI, choose the deletion scope with 'd', then press Enter
```

### Example 2: Automated Cleanup in CI/CD
```bash
# Delete all merged branches older than 60 days (no confirmation)
git-branch-keeper --cli --filter merged --stale-days 60 --delete --force
```

### Example 3: Safe Exploration
```bash
# See what would be deleted without making changes
git-branch-keeper --cli --filter merged --dry-run
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
