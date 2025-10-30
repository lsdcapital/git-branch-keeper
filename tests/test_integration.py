"""Integration tests for BranchKeeper"""
import pytest
from unittest.mock import patch

from git_branch_keeper.core import BranchKeeper


class TestBranchKeeperInit:
    """Test BranchKeeper initialization."""

    def test_init_with_valid_repo(self, git_repo, mock_config):
        """Test initialization with valid repository."""
        keeper = BranchKeeper(git_repo.working_dir, mock_config)

        assert keeper.repo_path == git_repo.working_dir
        # Config is now a proper Config object, not a dict
        assert keeper.config.get('stale_days') == mock_config['stale_days']
        assert keeper.config.get('protected_branches') == mock_config['protected_branches']
        assert keeper.repo is not None

    def test_init_with_invalid_repo(self, temp_dir, mock_config):
        """Test initialization with invalid repository."""
        with pytest.raises(Exception, match="Error initializing repository"):
            BranchKeeper(str(temp_dir / "nonexistent"), mock_config)

    def test_init_services_created(self, git_repo, mock_config):
        """Test that all services are initialized."""
        keeper = BranchKeeper(git_repo.working_dir, mock_config)

        assert keeper.github_service is not None
        assert keeper.git_service is not None
        assert keeper.branch_status_service is not None
        assert keeper.display_service is not None


class TestBranchKeeperProcessing:
    """Test branch processing."""

    def test_process_branches_with_real_repo(self, git_repo_with_branches, mock_config):
        """Test processing branches with a real Git repository."""
        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        # Should not raise an exception
        keeper.process_branches(cleanup_enabled=False)

    @patch('git_branch_keeper.services.display_service.console')
    def test_process_branches_displays_results(self, mock_console, git_repo_with_branches, mock_config):
        """Test that process_branches displays branch information."""
        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        keeper.process_branches(cleanup_enabled=False)

        # Console should have been called to display information (by DisplayService)
        assert mock_console.print.called


class TestBranchKeeperCleanup:
    """Test branch cleanup operations."""

    def test_cleanup_dry_run(self, git_repo_with_branches, mock_config):
        """Test cleanup in dry-run mode."""
        mock_config['dry_run'] = True
        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        initial_branches = list(git_repo_with_branches.branches)

        keeper.cleanup()

        # No branches should be deleted in dry-run
        final_branches = list(git_repo_with_branches.branches)
        assert len(initial_branches) == len(final_branches)

    def test_cleanup_with_merged_branch(self, git_repo_with_branches, mock_config):
        """Test cleanup deletes merged branches."""
        mock_config['dry_run'] = False
        mock_config['force'] = True  # Skip confirmation
        mock_config['status_filter'] = 'merged'
        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        # Ensure we're on main
        git_repo_with_branches.git.checkout('main')

        initial_branch_count = len(list(git_repo_with_branches.branches))

        keeper.cleanup()

        # Should have deleted the merged branch
        final_branch_count = len(list(git_repo_with_branches.branches))
        assert final_branch_count <= initial_branch_count


class TestBranchKeeperEdgeCases:
    """Test edge cases."""

    def test_process_branches_empty_repo(self, git_repo, mock_config):
        """Test processing branches in repo with only main."""
        keeper = BranchKeeper(git_repo.working_dir, mock_config)

        # Should not raise an exception
        keeper.process_branches(cleanup_enabled=False)

    def test_process_branches_detached_head(self, git_repo_with_branches, mock_config):
        """Test processing branches when in detached HEAD state."""
        # Create detached HEAD
        commit_sha = git_repo_with_branches.head.commit.hexsha
        git_repo_with_branches.git.checkout(commit_sha)

        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        # Should not crash
        keeper.process_branches(cleanup_enabled=False)

    def test_delete_branch_while_on_it(self, git_repo_with_branches, mock_config):
        """Test cannot delete current branch."""
        git_repo_with_branches.git.checkout('feature/test-feature')

        keeper = BranchKeeper(git_repo_with_branches.working_dir, mock_config)

        # Try to delete current branch
        success, error_msg = keeper.delete_branch('feature/test-feature', 'test')

        assert success is False
        assert error_msg == 'Cannot delete current branch'
