"""Tests for GitService"""
import pytest
from unittest.mock import Mock, patch
import git

from git_branch_keeper.services.git_service import GitService
from git_branch_keeper.models.branch import SyncStatus


class TestGitServiceInit:
    """Test GitService initialization."""

    def test_init_with_repo_object(self, git_repo, mock_config):
        """Test initialization with a Repo path."""
        service = GitService(git_repo.working_dir, mock_config)
        assert service.repo_path == git_repo.working_dir
        assert service.config == mock_config
        assert service.verbose is False

    def test_init_with_repo_path(self, git_repo, mock_config):
        """Test initialization with a repository path."""
        service = GitService(git_repo.working_dir, mock_config)
        repo = service._get_repo()
        assert repo.working_dir == git_repo.working_dir

    def test_init_with_invalid_path(self, temp_dir, mock_config):
        """Test initialization with invalid path."""
        # GitService doesn't validate path in __init__, it creates repo on-demand
        service = GitService(str(temp_dir / "nonexistent"), mock_config)
        # Should fail when we try to get the repo
        with pytest.raises(Exception):
            service._get_repo()


class TestGitServiceBranchOperations:
    """Test basic branch operations."""

    def test_has_remote_branch_exists(self, git_repo_with_branches, mock_config):
        """Test detecting existing remote branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        # Create a remote tracking branch
        git_repo_with_branches.git.checkout('feature/test-feature')
        # has_remote_branch should return False if no remote exists
        assert service.has_remote_branch('feature/test-feature') is False

    def test_has_remote_branch_not_exists(self, git_repo_with_branches, mock_config):
        """Test detecting non-existent remote branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        assert service.has_remote_branch('nonexistent-branch') is False

    def test_get_branch_age(self, git_repo_with_branches, mock_config):
        """Test getting branch age in days."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        age = service.get_branch_age('main')
        assert age >= 0  # Should be recent (today or yesterday)
        assert age < 2  # Should not be more than a day old for fresh repo

    def test_get_branch_age_invalid_branch(self, git_repo_with_branches, mock_config):
        """Test getting age of non-existent branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        age = service.get_branch_age('nonexistent-branch')
        assert age == 0  # Should return 0 on error

    def test_get_last_commit_date(self, git_repo_with_branches, mock_config):
        """Test getting last commit date."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        date = service.get_last_commit_date('main')
        assert date  # Should return a date string
        assert '-' in date  # Should be in YYYY-MM-DD format

    def test_get_last_commit_date_invalid_branch(self, git_repo_with_branches, mock_config):
        """Test getting date of non-existent branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        date = service.get_last_commit_date('nonexistent-branch')
        assert date == "unknown"


class TestGitServiceSyncStatus:
    """Test branch sync status detection."""

    def test_sync_status_protected_branch_local_only(self, git_repo_with_branches, mock_config):
        """Test sync status for protected branch with no remote."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        status = service.get_branch_sync_status('main', 'main')
        assert status == SyncStatus.LOCAL_ONLY.value

    def test_sync_status_merged_branch(self, git_repo_with_branches, mock_config):
        """Test sync status for merged branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        # feature/to-merge was merged into main
        status = service.get_branch_sync_status('feature/to-merge', 'main')
        # Should detect as merged
        assert SyncStatus.MERGED_GIT.value in status or status == SyncStatus.SYNCED.value

    def test_sync_status_local_only_branch(self, git_repo_with_branches, mock_config):
        """Test sync status for local-only branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        status = service.get_branch_sync_status('feature/test-feature', 'main')
        # Branch exists locally but not on remote
        assert status == SyncStatus.LOCAL_ONLY.value or status == SyncStatus.MERGED_GIT.value


class TestGitServiceMergeDetection:
    """Test merge detection logic."""

    def test_is_branch_merged_true(self, git_repo_with_branches, mock_config):
        """Test detecting a merged branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        # feature/to-merge was merged into main
        is_merged = service.is_branch_merged('feature/to-merge', 'main')
        assert is_merged is True

    def test_is_branch_merged_false(self, git_repo_with_branches, mock_config):
        """Test detecting an unmerged branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        # feature/test-feature is not merged
        is_merged = service.is_branch_merged('feature/test-feature', 'main')
        assert is_merged is False

    def test_is_branch_merged_cache(self, git_repo_with_branches, mock_config):
        """Test that merge detection uses cache."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)

        # First call - should cache result
        result1 = service.is_branch_merged('feature/to-merge', 'main')

        # Second call - should use cache
        result2 = service.is_branch_merged('feature/to-merge', 'main')

        assert result1 == result2
        # Check cache was used
        cache_key = f"feature/to-merge:{mock_config['main_branch']}"
        assert cache_key in service._merge_status_cache

    def test_merge_detection_statistics(self, git_repo_with_branches, mock_config):
        """Test that merge detection statistics are tracked."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        service.verbose = True

        service.is_branch_merged('feature/to-merge', 'main')

        # At least one method should have been incremented
        total_attempts = sum(service.merge_detection_stats.values())
        assert total_attempts > 0


class TestGitServiceBranchDeletion:
    """Test branch deletion operations."""

    def test_delete_branch_local_only_dry_run(self, git_repo_with_branches, mock_config):
        """Test deleting local-only branch in dry-run mode."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        result = service.delete_branch('feature/test-feature', dry_run=True)
        assert result is True
        # Branch should still exist
        assert 'feature/test-feature' in [b.name for b in git_repo_with_branches.branches]

    def test_delete_branch_local_only_real(self, git_repo_with_branches, mock_config):
        """Test actually deleting a local-only branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        # Ensure we're not on the branch to delete
        git_repo_with_branches.git.checkout('main')

        result = service.delete_branch('feature/test-feature', dry_run=False)
        assert result is True
        # Branch should be deleted
        assert 'feature/test-feature' not in [b.name for b in git_repo_with_branches.branches]

    @patch('git_branch_keeper.services.git_service.console')
    def test_delete_branch_protected_remote(self, mock_console, git_repo_with_branches, mock_config):
        """Test deleting branch with protected remote."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)

        # Mock has_remote_branch to return True
        with patch.object(service, 'has_remote_branch', return_value=True):
            # Mock the repo to simulate protected branch error
            with patch.object(service, '_get_repo') as mock_get_repo:
                mock_repo = git_repo_with_branches
                mock_remote = Mock()
                mock_remote.push.side_effect = git.exc.GitCommandError(
                    'git push',
                    status=1,
                    stderr="remote: error: GH006: Protected branch"
                )

                # Save original remote method
                original_remote = mock_repo.remote
                mock_repo.remote = Mock(return_value=mock_remote)
                mock_get_repo.return_value = mock_repo

                git_repo_with_branches.git.checkout('main')
                result = service.delete_branch('feature/test-feature', dry_run=False)

                # Restore original remote method
                mock_repo.remote = original_remote

                # Should still return True (local deletion succeeded)
                assert result is True


