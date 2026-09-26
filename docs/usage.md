# GBK usage and configuration

[← Back to the quick start](../README.md)

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
- `--remote` - Enable remote deletion for matching remotes and positively merged remote-only branches (default: all remote refs are kept)
- `--no-remote-branches` - Only analyze branches that exist locally (default: remote-only branches are analyzed and shown)
- `--force` - Delete without confirmation (legacy behavior: also implies `--delete`; use with caution!). It skips the prompt only: branches and worktrees with uncommitted changes are still skipped
- `--refresh` - Fetch/prune the selected remote, bypass cache, and refresh all data

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

### TUI views and cleanup scope

The TUI defaults to **Local**. Click a view button or press `v` to cycle:

- **Local:** local branches and their worktrees. Cleanup keeps remote refs.
- **Remote:** branches on the selected remote, without worktree rows.
- **All:** combined branch rows, with `local`, `remote`, or `both` in Location.

Switching views clears marks. Mark All applies only to the visible view. Viewing
remote branches does not enable remote deletion: use `d` in Remote or All to
verify and mark remote cleanup candidates. This does not open a dialog or delete
anything. Review the marks, then press Enter for the deletion confirmation.
Confirmations show the local and remote
deletion counts separately, including paired branches. The plain CLI and JSON
outputs retain their existing branch-discovery options.

Startup analyzes the selected view only. Remote-only branches load on demand
when you open Remote or All. Press `r` to refresh the current view: Local skips
remote fetch/prune and remote-only analysis, while Remote and All synchronize
the selected remote. Local branches still receive PR checks. An explicit
command-line `--refresh` retains its fetch/prune behavior before launching the TUI.

### Understanding Branch Status

| Status | Description | Safe to Delete |
|--------|-------------|----------------|
| `merged` | Changes are fully merged into main branch | ✅ Yes |
| `stale` | No commits in N days (default: 30) | ⚠️ Maybe |
| `active` | Recent commits, not yet merged | ❌ No |
| `unstarted` | Local history indicates no development on the branch | No automatic cleanup |
| `already in main` | Remote tip is reachable from main, but merge history is unclear | No automatic cleanup |

The separate **Cleanup** column explains why a row cannot be cleaned up. For
example, a merged remote branch can say “remote deletion is disabled”; a stale
remote branch can say “merge not confirmed.” Neither restriction replaces its
underlying status.

### Understanding Sync Status

| Status | Description |
|--------|-------------|
| `synced` | Local and remote at same commit |
| `ahead X` | Local has X commits not pushed |
| `behind X` | Remote has X commits not pulled |
| `diverged` | Local and remote have different commits |
| `local-only` | No remote branch exists |
| `remote-only` | Remote branch has no local counterpart; normal cleanup requires merge proof and remote deletion enabled; explicit TUI force-marking can override merge proof |
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
| **Remote deletion** | TUI `d` selection / CLI `--remote` | Off by default for all remote refs | ✅ **SAFE default** |
| **Merged remote-only cleanup** | Remote deletion enabled and merged row with Location `remote` | Blocked until explicit opt-in; then deletes after confirmation | ⚠️ **CAUTION** |

### ⚠️ Important Safety Warnings

1. **CLI Mode Is Read-Only by Default**: `--cli` / `--no-interactive` prints a plain report and does not prompt for cleanup. Add `--delete` when you want cleanup.

2. **Force Mode Skips All Confirmations**: The `--force` flag immediately deletes branches without asking when cleanup is enabled. For legacy compatibility, `--force` also implies `--delete`. Deletions are recorded in the deletion journal and can usually be restored with `git-branch-keeper undo` (see below), but don't rely on it — remote deletions affect collaborators immediately.

   **Deletion is local-only by default**: press `d` in Remote or All to verify and mark your merged remote cleanup candidates. GBK enables the verified scope and adds normal marks without opening a dialog or deleting anything; existing unrelated marks are preserved. Current checkouts and branches with uncommitted changes are not automatically marked. Review the selection, then press Enter for the single deletion confirmation. Eligibility requires a same-repository PR authored by your authenticated GitHub account, merged into the configured main branch, with no open PR and an unchanged head matching the remote-tracking tip. Missing ownership or API evidence keeps the remote. Local cleanup can still remove other eligible local branches while retaining their remotes. The final confirmation shows separate local and remote totals and each branch's deletion locations. Ownership, PR status and tips are checked again before remote deletion; a lease rejects a remote tip that changes. `d` again returns to local-only and clears remote-only marks. This selection is session-only; a refresh does not silently add new eligible remotes. Toggle off and press `d` again to select a new set. Undo restores local branches only.

   **Broader remote cleanup is explicit**: starting with `--remote` retains the broader **LOCAL + REMOTE** scope, including other authors' merged branches. In that explicit scope, `f` can force-mark stale, merged or unstarted remote-only branches without merge proof; protected branches, open PRs, remote access failures and changed tips still block deletion. Force-marking never bypasses ownership or merge requirements in **LOCAL + MY MERGED REMOTES**. Bulk marking and CLI `--force` continue to require merge proof for remote-only branches.

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

Every branch deletion is recorded in a journal at `~/.git-branch-keeper/deletions.jsonl`, including the branch's tip commit SHA. As long as the commit still exists in your repository (retention depends on reflogs and garbage-collection settings), you can restore it:

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
- ✅ Worktrees and branches with uncommitted changes (CLI `--force` included; only a per-row TUI force-mark overrides this)
- ✅ Remote-only branches unless positively confirmed merged (stale alone is not enough)

You can run GBK from inside a linked worktree. It protects whatever the *other*
worktrees have checked out - including the main working tree, which often holds a
feature branch rather than `main` - and only ever offers to remove linked
worktrees that are neither the main working tree nor the one you're standing in.

## ⚙️ Configuration

Create a configuration file to customize behavior. The tool looks for config files in this order:

1. Path specified with `--config` flag
2. `git-branch-keeper.json` in current directory
3. `.git-branch-keeper.json` in home directory

The first file found is loaded. Explicit CLI values override file values; `--protected`
and `--ignore` add entries to the configured lists. Invalid or missing explicitly
selected files stop startup. Saved settings cannot enable `--force` or remote deletion.

When GitHub protection is configured, API failures block deletion until verification
succeeds. For local branches with matching remotes, remote cleanup requires the live
remote tip to equal the local tip and Git to confirm it is merged. A differing tip
blocks the operation for review; pushes use a lease to preserve concurrent updates.

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
| `include_remote_branches` | boolean | Analyze and show remote-only branches; deletion still requires remote opt-in | `true` |

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

## 🎨 Examples

### Example 1: Weekly Cleanup
```bash
# Interactive review of all merged branches
git-branch-keeper --filter merged

# Mark branches in the TUI, choose the deletion scope with 'd', then press Enter
```

### Example 2: Automated Cleanup in CI/CD
```bash
# Delete eligible merged branches without confirmation
git-branch-keeper --cli --filter merged --delete --force
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
