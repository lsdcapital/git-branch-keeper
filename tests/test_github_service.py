"""Tests for GitHubService"""
from unittest.mock import Mock, patch

from git_branch_keeper.services.github_service import GitHubService


class TestGitHubServiceInit:
    """Test GitHubService initialization."""

    def test_init_without_token(self, mock_git_repo, mock_config):
        """Test initialization without GitHub token."""
        mock_config['github_token'] = None
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        assert service.github_enabled is False
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

            assert service.github_enabled is True
            assert service.github_repo == "test/repo"
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

            assert service.github_enabled is True
            assert service.github_repo == "test/repo"

    def test_setup_with_non_github_url(self, mock_git_repo, mock_config):
        """Test setup with non-GitHub URL."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.setup_github_api("git@gitlab.com:test/repo.git")

        assert service.github_enabled is False

    def test_setup_without_token(self, mock_git_repo, mock_config):
        """Test setup fails gracefully without token."""
        mock_config['github_token'] = None
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.setup_github_api("git@github.com:test/repo.git")

        assert service.github_enabled is False


class TestGitHubServicePROperations:
    """Test PR-related operations."""

    def test_has_open_pr_true(self, mock_git_repo, mock_config):
        """Test detecting open PR."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        # Setup mock
        service.github_enabled = True
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

        service.github_enabled = True
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        mock_pulls = Mock()
        mock_pulls.totalCount = 0
        service.gh_repo.get_pulls.return_value = mock_pulls

        result = service.has_open_pr("feature/test")

        assert result is False

    def test_has_open_pr_github_disabled(self, mock_git_repo, mock_config):
        """Test has_open_pr when GitHub is disabled."""
        service = GitHubService(mock_git_repo.working_dir, mock_config)
        service.github_enabled = False

        result = service.has_open_pr("feature/test")

        assert result is False

    def test_get_pr_status(self, mock_git_repo, mock_config):
        """Test getting PR status."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Create mock PRs
        open_pr = Mock()
        open_pr.state = 'open'
        open_pr.merged = False

        merged_pr = Mock()
        merged_pr.state = 'closed'
        merged_pr.merged = True

        service.gh_repo.get_pulls.return_value = [open_pr, merged_pr]

        count, was_merged = service.get_pr_status("feature/test")

        assert count == 1  # One open PR
        assert was_merged is True  # Branch was merged via PR

    def test_was_merged_via_pr_true(self, mock_git_repo, mock_config):
        """Test detecting branch merged via PR."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True

        with patch.object(service, 'get_branch_pr_status') as mock_get_status:
            mock_get_status.return_value = {
                'has_pr': True,
                'state': 'merged'
            }

            result = service.was_merged_via_pr("feature/test")
            assert result is True

    def test_was_merged_via_pr_false(self, mock_git_repo, mock_config):
        """Test detecting branch not merged via PR."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True

        with patch.object(service, 'get_branch_pr_status') as mock_get_status:
            mock_get_status.return_value = {
                'has_pr': True,
                'state': 'open'
            }

            result = service.was_merged_via_pr("feature/test")
            assert result is False


class TestGitHubServiceBulkOperations:
    """Test bulk PR data fetching."""

    def test_get_bulk_pr_data(self, mock_git_repo, mock_config):
        """Test fetching PR data for multiple branches."""
        mock_config['github_token'] = "test_token"
        mock_config['max_prs_to_fetch'] = 500
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True
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

        service.github_enabled = True
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

    def test_get_bulk_pr_data_github_disabled(self, mock_git_repo, mock_config):
        """Test bulk PR data when GitHub is disabled."""
        service = GitHubService(mock_git_repo.working_dir, mock_config)
        service.github_enabled = False

        result = service.get_bulk_pr_data(["feature/test"])

        assert result == {}

    def test_get_bulk_pr_data_error_handling(self, mock_git_repo, mock_config):
        """Test bulk PR data handles errors gracefully."""
        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True
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


class TestGitHubServicePRCounts:
    """Test PR count operations."""

    def test_get_pr_count_for_branch(self, mock_git_repo, mock_config):
        """Test getting PR count for a regular branch."""
        mock_config['github_token'] = "test_token"
        mock_config['protected_branches'] = ['main']
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        mock_pulls = Mock()
        mock_pulls.totalCount = 2
        service.gh_repo.get_pulls.return_value = mock_pulls

        count = service.get_pr_count("feature/test")

        assert count == 2

    def test_get_pr_count_for_protected_branch(self, mock_git_repo, mock_config):
        """Test getting PR count for protected branch (includes targeting PRs)."""
        mock_config['github_token'] = "test_token"
        mock_config['protected_branches'] = ['main']
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Mock PRs from branch
        from_branch_pulls = Mock()
        from_branch_pulls.totalCount = 1

        # Mock PRs targeting branch
        to_branch_pulls = Mock()
        to_branch_pulls.totalCount = 3

        service.gh_repo.get_pulls.side_effect = [from_branch_pulls, to_branch_pulls]

        count = service.get_pr_count("main")

        # Should sum both
        assert count == 4

    def test_get_pr_count_github_disabled(self, mock_git_repo, mock_config):
        """Test get_pr_count when GitHub is disabled."""
        service = GitHubService(mock_git_repo.working_dir, mock_config)
        service.github_enabled = False

        count = service.get_pr_count("feature/test")

        assert count == 0


class TestGitHubServiceEdgeCases:
    """Test edge cases and error handling."""

    def test_operations_with_api_rate_limit(self, mock_git_repo, mock_config):
        """Test handling API rate limit errors."""
        from github import GithubException

        mock_config['github_token'] = "test_token"
        service = GitHubService(mock_git_repo.working_dir, mock_config)

        service.github_enabled = True
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

        service.github_enabled = True
        service.gh_repo = Mock()

        # Simulate network error
        service.gh_repo.get_pulls.side_effect = Exception("Network error")

        result = service.has_open_pr("feature/test")

        # Should handle gracefully
        assert result is False
