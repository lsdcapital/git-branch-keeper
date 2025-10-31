"""Tests for merge method detection (merged-pr vs merged-git)."""

import pytest
from unittest.mock import Mock, patch
from pathlib import Path

from git_branch_keeper.core.branch_keeper import BranchKeeper
from git_branch_keeper.models.branch import BranchStatus, SyncStatus


class TestMergeMethodDetection:
    """Test that branches merged via PR show 'merged-pr' and git merges show 'merged-git'."""

    def test_branch_merged_via_pr_shows_merged_pr(self, git_repo, mock_config):
        """Test that a branch merged via GitHub PR shows 'merged-pr' status."""
        # Setup: Create a branch and merge it into main
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create and merge a branch
        repo.git.checkout("-b", "feature/pr-merged")
        test_file = repo_path / "pr_feature.txt"
        test_file.write_text("PR feature content\n")
        repo.index.add(["pr_feature.txt"])
        repo.index.commit("Add PR feature")

        # Merge into main (simulating a PR merge)
        repo.git.checkout("main")
        repo.git.merge("feature/pr-merged", "--no-ff")

        # Create BranchKeeper instance
        keeper = BranchKeeper(str(repo_path), mock_config)

        # Mock PR data indicating this branch was merged via PR
        pr_data = {
            "feature/pr-merged": {
                "count": 0,  # No open PRs
                "merged": True,  # Was merged via PR
                "closed": False
            }
        }

        # Test: Determine branch status with PR data
        status, sync_status, pr_status, notes = keeper._determine_branch_status(
            "feature/pr-merged",
            pr_data
        )

        # Assert: Should be merged with merged-pr sync status
        assert status == BranchStatus.MERGED
        assert sync_status == SyncStatus.MERGED_PR.value

    def test_branch_merged_via_git_shows_merged_git(self, git_repo, mock_config):
        """Test that a branch merged directly via git shows 'merged-git' status."""
        # Setup: Create a branch and merge it into main
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create and merge a branch
        repo.git.checkout("-b", "feature/git-merged")
        test_file = repo_path / "git_feature.txt"
        test_file.write_text("Git feature content\n")
        repo.index.add(["git_feature.txt"])
        repo.index.commit("Add git feature")

        # Merge into main (direct git merge, no PR)
        repo.git.checkout("main")
        repo.git.merge("feature/git-merged", "--no-ff")

        # Create BranchKeeper instance
        keeper = BranchKeeper(str(repo_path), mock_config)

        # Mock PR data indicating this branch was NOT merged via PR
        pr_data = {
            "feature/git-merged": {
                "count": 0,  # No open PRs
                "merged": False,  # NOT merged via PR
                "closed": False
            }
        }

        # Test: Determine branch status with PR data
        status, sync_status, pr_status, notes = keeper._determine_branch_status(
            "feature/git-merged",
            pr_data
        )

        # Assert: Should be merged with merged-git sync status
        assert status == BranchStatus.MERGED
        assert sync_status == SyncStatus.MERGED_GIT.value

    def test_branch_merged_no_pr_data_defaults_to_merged_git(self, git_repo, mock_config):
        """Test that merged branch with no PR data defaults to 'merged-git'."""
        # Setup: Create a branch and merge it into main
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create and merge a branch
        repo.git.checkout("-b", "feature/no-pr-data")
        test_file = repo_path / "no_pr.txt"
        test_file.write_text("No PR data content\n")
        repo.index.add(["no_pr.txt"])
        repo.index.commit("Add no PR data feature")

        # Merge into main
        repo.git.checkout("main")
        repo.git.merge("feature/no-pr-data", "--no-ff")

        # Create BranchKeeper instance
        keeper = BranchKeeper(str(repo_path), mock_config)

        # Test: Determine branch status with empty PR data
        pr_data = {}

        status, sync_status, pr_status, notes = keeper._determine_branch_status(
            "feature/no-pr-data",
            pr_data
        )

        # Assert: Should be merged with merged-git sync status (default when no PR data)
        assert status == BranchStatus.MERGED
        assert sync_status == SyncStatus.MERGED_GIT.value

    def test_pr_data_overrides_git_merge_detection(self, git_repo, mock_config):
        """Test that PR data correctly overrides the default git merge detection."""
        # Setup: Create a branch and merge it
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create and merge a branch
        repo.git.checkout("-b", "feature/override-test")
        test_file = repo_path / "override.txt"
        test_file.write_text("Override test content\n")
        repo.index.add(["override.txt"])
        repo.index.commit("Add override test")

        # Merge into main
        repo.git.checkout("main")
        repo.git.merge("feature/override-test", "--no-ff")

        # Create BranchKeeper instance
        keeper = BranchKeeper(str(repo_path), mock_config)

        # First test: Without PR data (should be merged-git)
        status1, sync_status1, _, _ = keeper._determine_branch_status(
            "feature/override-test",
            {}
        )
        assert status1 == BranchStatus.MERGED
        assert sync_status1 == SyncStatus.MERGED_GIT.value

        # Second test: With PR data indicating PR merge (should override to merged-pr)
        pr_data = {
            "feature/override-test": {
                "count": 0,
                "merged": True,  # Indicate it was merged via PR
                "closed": False
            }
        }

        status2, sync_status2, _, _ = keeper._determine_branch_status(
            "feature/override-test",
            pr_data
        )
        assert status2 == BranchStatus.MERGED
        assert sync_status2 == SyncStatus.MERGED_PR.value

    def test_branch_with_closed_unmerged_pr_not_marked_as_merged_pr(self, git_repo, mock_config):
        """Test that a branch with closed (but unmerged) PR is not marked as merged-pr."""
        # Setup: Create an unmerged branch
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create a branch but DON'T merge it
        repo.git.checkout("-b", "feature/closed-unmerged")
        test_file = repo_path / "closed_unmerged.txt"
        test_file.write_text("Closed but unmerged content\n")
        repo.index.add(["closed_unmerged.txt"])
        repo.index.commit("Add closed unmerged feature")
        repo.git.checkout("main")

        # Create BranchKeeper instance
        keeper = BranchKeeper(str(repo_path), mock_config)

        # Mock PR data: PR was closed but NOT merged
        pr_data = {
            "feature/closed-unmerged": {
                "count": 0,  # No open PRs
                "merged": False,  # NOT merged
                "closed": True  # But was closed
            }
        }

        # Test: Determine branch status
        status, sync_status, pr_status, notes = keeper._determine_branch_status(
            "feature/closed-unmerged",
            pr_data
        )

        # Assert: Should NOT be marked as merged
        assert status != BranchStatus.MERGED
        # Should be active (has closed PR but not merged)
        assert status == BranchStatus.ACTIVE
