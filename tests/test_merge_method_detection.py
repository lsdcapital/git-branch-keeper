"""Tests for merge method detection (merged-pr vs merged-git)."""

from pathlib import Path

from git_branch_keeper.core.branch_keeper import BranchKeeper
from git_branch_keeper.models.branch import BranchStatus, SyncStatus


class TestMergeMethodDetection:
    """Test that branches merged via PR show 'merged-pr' and git merges show 'merged-git'."""

    def test_open_pr_displays_selected_number_instead_of_open_count(self, git_repo, mock_config):
        """The PR cell links to a real PR even when the branch has multiple open PRs."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        repo.git.checkout("-b", "feature/open-pr")
        test_file = repo_path / "open_pr.txt"
        test_file.write_text("Open PR content\n")
        repo.index.add(["open_pr.txt"])
        repo.index.commit("Add open PR feature")
        repo.git.checkout("main")

        keeper = BranchKeeper(str(repo_path), mock_config)
        pr_data = {
            "feature/open-pr": {
                "count": 2,
                "merged": False,
                "closed": False,
                "number": 91,
            }
        }

        status, _sync_status, pr_status, _notes = keeper._determine_branch_status(
            "feature/open-pr", pr_data
        )

        assert status == BranchStatus.ACTIVE
        assert pr_status == "91"

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
                "closed": False,
            }
        }

        # Test: Determine branch status with PR data
        status, sync_status, _pr_status, _notes = keeper._determine_branch_status(
            "feature/pr-merged", pr_data
        )

        # Assert: Should be merged with merged-pr sync status
        assert status == BranchStatus.MERGED
        assert sync_status == SyncStatus.MERGED_PR.value

    def test_merged_pr_head_sha_matching_local_tip_shows_merged_pr(self, git_repo, mock_config):
        """A merged PR is authoritative when the local branch still matches the PR head."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        repo.git.checkout("-b", "feature/pr-head-match")
        test_file = repo_path / "pr_head_match.txt"
        test_file.write_text("PR head match content\n")
        repo.index.add(["pr_head_match.txt"])
        repo.index.commit("Add PR head match feature")
        local_tip = repo.head.commit.hexsha
        repo.git.checkout("main")

        keeper = BranchKeeper(str(repo_path), mock_config)
        pr_data = {
            "feature/pr-head-match": {
                "count": 0,
                "merged": True,
                "closed": False,
                "number": 42,
                "head_sha": local_tip,
            }
        }

        status, sync_status, pr_status, notes = keeper._determine_branch_status(
            "feature/pr-head-match", pr_data
        )

        assert status == BranchStatus.MERGED
        assert sync_status == SyncStatus.MERGED_PR.value
        assert pr_status == "42"
        assert notes is None
        assert pr_data["feature/pr-head-match"]["head_matches_local"] is True

    def test_merged_pr_head_sha_mismatch_does_not_force_merged_pr(self, git_repo, mock_config):
        """A merged PR does not prove the current local branch tip is the merged PR head."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        repo.git.checkout("-b", "feature/pr-head-mismatch")
        test_file = repo_path / "pr_head_mismatch.txt"
        test_file.write_text("PR head mismatch content\n")
        repo.index.add(["pr_head_mismatch.txt"])
        repo.index.commit("Add PR head mismatch feature")
        local_tip = repo.head.commit.hexsha
        repo.git.checkout("main")

        keeper = BranchKeeper(str(repo_path), mock_config)
        pr_data = {
            "feature/pr-head-mismatch": {
                "count": 0,
                "merged": True,
                "closed": False,
                "number": 35,
                "head_sha": "0" * 40,
            }
        }

        status, sync_status, _, notes = keeper._determine_branch_status(
            "feature/pr-head-mismatch", pr_data
        )

        assert status == BranchStatus.ACTIVE
        assert sync_status != SyncStatus.MERGED_PR.value
        assert "PR #35 merged but local tip differs from PR head" in notes
        assert pr_data["feature/pr-head-mismatch"]["local_head_sha"] == local_tip
        assert pr_data["feature/pr-head-mismatch"]["head_matches_local"] is False

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
                "closed": False,
            }
        }

        # Test: Determine branch status with PR data
        status, sync_status, _pr_status, _notes = keeper._determine_branch_status(
            "feature/git-merged", pr_data
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

        status, sync_status, _pr_status, _notes = keeper._determine_branch_status(
            "feature/no-pr-data", pr_data
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
        status1, sync_status1, _, _ = keeper._determine_branch_status("feature/override-test", {})
        assert status1 == BranchStatus.MERGED
        assert sync_status1 == SyncStatus.MERGED_GIT.value

        # Second test: With PR data indicating PR merge (should override to merged-pr)
        pr_data = {
            "feature/override-test": {
                "count": 0,
                "merged": True,  # Indicate it was merged via PR
                "closed": False,
            }
        }

        status2, sync_status2, _, _ = keeper._determine_branch_status(
            "feature/override-test", pr_data
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
                "closed": True,  # But was closed
                "number": 73,
            }
        }

        # Test: Determine branch status
        status, _sync_status, pr_status, _notes = keeper._determine_branch_status(
            "feature/closed-unmerged", pr_data
        )

        # Assert: Should NOT be marked as merged
        assert status != BranchStatus.MERGED
        # Should be active (has closed PR but not merged)
        assert status == BranchStatus.ACTIVE
        assert pr_status == "73"

    def test_closed_unmerged_pr_can_still_be_detected_as_merged_via_git(
        self, git_repo, mock_config
    ):
        """A closed PR stays a note when equivalent work reached main another way."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        repo.git.checkout("-b", "feature/closed-pr-merged-via-git")
        test_file = repo_path / "closed_pr_merged_via_git.txt"
        test_file.write_text("Work from a closed PR\n")
        repo.index.add(["closed_pr_merged_via_git.txt"])
        branch_tip = repo.index.commit("Add work from closed PR").hexsha

        repo.git.checkout("main")
        repo.git.cherry_pick(branch_tip)
        repo.git.commit("--amend", "-m", "Integrate closed PR work through another path")

        keeper = BranchKeeper(str(repo_path), mock_config)
        pr_data = {
            "feature/closed-pr-merged-via-git": {
                "count": 0,
                "merged": False,
                "closed": True,
            }
        }

        status, sync_status, _pr_status, notes = keeper._determine_branch_status(
            "feature/closed-pr-merged-via-git", pr_data
        )

        assert status == BranchStatus.MERGED
        assert sync_status == SyncStatus.MERGED_GIT.value
        assert notes == "PR closed without merging"
