"""Machine-readable JSON serialization for git-branch-keeper.

The JSON output is optimized for agents: explicit status objects, structured
blockers, actionable recommended actions, and no human-table scraping.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from git_branch_keeper.formatters import format_deletion_reason
from git_branch_keeper.models.branch import BranchAnalysisResult, BranchDetails, BranchStatus
from git_branch_keeper.services.branch_validation_service import BranchValidationService

if TYPE_CHECKING:
    from git_branch_keeper.core import BranchKeeper

SCHEMA_VERSION = 3


def _notes(branch: BranchDetails) -> list[str]:
    """Return notes as a stable list instead of nullable presentation text."""
    if not branch.notes:
        return []
    return [line for line in branch.notes.splitlines() if line]


def _changes_checked(branch: BranchDetails) -> bool:
    return (
        branch.modified_files is not None
        and branch.untracked_files is not None
        and branch.staged_files is not None
    )


def _changes(branch: BranchDetails) -> dict[str, Any]:
    """Return explicit file-change status for a branch/worktree row."""
    if not _changes_checked(branch):
        return {
            "checked": False,
            "state": "unknown",
            "reason": "status_check_failed_or_not_available",
        }

    modified = bool(branch.modified_files)
    untracked = bool(branch.untracked_files)
    staged = bool(branch.staged_files)
    dirty = modified or untracked or staged

    return {
        "checked": True,
        "state": "dirty" if dirty else "clean",
        "modified": modified,
        "untracked": untracked,
        "staged": staged,
    }


def _has_uncommitted_changes(branch: BranchDetails) -> bool:
    """Return True if any tracked file-state indicator blocks safe deletion."""
    return (
        branch.modified_files is True
        or branch.untracked_files is True
        or branch.staged_files is True
    )


def _deletion_scope(branch: BranchDetails, delete_remote: bool) -> str:
    """Describe what a branch delete action would affect."""
    if branch.has_remote and delete_remote:
        return "local-and-remote"
    if branch.has_remote:
        return "local-only-remote-kept"
    return "local-only"


def _deletion_reason(branch: BranchDetails) -> str:
    if branch.status in [BranchStatus.STALE, BranchStatus.MERGED]:
        return format_deletion_reason(branch.status)
    return "not_applicable"


def _blocker(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _branch_deletion_blockers(
    branch: BranchDetails, protected_branches: list[str], stale_days: int
) -> list[dict[str, str]]:
    """Return structured reasons a branch row is not branch-deletable."""
    blockers: list[dict[str, str]] = []

    if branch.is_worktree:
        blockers.append(
            _blocker(
                "IS_WORKTREE_ROW",
                "This row represents a worktree, not a local branch ref.",
            )
        )
    if branch.status == BranchStatus.UNSTARTED:
        # Age would be main's commit date here, not the branch's own, so quoting it
        # against stale_days as the generic message does would be meaningless.
        blockers.append(
            _blocker(
                "NO_UNIQUE_COMMITS",
                (
                    "Branch has no commits of its own relative to main, so it was "
                    "never merged and is not a cleanup candidate."
                ),
            )
        )
    elif branch.status not in [BranchStatus.STALE, BranchStatus.MERGED]:
        blockers.append(
            _blocker(
                "NOT_STALE_OR_MERGED",
                (
                    f"Branch status is {branch.status.value}; age is {branch.age_days} days "
                    f"and stale_days is {stale_days}, and the branch is not known merged."
                ),
            )
        )
    if branch.name in protected_branches:
        blockers.append(
            _blocker(
                "PROTECTED_BRANCH",
                "Branch name is listed in protected_branches configuration.",
            )
        )
    if _has_uncommitted_changes(branch):
        dirty_parts = []
        if branch.modified_files:
            dirty_parts.append("modified")
        if branch.untracked_files:
            dirty_parts.append("untracked")
        if branch.staged_files:
            dirty_parts.append("staged")
        blockers.append(
            _blocker(
                "HAS_UNCOMMITTED_CHANGES",
                f"Branch/worktree has uncommitted file state: {', '.join(dirty_parts)}.",
            )
        )
    if branch.in_worktree:
        location = f" at {branch.worktree_path}" if branch.worktree_path else ""
        blockers.append(
            _blocker(
                "CHECKED_OUT_IN_WORKTREE",
                f"Branch is checked out in another worktree{location}.",
            )
        )
    if branch.pr_status and branch.status == BranchStatus.ACTIVE:
        blockers.append(
            _blocker(
                "HAS_OPEN_PULL_REQUEST",
                "Branch has an open pull request and is treated as active.",
            )
        )

    return blockers


def _worktree_removal_blockers(branch: BranchDetails) -> list[dict[str, str]]:
    """Return structured reasons a worktree row is not removable."""
    if not branch.is_worktree:
        return []
    if BranchValidationService.is_worktree_removable(branch):
        return []
    return [
        _blocker(
            "WORKTREE_NOT_ORPHANED_OR_STALE",
            "Worktree is not orphaned and its parent branch is not stale or merged.",
        )
    ]


def _parse_pr_count(value: str | None, prefix: str = "") -> int:
    if not value:
        return 0
    if prefix and value.startswith(prefix):
        value = value[len(prefix) :]
    try:
        return int(value)
    except ValueError:
        return 0


def _attach_pr_details(pr: dict[str, Any], details: dict[str, Any] | None) -> dict[str, Any]:
    """Attach stable PR metadata when available."""
    if not details:
        return pr

    for key in [
        "number",
        "url",
        "head_ref",
        "head_sha",
        "base_ref",
        "merge_commit_sha",
        "merged_at",
        "head_matches_local",
        "local_head_sha",
    ]:
        if key in details and details[key] is not None:
            pr[key] = details[key]
    return pr


def _pr_status(
    branch: BranchDetails, github_enabled: bool, github_disabled_reason: str
) -> dict[str, Any]:
    """Return explicit PR/GitHub status instead of nullable display text."""
    if branch.sync_status == "merged-pr":
        pr: dict[str, Any] = {
            "provider": "github",
            "checked": github_enabled,
            "status": "merged",
            "role": "source",
            "open_count": 0,
            "merged": True,
            "closed_unmerged": False,
        }
        if not github_enabled:
            pr["reason"] = github_disabled_reason
            pr["source"] = "cached_or_previous_analysis"
        return _attach_pr_details(pr, branch.pr_details)

    if not github_enabled:
        return {
            "provider": "github",
            "checked": False,
            "status": "unknown",
            "reason": github_disabled_reason,
        }

    if branch.pr_details and branch.pr_details.get("merged"):
        return _attach_pr_details(
            {
                "provider": "github",
                "checked": True,
                "status": "merged",
                "role": "source",
                "open_count": 0,
                "merged": True,
                "closed_unmerged": False,
            },
            branch.pr_details,
        )

    if branch.notes and "PR closed without merging" in branch.notes:
        return _attach_pr_details(
            {
                "provider": "github",
                "checked": True,
                "status": "closed_unmerged",
                "role": "source",
                "open_count": 0,
                "merged": False,
                "closed_unmerged": True,
            },
            branch.pr_details,
        )

    if branch.pr_status and branch.pr_status.startswith("target:"):
        return _attach_pr_details(
            {
                "provider": "github",
                "checked": True,
                "status": "open",
                "role": "target",
                "open_count": _parse_pr_count(branch.pr_status, "target:"),
                "merged": False,
                "closed_unmerged": False,
            },
            branch.pr_details,
        )

    if branch.pr_status:
        return _attach_pr_details(
            {
                "provider": "github",
                "checked": True,
                "status": "open",
                "role": "source",
                "open_count": _parse_pr_count(branch.pr_status),
                "merged": False,
                "closed_unmerged": False,
            },
            branch.pr_details,
        )

    return {
        "provider": "github",
        "checked": True,
        "status": "none",
        "open_count": 0,
        "merged": False,
        "closed_unmerged": False,
    }


def _merge_detection(branch: BranchDetails) -> dict[str, Any]:
    """Return structured merge-detection details for a branch row."""
    if branch.sync_status == "merged-pr":
        return {
            "merged": True,
            "method": "github_pr",
            "confidence": "provider",
            "matched_commit": None,
            "searched_commits": 0,
            "scan_limit": None,
            "truncated": False,
        }

    if branch.merge_detection:
        return dict(branch.merge_detection)

    return {
        "merged": branch.status == BranchStatus.MERGED,
        "method": "not_checked",
        "confidence": "none",
        "matched_commit": None,
        "searched_commits": 0,
        "scan_limit": None,
        "truncated": False,
    }


def _confidence(
    branch: BranchDetails, github_enabled: bool, comparison: dict[str, Any]
) -> dict[str, Any]:
    uncertainties: list[str] = []

    if not github_enabled:
        uncertainties.append("github_pr_status_not_checked")
    if not _changes_checked(branch):
        uncertainties.append("branch_state_not_checked")
    merge_detection = branch.merge_detection or {}
    if branch.notes and "possible squash-merge" in branch.notes:
        uncertainties.append("possible_squash_merge_requires_human_verification")
    if (
        merge_detection.get("confidence") == "advisory"
        and "possible_squash_merge_requires_human_verification" not in uncertainties
    ):
        uncertainties.append("possible_squash_merge_requires_human_verification")
    if merge_detection.get("truncated"):
        uncertainties.append("squash_merge_scan_was_truncated")
    if branch.notes and "partially merged:" in branch.notes:
        uncertainties.append("branch_partially_applied_to_main")
    if branch.pr_details and branch.pr_details.get("head_matches_local") is False:
        uncertainties.append("local_branch_tip_differs_from_merged_pr_head")
    if not comparison.get("checked", False):
        uncertainties.append("comparison_to_main_not_checked")

    return {
        "level": "medium" if uncertainties else "high",
        "uncertainties": uncertainties,
    }


def _command(argv: list[str]) -> dict[str, Any]:
    """Return both argv and shell forms for an action command."""
    return {"argv": argv, "shell": shlex.join(argv)}


def _branch_to_dict(
    keeper: BranchKeeper,
    branch: BranchDetails,
    protected_branches: list[str],
    delete_remote: bool,
    current_branch: str | None,
) -> dict[str, Any]:
    """Serialize one BranchDetails row to a machine-readable shape."""
    stale_days = int(keeper.min_stale_days)
    github_enabled = keeper.github_service.is_enabled()
    github_disabled_reason = (
        "github_integration_unavailable"
        if getattr(keeper.github_service, "github_token", None)
        else "missing_github_token"
    )
    comparison = keeper.git_service.get_comparison_to_main(branch.name, keeper.main_branch)
    worktree_blockers = _worktree_removal_blockers(branch)

    worktree: dict[str, Any] = {
        "in_worktree": branch.in_worktree,
        "is_worktree": branch.is_worktree,
        "parent_has_orphaned_worktree": branch.worktree_is_orphaned,
        "removable": BranchValidationService.is_worktree_removable(branch),
        "removal_blockers": worktree_blockers,
    }
    if branch.worktree_path:
        worktree["path"] = branch.worktree_path

    return {
        "name": branch.name,
        "kind": "worktree" if branch.is_worktree else "branch",
        "is_current_branch": bool(
            current_branch and branch.name == current_branch and not branch.is_worktree
        ),
        "status": branch.status.value,
        "last_commit_at": keeper.git_service.get_last_commit_at(branch.name),
        "age_days": branch.age_days,
        "remote": {
            "has_remote": branch.has_remote,
            "sync_status": branch.sync_status,
        },
        "pr": _pr_status(branch, github_enabled, github_disabled_reason),
        "changes": _changes(branch),
        "comparison_to_main": comparison,
        "merge_detection": _merge_detection(branch),
        "worktree": worktree,
        "deletion": {
            "deletable": BranchValidationService.is_deletable(branch, protected_branches),
            "reason": _deletion_reason(branch),
            "scope": _deletion_scope(branch, delete_remote),
            "blockers": _branch_deletion_blockers(branch, protected_branches, stale_days),
        },
        "confidence": _confidence(branch, github_enabled, comparison),
        "notes": _notes(branch),
    }


def _summary(analysis: BranchAnalysisResult, protected_branches: list[str]) -> dict[str, int]:
    """Build summary counts for an analysis result."""
    branch_rows = [branch for branch in analysis.branches if not branch.is_worktree]
    worktree_rows = [branch for branch in analysis.branches if branch.is_worktree]

    return {
        "total_rows": len(analysis.branches),
        "branches": len(branch_rows),
        "worktrees": len(worktree_rows),
        "active": sum(1 for branch in branch_rows if branch.status == BranchStatus.ACTIVE),
        "stale": sum(1 for branch in branch_rows if branch.status == BranchStatus.STALE),
        "merged": sum(1 for branch in branch_rows if branch.status == BranchStatus.MERGED),
        "unstarted": sum(1 for branch in branch_rows if branch.status == BranchStatus.UNSTARTED),
        "protected": sum(1 for branch in branch_rows if branch.name in protected_branches),
        "deletable_branches": len(analysis.deletable_branches),
        "removable_worktrees": len(analysis.removable_worktrees),
    }


def _recommended_actions(
    analysis: BranchAnalysisResult, delete_remote: bool, remote_name: str
) -> list[dict[str, Any]]:
    """Return structured actions an agent may choose to apply later."""
    actions: list[dict[str, Any]] = []

    for branch in analysis.deletable_branches:
        reason = format_deletion_reason(branch.status)
        # GBK deletes local refs with force after its own validation because branches
        # can be merged by PR, rebase, cherry-pick, or squash without satisfying
        # `git branch -d` reachability checks.
        commands = [_command(["git", "branch", "-D", branch.name])]
        if delete_remote and branch.has_remote:
            commands.append(_command(["git", "push", remote_name, "--delete", branch.name]))

        actions.append(
            {
                "type": (
                    "delete_local_and_remote_branch"
                    if delete_remote and branch.has_remote
                    else "delete_local_branch"
                ),
                "branch": branch.name,
                "reason": reason,
                "scope": _deletion_scope(branch, delete_remote),
                "destructive": True,
                "requires_confirmation": True,
                "commands": commands,
            }
        )

    for worktree in analysis.removable_worktrees:
        reason = (
            "orphaned"
            if worktree.notes and "[ORPHANED]" in worktree.notes
            else format_deletion_reason(worktree.status)
        )
        commands = []
        if worktree.worktree_path:
            commands.append(_command(["git", "worktree", "remove", worktree.worktree_path]))

        actions.append(
            {
                "type": "remove_worktree",
                "branch": worktree.name,
                "path": worktree.worktree_path or "unknown",
                "reason": reason,
                "destructive": True,
                "requires_confirmation": True,
                "commands": commands,
            }
        )

    return actions


def analysis_to_dict(keeper: BranchKeeper, analysis: BranchAnalysisResult) -> dict[str, Any]:
    """Serialize BranchAnalysisResult for JSON output."""
    protected_branches = list(keeper.protected_branches)
    delete_remote = bool(keeper.delete_remote)
    github_enabled = keeper.github_service.is_enabled()

    github: dict[str, Any] = {
        "enabled": github_enabled,
        "pr_status_checked": github_enabled,
    }
    if analysis.github_base_url:
        github["base_url"] = analysis.github_base_url
    if getattr(keeper.github_service, "github_repo", None):
        github["repository"] = keeper.github_service.github_repo

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "operation": "branch.scan",
        "mode": "read-only",
        "repo": {
            "path": str(keeper.repo.working_dir or keeper.repo_path),
            "main_branch": keeper.main_branch,
            "current_branch": analysis.current_branch or "unknown",
            "remote": keeper.remote_name,
            "github": github,
        },
        "analysis": {
            "complete": analysis.is_complete,
            "cached_count": analysis.cached_count,
            "refreshed_count": analysis.refreshed_count,
            "branches_to_process": analysis.branches_to_process,
        },
        "config": {
            "status_filter": keeper.status_filter,
            "stale_days": keeper.min_stale_days,
            "protected_branches": protected_branches,
            "delete_remote": delete_remote,
            "sort_by": keeper.config.get("sort_by", "age"),
            "sort_order": keeper.config.get("sort_order", "asc"),
            "squash_scan_limit": keeper.config.get("squash_scan_limit", 500),
        },
        "summary": _summary(analysis, protected_branches),
        "branches": [
            _branch_to_dict(
                keeper, branch, protected_branches, delete_remote, analysis.current_branch
            )
            for branch in analysis.branches
        ],
        "recommended_actions": _recommended_actions(analysis, delete_remote, keeper.remote_name),
    }


def schema_to_dict() -> dict[str, Any]:
    """Return a lightweight, versioned schema/capability document for agents."""
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "application": "git-branch-keeper",
        "capabilities": {
            "commands": [
                {
                    "name": "branch.scan",
                    "cli": "git-branch-keeper --output json --cli",
                    "aliases": [
                        "git-branch-keeper --json --cli",
                        "git-branch-keeper --output json --no-interactive",
                    ],
                    "side_effects": "read-only",
                    "description": "Analyze local Git branches and return structured cleanup recommendations.",
                },
                {
                    "name": "schema",
                    "cli": "git-branch-keeper schema --output json",
                    "side_effects": "read-only",
                    "description": "Return machine-readable output schema and command capabilities.",
                },
            ],
            "global_flags": [
                "--json",
                "--output json",
                "--filter {all,stale,merged,unstarted}",
                "--main-branch NAME",
                "--protected BRANCH [BRANCH ...]",
                "--ignore PATTERN [PATTERN ...]",
                "--refresh",
                "--remote",
            ],
        },
        "branch_scan_result": {
            "top_level_fields": [
                "ok",
                "schema_version",
                "operation",
                "mode",
                "repo",
                "analysis",
                "config",
                "summary",
                "branches",
                "recommended_actions",
            ],
            "branch_fields": [
                "name",
                "kind",
                "is_current_branch",
                "status",
                "last_commit_at",
                "age_days",
                "remote",
                "pr",
                "changes",
                "comparison_to_main",
                "merge_detection",
                "worktree",
                "deletion",
                "confidence",
                "notes",
            ],
            "status_values": [status.value for status in BranchStatus],
            "action_types": [
                "delete_local_branch",
                "delete_local_and_remote_branch",
                "remove_worktree",
            ],
            "deletion_blockers": [
                "IS_WORKTREE_ROW",
                "NOT_STALE_OR_MERGED",
                "PROTECTED_BRANCH",
                "HAS_UNCOMMITTED_CHANGES",
                "CHECKED_OUT_IN_WORKTREE",
                "HAS_OPEN_PULL_REQUEST",
            ],
            "blocker_shape": {"code": "STRING_CODE", "message": "Human-readable explanation"},
        },
        "error_shape": {
            "ok": False,
            "error": {
                "code": "STRING_CODE",
                "message": "Human-readable error message",
                "retryable": False,
            },
        },
    }
