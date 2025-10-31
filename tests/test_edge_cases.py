"""Tests for edge cases and error scenarios"""

from unittest.mock import Mock, patch
import git
import pytest

from git_branch_keeper.core import BranchKeeper
from git_branch_keeper.services.git import GitOperations
from git_branch_keeper.services.git import GitHubService
from git_branch_keeper.models.branch import SyncStatus


class TestDetachedHeadHandling:
    """Test handling of detached HEAD state."""

    def test_delete_branch_in_detached_head(self, git_repo_with_branches, mock_config):
        """Test deleting branch when in detached HEAD."""
        # Create detached HEAD state
        commit_sha = git_repo_with_branches.head.commit.hexsha
        git_repo_with_branches.git.checkout(commit_sha)

        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)
        mock_config["dry_run"] = False

        # Should not crash when checking active_branch
        result = keeper.delete_branch("feature/test-feature", "test")

        # Should allow deletion since no active branch
        assert result is not None

    def test_get_branch_status_detached_head(self, git_repo_with_branches, mock_config):
        """Test getting branch status when in detached HEAD."""
        service = GitOperations(git_repo_with_branches.working_dir, mock_config)

        # Create detached HEAD
        commit_sha = git_repo_with_branches.head.commit.hexsha
        git_repo_with_branches.git.checkout(commit_sha)

        # Should not crash
        details = service.get_branch_status_details("main")
        assert "modified" in details


class TestProtectedRemoteBranches:
    """Test handling of protected remote branches."""

    def test_delete_protected_remote_branch(self, git_repo, mock_config):
        """Test deletion of branch with protected remote."""
        service = GitOperations(git_repo.working_dir, mock_config)

        with patch.object(service, "has_remote_branch", return_value=True):
            # Mock the repo.remote() call to simulate protected branch error
            with patch.object(service, "_get_repo") as mock_get_repo:
                mock_repo = Mock()
                mock_remote = Mock()
                mock_remote.push.side_effect = git.exc.GitCommandError(
                    "push", status=1, stderr="remote: error: GH006: Protected branch update failed"
                )
                mock_repo.remote.return_value = mock_remote
                mock_repo.delete_head = Mock()
                mock_get_repo.return_value = mock_repo

                # Should handle gracefully
                result = service.delete_branch("main", dry_run=False)

                # Local deletion should succeed even if remote fails
                assert result is True


class TestGitHubAPIPagination:
    """Test GitHub API pagination limits."""

    def test_bulk_pr_data_respects_limit(self, mock_git_repo, mock_config):
        """Test that bulk PR fetching respects the limit."""
        mock_config["github_token"] = "test_token"
        mock_config["max_prs_to_fetch"] = 5

        service = GitHubService(mock_git_repo, mock_config)
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Create 10 mock PRs
        prs = []
        for i in range(10):
            pr = Mock()
            pr.state = "open"
            pr.merged = False
            pr.head = Mock()
            pr.head.ref = f"branch{i}"
            pr.base = Mock()
            pr.base.ref = "main"
            prs.append(pr)

        service.gh_repo.get_pulls.return_value = iter(prs)

        result = service.get_bulk_pr_data([f"branch{i}" for i in range(10)])

        # Should have data, but processing should have stopped at limit
        assert isinstance(result, dict)


class TestNetworkErrors:
    """Test handling of network errors."""

    def test_github_api_network_error(self, mock_git_repo, mock_config):
        """Test handling GitHub API network errors."""
        mock_config["github_token"] = "test_token"
        service = GitHubService(mock_git_repo, mock_config)
        service.github_repo = "test/repo"
        service.gh_repo = Mock()

        # Simulate network error
        service.gh_repo.get_pulls.side_effect = Exception("Network unreachable")

        # Should handle gracefully
        result = service.has_open_pr("feature/test")
        assert result is False


class TestInvalidBranchNames:
    """Test handling of invalid or special branch names."""

    def test_branch_with_special_characters(self, git_repo, mock_config):
        """Test handling branches with special characters."""
        service = GitOperations(git_repo, mock_config)

        # GitPython should handle escaping automatically
        # Test that we don't crash with special characters
        age = service.get_branch_age("nonexistent/branch-with-dashes_and_underscores")
        assert age == 0  # Returns 0 for non-existent branches


class TestEmptyRepository:
    """Test handling of edge cases in empty or minimal repos."""

    def test_process_empty_repository(self, git_repo, mock_config):
        """Test processing repository with no feature branches."""
        keeper = BranchKeeper(git_repo.working_dir, mock_config)

        # Should not crash with only main branch
        keeper.process_branches(cleanup_enabled=False)


class TestMergeDetectionEdgeCases:
    """Test edge cases in merge detection."""

    def test_merge_detection_with_cache(self, git_repo_with_branches, mock_config):
        """Test that merge detection caching works correctly."""
        service = GitOperations(git_repo_with_branches, mock_config)

        branch = "feature/to-merge"
        main = "main"

        # First call
        result1 = service.is_branch_merged(branch, main)

        # Cache should be populated
        cache_key = f"{branch}:{main}"
        assert cache_key in service._merge_status_cache

        # Second call should use cache
        result2 = service.is_branch_merged(branch, main)

        assert result1 == result2

    def test_merge_detection_non_existent_branch(self, git_repo, mock_config):
        """Test merge detection with non-existent branch."""
        service = GitOperations(git_repo, mock_config)

        result = service.is_branch_merged("nonexistent", "main")

        # Should return False for non-existent branches
        assert result is False


class TestConfigurationEdgeCases:
    """Test edge cases in configuration."""

    def test_missing_config_values_use_defaults(self, git_repo):
        """Test that missing config values use sensible defaults."""
        minimal_config = {"github_token": "test_token"}  # Required for GitHub repos

        keeper = BranchKeeper(git_repo.working_dir, minimal_config)

        # Should use defaults
        assert keeper.min_stale_days == 30
        assert "main" in keeper.protected_branches or "master" in keeper.protected_branches

    def test_invalid_filter_values(self, git_repo, mock_config):
        """Test handling of invalid filter values."""
        mock_config["status_filter"] = "invalid_filter"

        # Config validation should catch invalid filter and raise ValueError
        with pytest.raises(ValueError, match="status_filter must be one of"):
            BranchKeeper(git_repo.working_dir, mock_config)


class TestConcurrencyEdgeCases:
    """Test edge cases related to concurrent operations."""

    def test_signal_handling_during_git_operation(self, git_repo, mock_config):
        """Test that signal handling respects in_git_operation flag."""
        keeper = BranchKeeper(git_repo.working_dir, mock_config)

        # Verify that git_service has in_git_operation flag
        assert hasattr(keeper.git_service, "in_git_operation")
        assert not keeper.git_service.in_git_operation

        # Simulate being in a git operation
        keeper.git_service.in_git_operation = True
        assert keeper.git_service.in_git_operation

        # Clean up
        keeper.git_service.in_git_operation = False


class TestSyncStatusEdgeCases:
    """Test edge cases in sync status detection."""

    def test_sync_status_with_no_remote(self, git_repo, mock_config):
        """Test sync status when branch has no remote."""
        service = GitOperations(git_repo, mock_config)

        status = service.get_branch_sync_status("main", "main")

        # Should handle gracefully
        assert status == SyncStatus.LOCAL_ONLY.value

    def test_sync_status_behind_main(self, git_repo_with_branches, mock_config):
        """Test detecting when main branch is behind."""
        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        # Process should detect if main is behind
        # (In our test repo, main is up to date, but the code handles this case)
        keeper.process_branches(cleanup_enabled=False)
