# git-branch-keeper

A smart Git branch management tool that helps keep your repository clean and organized. It identifies and helps you clean up merged and stale branches while protecting branches with open pull requests.

## Features

- 📊 Display branch status in a beautiful table format
- 🔍 Filter branches by status (merged/stale/all)
- 🔄 Detect merged branches
- ⏰ Identify stale branches based on age
- 🔒 Protect branches with open PRs
- 🚀 Support for both local and remote branch cleanup
- 📝 Detailed status reporting
- ⚡ Force mode for automated cleanup
- 🔍 Dry-run mode to preview changes

## Installation

### Development Installation

Since this package is currently in development, install it directly from the source:

```bash
# Clone the repository
git clone https://github.com/WeR1Hub/git-branch-keeper.git
cd git-branch-keeper

# Install in development mode using pipx
pipx install -e .

# Or using pip in a virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### Future Installation (Once Published)

Once the package is published to PyPI, you'll be able to install it using:

```bash
# Using pipx (Recommended)
pipx install git-branch-keeper

# Or using pip
pip install git-branch-keeper
```

## Usage

Basic usage:

```bash
# Show status of all branches
git-branch-keeper --status all

# Show only merged branches
git-branch-keeper --status merged

# Show only stale branches
git-branch-keeper --status stale

# Delete merged branches (interactive)
git-branch-keeper --status merged

# Delete stale branches (interactive)
git-branch-keeper --status stale

# Force delete merged branches (no confirmation)
git-branch-keeper --status merged -f

# Dry run to see what would be deleted
git-branch-keeper --status merged --dry-run
```

## Configuration

The tool can be configured using a JSON configuration file. By default, it looks for:
1. A file specified via command line argument
2. `git-branch-keeper.json` in the current directory
3. `.git-branch-keeper.json` in your home directory

Copy the example configuration file to get started:

```bash
cp git-branch-keeper.example.json git-branch-keeper.json
```

### Configuration Options

```json
{
    "protected_branches": [                        // Branches that will never be modified
        "main",
        "master",
        "dev"
    ],
    "ignore_patterns": [                          // Branch patterns to ignore (glob syntax)
        "develop",                                // Exact match
        "staging",                                // Exact match
        "release/*",                              // All release branches
        "feature/*",                              // All feature branches
        "hotfix/*"                                // All hotfix branches
    ],
    "stale_days": 30,                            // Number of days before a branch is considered stale
    "github_token": "your-github-token-here"      // GitHub personal access token (optional)
}
```

The `protected_branches` list specifies branches that will never be modified or deleted by the tool. By default, this includes "main" and "master". The GitHub token is optional and only required if you want to check for open pull requests before cleaning branches. The repository information is automatically detected from your git remote URL.

Branch patterns support glob syntax:
- `*` matches any sequence of characters
- `?` matches any single character
- `[seq]` matches any character in seq
- `[!seq]` matches any character not in seq

For example:
- `feature/*` matches all feature branches
- `release/v?.?.*` matches release branches like "release/v1.2.3"
- `hotfix-*` matches all hotfix branches

## Options

- `--status {all,merged,stale}`: Filter branches by status
- `-f, --force`: Force deletion without confirmation
- `--dry-run`: Show what would be deleted without making changes
- `-v, --verbose`: Enable verbose output
- `-c, --config`: Path to config file
- `--stale-days`: Days before a branch is considered stale (default: 30)
- `--version`: Show version information

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License
