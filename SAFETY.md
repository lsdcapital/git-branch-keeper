# Safety Guide for git-branch-keeper

This document provides comprehensive safety information for using `git-branch-keeper`, including known risks, best practices, and recovery procedures.

## 🔒 Safety Philosophy

`git-branch-keeper` is designed with safety in mind, but **branch deletion is irreversible**. This guide helps you understand the risks and use the tool safely.

## 🎯 Quick Safety Checklist

Before using `git-branch-keeper` for the first time:

- [ ] Read this entire safety guide
- [ ] Run with `--dry-run` first to preview changes
- [ ] Configure `protected_branches` in your config file
- [ ] Understand the difference between TUI and CLI modes
- [ ] Never use `--force` unless you're absolutely certain
- [ ] Set up GitHub token (optional, but adds PR protection for GitHub repos)

## 🚦 Safety Levels by Mode

### ✅ SAFE Modes

**1. Interactive TUI (Default)**
```bash
git-branch-keeper
git-branch-keeper --filter merged
```
- **Behavior**: Opens interactive interface, you manually select branches to delete
- **Confirmations**: Yes, you choose what to delete
- **Recommended for**: Day-to-day use, exploring branch status

**2. Dry Run Mode**
```bash
git-branch-keeper --no-interactive --filter merged --dry-run
```
- **Behavior**: Shows what WOULD be deleted, makes NO changes
- **Confirmations**: N/A (read-only)
- **Recommended for**: First run, testing filters, verifying configuration

### ⚠️ CAUTION Modes

**3. CLI Mode (Non-Interactive)**
```bash
git-branch-keeper --no-interactive --filter merged
```
- **Behavior**: **DELETES branches** matching filter, with confirmation prompts
- **Confirmations**: Yes, but requires attention
- **Recommended for**: Scripting after testing with dry-run first
- **Risk**: Easy to miss confirmation prompts in automated scripts

### 🔴 DANGEROUS Modes

**4. Force Mode**
```bash
git-branch-keeper --no-interactive --filter merged --force
```
- **Behavior**: **IMMEDIATELY DELETES** branches without ANY confirmation
- **Confirmations**: NONE
- **Recommended for**: Never use unless you're 100% certain
- **Risk**: Irreversible deletion, no second chances

## ⚠️ Known Risks

Based on the project's TODO.md, here are documented data loss risks:

### 🔴 Critical Risks

1. **Local branch deleted before checking remote protection**
   - **Risk**: Branch deleted locally before confirming remote protection status
   - **Mitigation**: Tool checks remote protection before deletion
   - **Your action**: Verify `protected_branches` config

2. **Merge detection false positives**
   - **Risk**: Non-merged branch incorrectly identified as merged
   - **Mitigation**: Tool uses multiple merge detection strategies
   - **Your action**: Always preview with `--dry-run` first

3. **Branch checkout without proper isolation**
   - **Risk**: Status checks might affect working directory
   - **Mitigation**: Tool uses git worktrees for isolated checks
   - **Your action**: Commit or stash changes before running

### 🟠 High Risks

4. **Default cleanup mode with no confirmation**
   - **Risk**: CLI mode deletes by default (though with confirmation)
   - **Mitigation**: Added prominent warnings in documentation
   - **Your action**: Always use `--dry-run` on first run

5. **Force mode bypasses uncommitted changes check**
   - **Risk**: `--force` skips safety checks for speed
   - **Mitigation**: Documented as dangerous
   - **Your action**: Never use `--force` in scripts

6. **Stale branch detection could catch temporarily inactive feature branches**
   - **Risk**: Long-running feature branches marked as stale
   - **Mitigation**: Configurable `stale_days` threshold (default: 30)
   - **Your action**: Adjust `stale_days` or use `ignore_patterns`

7. **Main branch detection could fail if named unconventionally**
   - **Risk**: Main branch not properly detected
   - **Mitigation**: Configurable `main_branch` setting
   - **Your action**: Set `main_branch` in config if not "main" or "master"

### 🟡 Medium Risks

8. **PR data might be stale if cache not refreshed**
   - **Risk**: Open PRs not detected if cache is old
   - **Mitigation**: Use `--refresh` flag to bypass cache
   - **Your action**: Refresh before critical cleanup: `--refresh`

9. **Sync status could be wrong if remote changed since last fetch**
   - **Risk**: Remote changes not reflected in sync status
   - **Mitigation**: Tool shows last known status
   - **Your action**: Run `git fetch` before running tool

10. **Pattern matching could accidentally include/exclude branches**
    - **Risk**: Glob patterns might match unintended branches
    - **Mitigation**: Test patterns with `--dry-run`
    - **Your action**: Verify `ignore_patterns` with dry run

## 🛡️ Built-in Protections

The tool automatically protects:

