"""Tests for cache persistence during refresh operations."""

from pathlib import Path

from git_branch_keeper.core.branch_keeper import BranchKeeper


class TestCacheRefreshPersistence:
    """Test that cache is updated correctly during refresh operations."""

    def test_cache_saved_after_refresh(self, git_repo, mock_config):
        """Test that cache is saved after refresh, preserving fresh data."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create a test file to make the branch "dirty"
        test_file = repo_path / "uncommitted.txt"
        test_file.write_text("uncommitted content\n")

        # Create BranchKeeper with refresh=False (normal mode)
        config = mock_config.copy()
        config["refresh"] = False
        keeper = BranchKeeper(str(repo_path), config)

        # Clear any existing cache first
        keeper.cache_service.clear_cache()

        # First pass: Get branch details (should detect uncommitted file)
        branches = keeper.get_branch_details(show_progress=False)

        # Find main branch
        main_branch = next((b for b in branches if b.name == "main"), None)
        assert main_branch is not None

        # Should have untracked file
        assert main_branch.untracked_files is True, "Should detect untracked file on first pass"

        # Verify cache was saved
        cached_branches = keeper.cache_service.load_cache()
        assert "main" in cached_branches, "Main branch should be in cache"
        assert (
            cached_branches["main"]["untracked_files"] is True
        ), "Cache should have untracked=True"

        # Now enable refresh mode and call again
        keeper.config.refresh = True

        # Second pass: Refresh (should still detect uncommitted file and update cache)
        branches_refreshed = keeper.get_branch_details(show_progress=False)

        # Find main branch again
        main_branch_refreshed = next((b for b in branches_refreshed if b.name == "main"), None)
        assert main_branch_refreshed is not None

        # Should still have untracked file
        assert (
            main_branch_refreshed.untracked_files is True
        ), "Should detect untracked file after refresh"

        # Verify cache was updated (this is the key test)
        cached_branches_after_refresh = keeper.cache_service.load_cache()
        assert (
            "main" in cached_branches_after_refresh
        ), "Main branch should be in cache after refresh"
        assert (
            cached_branches_after_refresh["main"]["untracked_files"] is True
        ), "Cache should STILL have untracked=True after refresh"

    def test_cache_updated_when_file_status_changes(self, git_repo, mock_config):
        """Test that cache reflects changed file status after refresh."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        config = mock_config.copy()
        config["refresh"] = False
        keeper = BranchKeeper(str(repo_path), config)

        # Clear cache
        keeper.cache_service.clear_cache()

        # First pass: Clean state
        branches1 = keeper.get_branch_details(show_progress=False)
        main1 = next((b for b in branches1 if b.name == "main"), None)

        # Should be clean
        assert main1.untracked_files is False, "Should be clean initially"

        # Verify cache
        cache1 = keeper.cache_service.load_cache()
        assert cache1["main"]["untracked_files"] is False, "Cache should show clean"

        # NOW add an uncommitted file
        test_file = repo_path / "new_file.txt"
        test_file.write_text("new content\n")

        # Enable refresh and get details again
        keeper.config.refresh = True
        branches2 = keeper.get_branch_details(show_progress=False)
        main2 = next((b for b in branches2 if b.name == "main"), None)

        # Should detect the new file
        assert main2.untracked_files is True, "Should detect new untracked file"

        # KEY TEST: Cache should be updated with new status
        cache2 = keeper.cache_service.load_cache()
        assert (
            cache2["main"]["untracked_files"] is True
        ), "Cache should be updated to show untracked file"

    def test_cache_always_saved_regardless_of_use_cache_flag(self, git_repo, mock_config):
        """Test that cache is saved whether use_cache is True or False."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Test with refresh=False (use_cache=True)
        config1 = mock_config.copy()
        config1["refresh"] = False
        keeper1 = BranchKeeper(str(repo_path), config1)

        # Clear cache first
        keeper1.cache_service.clear_cache()

        keeper1.get_branch_details(show_progress=False)
        cache1 = keeper1.cache_service.load_cache()
        assert len(cache1) > 0, "Cache should be saved when use_cache=True"

        # Clear cache
        keeper1.cache_service.clear_cache()
        assert len(keeper1.cache_service.load_cache()) == 0, "Cache should be cleared"

        # Test with refresh=True (use_cache=False)
        config2 = mock_config.copy()
        config2["refresh"] = True
        keeper2 = BranchKeeper(str(repo_path), config2)

        keeper2.get_branch_details(show_progress=False)
        cache2 = keeper2.cache_service.load_cache()
        assert (
            len(cache2) > 0
        ), "Cache should ALSO be saved when use_cache=False (this was the bug!)"

    def test_file_status_preserved_across_cache_operations(self, git_repo, mock_config):
        """Test that file status (M/U/S) is preserved through cache save/load cycles."""
        repo = git_repo
        repo_path = Path(repo.working_dir)

        # Create files in different states
        # Untracked file
        (repo_path / "untracked.txt").write_text("untracked\n")

        # Modified file
        readme = repo_path / "README.md"
        readme.write_text(readme.read_text() + "\nmodified\n")

        # Staged file
        staged_file = repo_path / "staged.txt"
        staged_file.write_text("staged content\n")
        repo.index.add(["staged.txt"])

        config = mock_config.copy()
        config["refresh"] = True  # Force fresh check
        keeper = BranchKeeper(str(repo_path), config)

        # Clear cache
        keeper.cache_service.clear_cache()

        # Get branch details
        branches = keeper.get_branch_details(show_progress=False)
        main = next((b for b in branches if b.name == "main"), None)

        # Should detect all file types
        assert main.untracked_files is True, "Should detect untracked file"
        assert main.modified_files is True, "Should detect modified file"
        assert main.staged_files is True, "Should detect staged file"

        # Verify cache has correct values
        cache = keeper.cache_service.load_cache()
        assert cache["main"]["untracked_files"] is True
        assert cache["main"]["modified_files"] is True
        assert cache["main"]["staged_files"] is True

        # Load from cache (simulate next run without refresh)
        config2 = mock_config.copy()
        config2["refresh"] = False  # Use cache
        keeper2 = BranchKeeper(str(repo_path), config2)

        branches2 = keeper2.get_branch_details(show_progress=False)
        main2 = next((b for b in branches2 if b.name == "main"), None)

        # Status should be preserved from cache
        assert main2.untracked_files is True, "Untracked status should be preserved"
        assert main2.modified_files is True, "Modified status should be preserved"
        assert main2.staged_files is True, "Staged status should be preserved"