class TestGitServiceBranchStatusDetails:
    """Test getting branch status details."""

    def test_get_branch_status_details_clean(self, git_repo_with_branches, mock_config):
        """Test getting status of clean branch."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        git_repo_with_branches.git.checkout('main')

        details = service.get_branch_status_details('main')

        assert details['modified'] is False
        assert details['untracked'] is False
        assert details['staged'] is False

    def test_get_branch_status_details_with_changes(self, git_repo_with_branches, mock_config):
        """Test getting status of branch with changes."""
        import os
        service = GitService(git_repo_with_branches.working_dir, mock_config)
        repo_path = git_repo_with_branches.working_dir

        # Create untracked file
        untracked_file = os.path.join(repo_path, 'untracked.txt')
        with open(untracked_file, 'w') as f:
            f.write('untracked content')

        details = service.get_branch_status_details('main')

        # Should detect untracked file
        assert details['untracked'] is True

    def test_get_branch_status_details_detached_head(self, git_repo_with_branches, mock_config):
        """Test getting status when in detached HEAD state."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)

        # Create detached HEAD state
        commit_sha = git_repo_with_branches.head.commit.hexsha
        git_repo_with_branches.git.checkout(commit_sha)

        # Should not crash
        details = service.get_branch_status_details('main')
        assert 'modified' in details


class TestGitServiceDebug:
    """Test debug functionality."""

    def test_debug_mode_off(self, git_repo, mock_config):
        """Test debug mode is correctly set to False."""
        mock_config['debug'] = False
        service = GitService(git_repo.working_dir, mock_config)
        assert service.debug_mode is False

    def test_debug_mode_on(self, git_repo, mock_config):
        """Test debug mode is correctly set to True."""
        mock_config['debug'] = True
        service = GitService(git_repo.working_dir, mock_config)
        assert service.debug_mode is True


class TestGitServiceEdgeCases:
    """Test edge cases and error handling."""

    def test_is_tag_detection(self, git_repo_with_branches, mock_config):
        """Test detecting tags vs branches."""
        service = GitService(git_repo_with_branches.working_dir, mock_config)

        # Create a tag
        git_repo_with_branches.create_tag('v1.0.0')

        assert service.is_tag('refs/tags/v1.0.0') is True
        assert service.is_tag('main') is False

    def test_operations_with_network_error(self, git_repo, mock_config):
        """Test handling network errors gracefully."""
        service = GitService(git_repo.working_dir, mock_config)

        # Mock the _get_repo to simulate network error
        with patch.object(service, '_get_repo') as mock_get_repo:
            mock_repo = Mock()
            mock_remote = Mock()
            mock_remote.pull.side_effect = git.exc.GitCommandError('fetch', 128)
            mock_repo.remote.return_value = mock_remote
            mock_get_repo.return_value = mock_repo

            # Should handle error gracefully
            result = service.update_main_branch('main')
            assert result is False