1. **Protected branches**: Branches in `protected_branches` config (default: `main`, `master`)
2. **Current branch**: The branch you're currently on
3. **Branches with open PRs**: If GitHub token is configured (GitHub repos only)
4. **Active worktrees**: Branches checked out in git worktrees
5. **Ignored patterns**: Branches matching `ignore_patterns` glob patterns

## 📋 Recommended Workflows

### First-Time Use

```bash
# Step 1: Preview what exists
git-branch-keeper --filter all

# Step 2: Preview what would be deleted
git-branch-keeper --no-interactive --filter merged --dry-run

# Step 3: Review output carefully, then decide:
#   Option A: Use interactive mode (safest)
git-branch-keeper --filter merged

#   Option B: Use CLI mode with confirmation
git-branch-keeper --no-interactive --filter merged

#   Option C: Never use force mode on first run!
```

### Regular Maintenance

```bash
# Weekly cleanup: Use interactive TUI
git-branch-keeper

# Monthly cleanup: Preview first, then clean
git-branch-keeper --filter merged --dry-run
git-branch-keeper --filter merged
```

### Automated Scripts

```bash
#!/bin/bash
# ALWAYS include dry-run in scripts for verification
git fetch --all  # Update remote info
git-branch-keeper --no-interactive --filter merged --dry-run

# Then manually review and run:
# git-branch-keeper --no-interactive --filter merged
```

**⚠️ NEVER use `--force` in automated scripts!**

## 🔧 Configuration Best Practices

### Essential Configuration

Create `git-branch-keeper.json` in your project root:

```json
{
  "protected_branches": ["main", "master", "develop", "staging", "production"],
  "ignore_patterns": [
    "release/*",
    "hotfix/*",
    "dependabot/*"
  ],
  "stale_days": 60,
  "github_token": null
}
```

### Explanation

- **protected_branches**: Critical branches that should NEVER be deleted
- **ignore_patterns**: Glob patterns for branches to skip (releases, automated PRs, etc.)
- **stale_days**: Be generous with this value (30-90 days recommended)
- **github_token**: Set via environment variable `GITHUB_TOKEN` (don't commit to git!)

## 🚨 Emergency: Branch Deleted by Mistake

### If you JUST deleted a branch:

1. **Find the commit SHA immediately**:
   ```bash
   git reflog
   ```
   Look for the last commit on the deleted branch

2. **Recreate the branch**:
   ```bash
   git branch <branch-name> <commit-sha>
   ```

3. **Push to remote if it was there**:
   ```bash
   git push origin <branch-name>
   ```

### If it's been a while:

- Git keeps deleted branch refs for ~30 days in `reflog`
- After that, commits may be garbage collected
- **This is why dry-run is so important!**

## 📞 When Things Go Wrong

### Tool seems to hang

- Check if there are many branches (parallel processing takes time)
- Use `--sequential` flag for debugging
- Enable `--debug` to see what's happening

### "GitHub token required" error

- Tool now works without token (as of recent update)
- If you see this error, you may be on an older version
- GitHub integration is optional - the tool works without it

### Branches marked as merged incorrectly

- Tool uses multiple merge detection strategies
- Some edge cases (complex rebases, manual merges) can be tricky
- Always preview with `--dry-run` first
- Report issues at: https://github.com/yourusername/git-branch-keeper/issues

## ✅ Safety Checklist for Each Use

Before running a cleanup:

- [ ] Run `git fetch --all` to update remote info
- [ ] Run with `--dry-run` first to preview
- [ ] Review the list of branches to be deleted carefully
- [ ] Verify protected branches are not in the list
- [ ] Check that no branches with open PRs are selected (if using GitHub)
- [ ] Ensure you're not on a branch that will be deleted
- [ ] Have a backup plan (know how to use `git reflog`)

## 🎓 Understanding the Output

### TUI Mode

- `✓` = Branch marked for deletion
- `✗` = Branch not marked
- Colors indicate status (green=safe, red=caution, etc.)

### CLI Mode

Look for these indicators:
- `[merged]` - Safe to delete
- `[stale]` - Review carefully
- `[active]` - Usually keep
- `[has PR]` - Protected from deletion

## 📚 Additional Resources

- **README.md**: Installation and basic usage
- **CLAUDE.md**: Developer guide and architecture
- **TODO.md**: Known issues and future improvements
- **GitHub Issues**: Report bugs and request features

## 💡 Pro Tips

1. **Start conservatively**: Use `stale_days: 90` until you're comfortable
2. **Use ignore patterns**: Liberally add patterns for any branch naming conventions
3. **Enable GitHub integration**: The extra PR protection is worth it
4. **Check reflog often**: `git reflog` is your safety net
5. **Document your patterns**: Comment your config file
6. **Test filters**: Use `--filter` and `--dry-run` to test pattern matching
7. **Trust the TUI**: Interactive mode is the safest way to clean up

## 🤝 Contributing to Safety

Found a new risk or have a safety improvement?
- Open an issue at the GitHub repository
- Submit a PR to update this document
- Share your safety practices with the community

---

**Remember**: When in doubt, use `--dry-run` first!
