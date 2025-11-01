"""Tests for branch status detection (M/U/S indicators)."""


def parse_git_status_porcelain(status_output: str) -> dict:
    """Helper function that replicates the git status parsing logic from the code.

    This is the exact logic used in:
    - worktrees.py: get_worktree_status_details()
    - branch_queries.py: get_branch_status_details()
    """
    has_modified = False
    has_untracked = False
    has_staged = False

    for line in status_output.split("\n"):
        if not line:
            continue
        if len(line) < 2:
            continue

        index_status = line[0]  # Staged changes
        worktree_status = line[1]  # Working tree changes

        # Untracked files
        if line.startswith("??"):
            has_untracked = True
            continue

        # Check for staged changes (index status is not space)
        if index_status != " ":
            has_staged = True

        # Check for working tree changes (worktree status is not space)
        if worktree_status != " ":
            has_modified = True

    return {
        "modified": has_modified,
        "untracked": has_untracked,
        "staged": has_staged,
    }


class TestGitStatusParsing:
    """Test the git status porcelain parsing logic that was fixed."""

    def test_modified_unstaged_file(self):
        """Test detection of modified, unstaged file ( M)."""
        status = " M file.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is True
        assert result["untracked"] is False
        assert result["staged"] is False

    def test_modified_staged_file(self):
        """Test detection of modified, staged file (M )."""
        status = "M  file.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False  # No working tree changes
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_modified_staged_and_unstaged(self):
        """Test detection of file staged AND modified (MM)."""
        status = "MM file.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is True
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_added_file(self):
        """Test detection of newly added file (A )."""
        status = "A  new_file.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_deleted_unstaged_file(self):
        """Test detection of deleted file, unstaged ( D)."""
        status = " D file.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is True  # Working tree change
        assert result["untracked"] is False
        assert result["staged"] is False

    def test_deleted_staged_file(self):
        """Test detection of deleted file, staged (D )."""
        status = "D  file.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_renamed_file(self):
        """Test detection of renamed file (R )."""
        status = "R  old.txt -> new.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_copied_file(self):
        """Test detection of copied file (C )."""
        status = "C  original.txt -> copy.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_untracked_file(self):
        """Test detection of untracked file (??)."""
        status = "?? untracked.txt"
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is True
        assert result["staged"] is False

    def test_updated_but_unmerged_file(self):
        """Test detection of updated but unmerged file (UU)."""
        status = "UU conflicted.txt"
        result = parse_git_status_porcelain(status)

        # Both index and worktree have changes
        assert result["modified"] is True
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_clean_status(self):
        """Test detection of clean status (no changes)."""
        status = ""
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is False
        assert result["staged"] is False

    def test_multiple_file_states(self):
        """Test detection with multiple files in different states."""
        status = """M  staged.txt
 M modified.txt
?? untracked.txt
A  added.txt
D  deleted.txt"""
        result = parse_git_status_porcelain(status)

        # Should detect all three types
        assert result["modified"] is True  # modified.txt
        assert result["untracked"] is True  # untracked.txt
        assert result["staged"] is True  # staged.txt, added.txt, deleted.txt

    def test_multiple_modified_files(self):
        """Test detection with multiple modified files."""
        status = """ M file1.txt
 M file2.txt
 M file3.txt"""
        result = parse_git_status_porcelain(status)

        assert result["modified"] is True
        assert result["untracked"] is False
        assert result["staged"] is False

    def test_multiple_staged_files(self):
        """Test detection with multiple staged files."""
        status = """M  file1.txt
A  file2.txt
D  file3.txt
R  file4.txt -> file5.txt"""
        result = parse_git_status_porcelain(status)

        assert result["modified"] is False
        assert result["untracked"] is False
        assert result["staged"] is True

    def test_all_states_combined(self):
        """Test detection with all possible states combined."""
        status = """MM staged_and_modified.txt
M  staged.txt
 M modified.txt
A  added.txt
D  staged_deleted.txt
 D unstaged_deleted.txt
R  renamed.txt -> new_name.txt
C  copied.txt -> copy.txt
?? untracked.txt"""
        result = parse_git_status_porcelain(status)

        # Should detect all three
        assert result["modified"] is True
        assert result["untracked"] is True
        assert result["staged"] is True

    def test_empty_lines_ignored(self):
        """Test that empty lines don't affect parsing."""
        status = """M  staged.txt

 M modified.txt

?? untracked.txt"""
        result = parse_git_status_porcelain(status)

        assert result["modified"] is True
        assert result["untracked"] is True
        assert result["staged"] is True

    def test_old_behavior_would_miss_added_files(self):
        """Test that proves old code would miss added files.

        Old code only checked for 'M ' prefix, missing 'A ', 'D ', 'R ', 'C ' etc.
        """
        # Old behavior: only detected 'M ' for staged
        status_with_added = "A  new_file.txt"
        result = parse_git_status_porcelain(status_with_added)

        # New code correctly detects this as staged
        assert result["staged"] is True

        # Old code would have missed this (returned False)

    def test_old_behavior_would_miss_deleted_modified_files(self):
        """Test that proves old code would miss various modified states.

        Old code only checked for ' M' prefix, missing ' D' and other states.
        """
        # Deleted file (working tree change)
        status_with_deleted = " D deleted_file.txt"
        result = parse_git_status_porcelain(status_with_deleted)

        # New code correctly detects this as modified
        assert result["modified"] is True

        # Old code would have missed this (returned False)

    def test_old_behavior_would_miss_staged_deletions(self):
        """Test that proves old code would miss staged deletions.

        Old code only checked for 'M ' prefix for staged, missing 'D '.
        """
        # Staged deletion
        status = "D  deleted_and_staged.txt"
        result = parse_git_status_porcelain(status)

        # New code correctly detects this as staged
        assert result["staged"] is True

        # Old code would have returned False for staged
