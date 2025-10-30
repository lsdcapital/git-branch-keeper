"""Pytest fixtures for git-branch-keeper tests"""
import tempfile
from pathlib import Path
from unittest.mock import Mock
import pytest
import git

from git_branch_keeper.models.branch import BranchStatus, SyncStatus


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config():
    """Create a mock configuration dictionary."""
    return {
        'verbose': False,
        'debug': False,
        'stale_days': 30,
        'protected_branches': ['main', 'master'],
        'ignore_patterns': [],
        'status_filter': 'all',
        'interactive': False,
        'dry_run': True,
        'force': False,
        'main_branch': 'main',
        'github_token': 'test_token_for_testing',  # Required for GitHub repos
        'max_prs_to_fetch': 500
    }


@pytest.fixture
def git_repo(temp_dir):
    """Create a real Git repository for testing."""
    repo_path = temp_dir / "test_repo"
    repo_path.mkdir()

    # Initialize repository
    repo = git.Repo.init(repo_path)

    # Configure git user for commits
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    # Create initial commit on main branch
    test_file = repo_path / "README.md"
    test_file.write_text("# Test Repository\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")

    # Rename master to main if needed
    try:
        repo.git.branch('-M', 'main')
    except Exception:
        pass

    # Add a fake GitHub remote for testing
    try:
        repo.create_remote('origin', 'git@github.com:test/test-repo.git')
    except Exception:
        pass

    yield repo

    # Cleanup
    repo.close()


@pytest.fixture
def git_repo_with_branches(git_repo):
    """Create a Git repository with multiple test branches."""
    repo = git_repo
    repo_path = Path(repo.working_dir)

    # Create feature branch
    repo.git.checkout('-b', 'feature/test-feature')
    test_file = repo_path / "feature.txt"
    test_file.write_text("Feature content\n")
    repo.index.add(["feature.txt"])
    repo.index.commit("Add feature")

    # Create stale branch (old commit)
    repo.git.checkout('main')
    repo.git.checkout('-b', 'stale/old-branch')
    old_file = repo_path / "old.txt"
    old_file.write_text("Old content\n")
    repo.index.add(["old.txt"])
    repo.index.commit("Old commit")
    # Make the commit appear old (this is a simulation - real age would require waiting)

    # Create merged branch
    repo.git.checkout('main')
    repo.git.checkout('-b', 'feature/to-merge')
    merge_file = repo_path / "merge.txt"
    merge_file.write_text("Merge content\n")
    repo.index.add(["merge.txt"])
    repo.index.commit("Feature to merge")

    # Merge it back to main
    repo.git.checkout('main')
    repo.git.merge('feature/to-merge', '--no-ff', '-m', 'Merge feature/to-merge')

    # Go back to main
    repo.git.checkout('main')

    yield repo


@pytest.fixture
def mock_git_repo():
    """Create a mock Git repository object."""
    repo = Mock(spec=git.Repo)
    repo.working_dir = "/fake/repo/path"
    repo.git_dir = "/fake/repo/path/.git"

    # Mock branches
    main_branch = Mock()
    main_branch.name = "main"
    main_branch.commit = Mock()
    main_branch.commit.hexsha = "abc123"
    main_branch.commit.committed_date = 1234567890

    repo.active_branch = main_branch
    repo.refs = {
        "main": main_branch,
        "feature/test": Mock(name="feature/test"),
    }

    # Mock remote
    remote = Mock()
    remote.url = "git@github.com:test/repo.git"
    remote.refs = []
    repo.remotes.origin = remote
    repo.remote = Mock(return_value=remote)

    return repo


@pytest.fixture
def mock_github():
    """Create a mock GitHub API object."""
    github = Mock()

    # Mock repository
    repo = Mock()
    repo.url = "https://api.github.com/repos/test/repo"
    repo.full_name = "test/repo"

    # Mock pulls
    def mock_get_pulls(state='open', head=None, base=None):
        pulls = Mock()
        pulls.totalCount = 0
        pulls.__iter__ = Mock(return_value=iter([]))
        return pulls

    repo.get_pulls = mock_get_pulls

    github.get_repo = Mock(return_value=repo)

    return github


@pytest.fixture
def mock_github_service(mock_config, mock_git_repo):
    """Create a mock GitHubService."""
    from git_branch_keeper.services.github_service import GitHubService

    service = GitHubService(mock_git_repo, mock_config)
    # Set up minimal mocks for testing
    service.github_repo = "test/repo"
    service.gh_repo = Mock()
    return service


@pytest.fixture
def mock_git_service(mock_config, mock_git_repo):
    """Create a mock GitService."""
    from git_branch_keeper.services.git_service import GitService

    service = Mock(spec=GitService)
    service.repo = mock_git_repo
    service.config = mock_config
    service.verbose = False
    service.debug_mode = False

    # Mock common methods
    service.has_remote_branch = Mock(return_value=False)
    service.get_branch_age = Mock(return_value=10)
    service.get_last_commit_date = Mock(return_value="2024-01-01")
    service.get_branch_sync_status = Mock(return_value=SyncStatus.SYNCED.value)
    service.is_branch_merged = Mock(return_value=False)
    service.is_tag = Mock(return_value=False)

    return service


@pytest.fixture
def sample_branch_data():
    """Create sample branch data for testing."""
    from git_branch_keeper.models.branch import BranchDetails

    return [
        BranchDetails(
            name="feature/active",
            last_commit_date="2024-01-15",
            age_days=5,
            status=BranchStatus.ACTIVE,
            has_local_changes=False,
            has_remote=True,
            sync_status=SyncStatus.SYNCED.value,
            pr_status="",
            notes=None
        ),
        BranchDetails(
            name="feature/stale",
            last_commit_date="2023-11-01",
            age_days=90,
            status=BranchStatus.STALE,
            has_local_changes=False,
            has_remote=True,
            sync_status=SyncStatus.BEHIND.value,
            pr_status="",
            notes=None
        ),
        BranchDetails(
            name="feature/merged",
            last_commit_date="2024-01-10",
            age_days=10,
            status=BranchStatus.MERGED,
            has_local_changes=False,
            has_remote=False,
            sync_status=SyncStatus.MERGED_GIT.value,
            pr_status="",
            notes=None
        ),
    ]


@pytest.fixture
def mock_pr_data():
    """Create mock PR data for testing."""
    return {
        "feature/with-pr": {
            "count": 1,
            "merged": False,
            "closed": False
        },
        "feature/merged-pr": {
            "count": 0,
            "merged": True,
            "closed": False
        },
        "feature/closed-pr": {
            "count": 0,
            "merged": False,
            "closed": True
        }
    }
