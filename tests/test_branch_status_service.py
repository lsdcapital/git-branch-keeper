"""Tests for BranchStatusService"""

from git_branch_keeper.services.branch_status_service import BranchStatusService
from git_branch_keeper.models.branch import BranchStatus


class TestBranchStatusServiceInit:
    """Test BranchStatusService initialization."""

    def test_init(self, mock_git_repo, mock_config, mock_git_service, mock_github_service):
        """Test service initialization."""
        service = BranchStatusService(
            mock_git_repo.working_dir,
            mock_config,
            mock_git_service,
            mock_github_service,
            verbose=False,
        )

        assert service.repo_path == mock_git_repo.working_dir
        assert service.config == mock_config
        assert service.git_service == mock_git_service
        assert service.github_service == mock_github_service


class TestBranchStatusDetection:
    """Test branch status detection."""

    def test_get_status_protected_branch(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test protected branches are always ACTIVE."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        status = service.get_branch_status("main", "main")
        assert status == BranchStatus.ACTIVE

    def test_get_status_with_open_pr(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test branch with open PR is ACTIVE."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        pr_data = {"feature/test": {"count": 1, "merged": False, "closed": False}}

        status = service.get_branch_status("feature/test", "main", pr_data)
        assert status == BranchStatus.ACTIVE

    def test_get_status_merged_via_pr(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test branch merged via PR is MERGED."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        pr_data = {"feature/test": {"count": 0, "merged": True, "closed": False}}

        status = service.get_branch_status("feature/test", "main", pr_data)
        assert status == BranchStatus.MERGED

    def test_get_status_merged_via_git(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test branch merged via Git is MERGED."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        mock_git_service.is_branch_merged.return_value = True

        status = service.get_branch_status("feature/test", "main")
        assert status == BranchStatus.MERGED

    def test_get_status_stale_branch(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test old branch is STALE."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        mock_git_service.get_branch_age.return_value = 60  # 60 days old
        mock_git_service.is_branch_merged.return_value = False

        status = service.get_branch_status("feature/old", "main")
        assert status == BranchStatus.STALE

    def test_get_status_active_branch(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test recent branch is ACTIVE."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        mock_git_service.get_branch_age.return_value = 5  # 5 days old
        mock_git_service.is_branch_merged.return_value = False

        status = service.get_branch_status("feature/recent", "main")
        assert status == BranchStatus.ACTIVE


class TestBranchProtection:
    """Test branch protection checks."""

    def test_is_protected_branch_true(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test detecting protected branch."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        assert service.is_protected_branch("main") is True
        assert service.is_protected_branch("master") is True

    def test_is_protected_branch_false(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test detecting non-protected branch."""
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        assert service.is_protected_branch("feature/test") is False


class TestBranchIgnorePatterns:
    """Test branch ignore patterns."""

    def test_should_ignore_branch_true(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test detecting ignored branch."""
        mock_config["ignore_patterns"] = ["hotfix/*", "release/*"]
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        assert service.should_ignore_branch("hotfix/urgent-fix") is True
        assert service.should_ignore_branch("release/v1.0") is True

    def test_should_ignore_branch_false(
        self, mock_git_repo, mock_config, mock_git_service, mock_github_service
    ):
        """Test detecting non-ignored branch."""
        mock_config["ignore_patterns"] = ["hotfix/*"]
        service = BranchStatusService(
            mock_git_repo, mock_config, mock_git_service, mock_github_service
        )

        assert service.should_ignore_branch("feature/test") is False


# Debug tests removed - service now uses Python logging framework instead of print()
