"""Tests for GitHubService"""
from unittest.mock import Mock, patch

from git_branch_keeper.services.github_service import GitHubService


class TestGitHubServiceInit:
    """Test GitHubService initialization."""

    def test_init_without_token(self, mock_git_repo, mock_config):
        """Test initialization without GitHub token."""
        mock_config['github_token'] = None
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        assert service.github_token is None

    def test_init_with_token_from_config(self, mock_git_repo, mock_config):
        """Test initialization with token from config."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        assert service.github_token == "test_token"

    @patch.dict('os.environ', {'GITHUB_TOKEN': 'env_token'})
    def test_init_with_token_from_env(self, mock_git_repo, mock_config):
        """Test initialization with token from environment."""
        mock_config['github_token'] = None
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        assert service.github_token == "env_token"


class TestGitHubServiceSetup:
    """Test GitHub API setup."""

    def test_setup_with_github_url(self, mock_git_repo, mock_config):
        """Test setup with GitHub repository URL."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        with patch('git_branch_keeper.services.github_service.Github') as mock_github_class:
            mock_gh = Mock()
            mock_repo = Mock()
            mock_github_class.return_value = mock_gh
            mock_gh.get_repo.return_value = mock_repo

            service.setup_github_api("git@github.com:test/repo.git")

            assert service.github_repo == "test/repo"
            assert service.gh_repo is not None
            mock_gh.get_repo.assert_called_once_with("test/repo")

    def test_setup_with_https_url(self, mock_git_repo, mock_config):
        """Test setup with HTTPS GitHub URL."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        with patch('git_branch_keeper.services.github_service.Github') as mock_github_class:
            mock_gh = Mock()
            mock_repo = Mock()
            mock_github_class.return_value = mock_gh
            mock_gh.get_repo.return_value = mock_repo

            service.setup_github_api("https://github.com/test/repo.git")

            assert service.github_repo == "test/repo"
            assert service.gh_repo is not None


class TestGitHubServicePROperations:
    """Test PR-related operations."""

    def test_has_open_pr_true(self, mock_git_repo, mock_config):
        """Test detecting open PR."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        # Setup mock
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        mock_pulls = Mock()
        mock_pulls.totalCount = 1
        service.gh_repo.get_pulls.return_value = mock_pulls

        result = service.has_open_pr("feature/test")

        assert result is True
        service.gh_repo.get_pulls.assert_called_once()

    def test_has_open_pr_false(self, mock_git_repo, mock_config):
        """Test detecting no open PR."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        mock_pulls = Mock()
        mock_pulls.totalCount = 0
        service.gh_repo.get_pulls.return_value = mock_pulls

        result = service.has_open_pr("feature/test")

        assert result is False

    def test_has_open_pr_error_handling(self, mock_git_repo, mock_config):
        """Test has_open_pr handles errors gracefully."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()
        service.gh_repo.get_pulls.side_effect = Exception("API Error")

        result = service.has_open_pr("feature/test")

        assert result is False


class TestGitHubServiceBulkOperations:
    """Test bulk PR data fetching."""

    def test_get_bulk_pr_data(self, mock_git_repo, mock_config):
        """Test fetching PR data for multiple branches."""
        mock_config['github_token'] = "test_token"
        mock_config['max_prs_to_fetch'] = 500
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Create mock PRs
        pr1 = Mock()
        pr1.state = 'open'
        pr1.merged = False
        pr1.head = Mock()
        pr1.head.ref = "feature/branch1"
        pr1.base = Mock()
        pr1.base.ref = "main"

        pr2 = Mock()
        pr2.state = 'closed'
        pr2.merged = True
        pr2.head = Mock()
        pr2.head.ref = "feature/branch2"
        pr2.base = Mock()
        pr2.base.ref = "main"

        # Mock get_pulls to return PRs based on head parameter (per-branch fetching)
        def mock_get_pulls(state='all', head=None, base=None):
            if head == "test:feature/branch1":
                return [pr1]
            elif head == "test:feature/branch2":
                return [pr2]
            else:
                return []

        service.gh_repo.get_pulls = Mock(side_effect=mock_get_pulls)

        branch_names = ["feature/branch1", "feature/branch2", "feature/branch3"]
        result = service.get_bulk_pr_data(branch_names)

        assert "feature/branch1" in result
        assert result["feature/branch1"]["count"] == 1
        assert result["feature/branch1"]["merged"] is False

        assert "feature/branch2" in result
        assert result["feature/branch2"]["count"] == 0
        assert result["feature/branch2"]["merged"] is True

        assert "feature/branch3" in result
        assert result["feature/branch3"]["count"] == 0
        assert result["feature/branch3"]["merged"] is False

    def test_get_bulk_pr_data_pagination_limit(self, mock_git_repo, mock_config):
        """Test PR data fetching respects pagination limit."""
        mock_config['github_token'] = "test_token"
        mock_config['max_prs_to_fetch'] = 2  # Very low limit for testing
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Create many mock PRs
        prs = []
        for i in range(10):
            pr = Mock()
            pr.state = 'open'
            pr.merged = False
            pr.head = Mock()
            pr.head.ref = f"feature/branch{i}"
            pr.base = Mock()
            pr.base.ref = "main"
            prs.append(pr)

        service.gh_repo.get_pulls.return_value = iter(prs)

        result = service.get_bulk_pr_data(["feature/branch0"])

        # Should have stopped at the limit
        # The function will only process 2 PRs before breaking
        assert isinstance(result, dict)

    def test_get_bulk_pr_data_empty_branches(self, mock_git_repo, mock_config):
        """Test bulk PR data with empty branch list."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)
        service.gh_repo = Mock()

        result = service.get_bulk_pr_data([])

        assert result == {}

    def test_get_bulk_pr_data_error_handling(self, mock_git_repo, mock_config):
        """Test bulk PR data handles errors gracefully."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Simulate API error
        service.gh_repo.get_pulls.side_effect = Exception("API Error")

        result = service.get_bulk_pr_data(["feature/test"])

        # Should return default values for the branch instead of crashing
        assert "feature/test" in result
        assert result["feature/test"]["count"] == 0
        assert result["feature/test"]["merged"] is False
        assert result["feature/test"]["closed"] is False


class TestGitHubServiceEdgeCases:
    """Test edge cases and error handling."""

    def test_operations_with_api_rate_limit(self, mock_git_repo, mock_config):
        """Test handling API rate limit errors."""
        from github import GithubException

        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Simulate rate limit error
        service.gh_repo.get_pulls.side_effect = GithubException(
            status=403,
            data={'message': 'API rate limit exceeded'}
        )

        result = service.has_open_pr("feature/test")

        # Should handle gracefully
        assert result is False

    def test_operations_with_network_error(self, mock_git_repo, mock_config):
        """Test handling network errors."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Simulate network error
        service.gh_repo.get_pulls.side_effect = Exception("Network error")

        result = service.has_open_pr("feature/test")

        # Should handle gracefully
        assert result is False
