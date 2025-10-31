# Release Checklist

This document outlines the steps for creating a new release of git-branch-keeper.

## Versioning System

This project uses **dynamic versioning** with `hatch-vcs`:

- **Version source**: Git tags (e.g., `v0.1.0`, `v1.2.3`)
- **No hardcoded versions**: Version is automatically determined from git tags
- **Development versions**: Commits after a tag show as `0.1.0.post3+g5678def`

### Version Numbering

Follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (X.0.0): Breaking changes
- **MINOR** (0.X.0): New features (backward compatible)
- **PATCH** (0.0.X): Bug fixes (backward compatible)

Examples:
- `v0.1.0` - First public release
- `v0.2.0` - New feature added
- `v0.2.1` - Bug fix
- `v1.0.0` - Stable API, breaking changes from 0.x

## Pre-Release Checklist

### 1. Code Quality

- [ ] All tests passing (when test suite is implemented)
- [ ] Code formatted with Black: `black .`
- [ ] Linting passes: `ruff check .`
- [ ] Type checking passes: `mypy git_branch_keeper`
- [ ] No known critical bugs

### 2. Documentation

- [ ] README.md is up to date
- [ ] CHANGELOG.md has been updated with all changes
- [ ] CONTRIBUTING.md is current
- [ ] All command-line options are documented
- [ ] Configuration options are documented

### 3. Version Selection

- [ ] Decide on version number following [Semantic Versioning](https://semver.org/)
  - MAJOR: Breaking changes
  - MINOR: New features (backward compatible)
  - PATCH: Bug fixes (backward compatible)
- [ ] Version will be set via git tag (no manual edits needed)

### 4. CHANGELOG.md

Update CHANGELOG.md with the new version:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features...

### Changed
- Changes to existing functionality...

### Fixed
- Bug fixes...

### Removed
- Removed features...
```

### 5. Testing

- [ ] Test installation from source: `uv sync && uv run git-branch-keeper --version`
- [ ] Test in TUI mode: `uv run git-branch-keeper`
- [ ] Test in CLI mode: `uv run git-branch-keeper --no-interactive`
- [ ] Test on different Python versions (3.8, 3.9, 3.10, 3.11, 3.12)
- [ ] Test on different operating systems (if possible)
- [ ] Test with different repository scenarios:
  - [ ] Small repo (< 10 branches)
  - [ ] Large repo (100+ branches)
  - [ ] Repo with worktrees
  - [ ] Repo with open PRs
  - [ ] Repo without GitHub token

## Release Process

### 1. Update CHANGELOG

Update CHANGELOG.md with the release date:

```bash
git checkout main
git pull origin main
# Edit CHANGELOG.md - change [Unreleased] to [X.Y.Z] - YYYY-MM-DD
git add CHANGELOG.md
git commit -m "Update CHANGELOG for vX.Y.Z release"
git push origin main
```

### 2. Create Version Tag

Create and push a git tag for the version:

```bash
git checkout main
git pull origin main
git tag -a vX.Y.Z -m "Release version X.Y.Z"
git push origin vX.Y.Z
```

**Note**: The tag name determines the version number. Use format `vX.Y.Z` (e.g., `v0.1.0`, `v1.2.3`).

### 3. Publish to PyPI

You have two options to trigger the release:

#### Option A: Manual Trigger (Recommended)

1. Go to: https://github.com/lsdcapital/git-branch-keeper/actions/workflows/release.yml
2. Click "Run workflow"
3. Select the tag you just created (e.g., `v0.1.0`) or leave blank for latest
4. Click "Run workflow" button
5. Monitor the workflow execution

**Benefits**: Full control over when publishing happens

#### Option B: Automatic on Tag Push

The workflow also auto-triggers when you push a `v*.*.*` tag. If you pushed the tag in step 2, the release workflow is already running.

**Note**: Both methods do the same thing - just different triggers.

### 4. Monitor GitHub Actions

The release workflow will:
- ✅ Build the package
- ✅ Verify package with twine
- ✅ Create a GitHub Release with auto-generated notes
- ✅ Publish to PyPI using Trusted Publishers (no token needed)

Watch the workflow at: https://github.com/lsdcapital/git-branch-keeper/actions

### 5. Verify Release

Once the workflow completes:

- [ ] Check GitHub Releases: https://github.com/lsdcapital/git-branch-keeper/releases
- [ ] Verify PyPI: https://pypi.org/project/git-branch-keeper/
- [ ] Test installation: `pipx install git-branch-keeper`
- [ ] Test installed version: `git-branch-keeper --version`

### 6. Announce Release

- [ ] Post in GitHub Discussions (if enabled)
- [ ] Update any relevant documentation sites
- [ ] Tweet/post on social media (optional)

## Post-Release

### 1. Update main branch

If needed, update CHANGELOG.md to add an `[Unreleased]` section:

```markdown
## [Unreleased]

### Added
### Changed
### Fixed
### Removed

## [X.Y.Z] - YYYY-MM-DD
...
```

### 2. Monitor for Issues

Watch for bug reports or issues with the new release.

## PyPI Setup (First Release Only)

This project uses **PyPI Trusted Publishers** for secure, token-free publishing.

### 1. Create PyPI Account

Sign up at https://pypi.org/account/register/

### 2. Configure Trusted Publisher

**Before your first release**, set up the trusted publisher:

1. Go to: https://pypi.org/manage/account/publishing/
2. Click "Add a new pending publisher"
3. Fill in the form:
   ```
   PyPI Project Name:    git-branch-keeper
   Owner:                lsdcapital
   Repository name:      git-branch-keeper
   Workflow name:        release.yml
   Environment name:     release
   ```
4. Click "Add"

**That's it!** No tokens or secrets needed.

### 3. How It Works

When you trigger the release workflow:
- GitHub Actions authenticates with PyPI using OpenID Connect (OIDC)
- PyPI verifies the workflow is running from your repository
- Package is published automatically

### 4. Manual Upload (if needed)

If automatic publishing fails, you can publish manually:

```bash
# Build the package
uv build

# Upload to PyPI (requires API token for manual upload)
uv run twine upload dist/*
```

## Hotfix Process

For urgent bug fixes that need to be released immediately:

### 1. Create Hotfix Branch

```bash
git checkout main
git checkout -b hotfix/vX.Y.Z
```

### 2. Make Fix

Make the minimal changes needed to fix the bug.

### 3. Update Version

Increment the PATCH version (e.g., 1.2.3 → 1.2.4).

### 4. Follow Normal Release Process

Follow steps 2-9 from the normal release process.

## Rollback Procedure

If a release has critical issues:

### 1. Yank from PyPI

```bash
# Install twine if needed
pip install twine

# Yank the release
twine yank git-branch-keeper -v X.Y.Z
```

This doesn't delete the release but marks it as unsuitable for installation.

### 2. Create Hotfix

Follow the hotfix process to release a corrected version.

### 3. Update GitHub Release

Mark the GitHub release as "pre-release" or add a warning in the description.

## Version History

| Version | Release Date | Notes |
|---------|--------------|-------|
| 0.1.0   | TBD          | Initial public release |

## Contact

For questions about the release process, open an issue on GitHub.
