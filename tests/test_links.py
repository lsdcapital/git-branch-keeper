"""Tests for terminal link formatting."""

from rich.style import Style

from git_branch_keeper.formatters.links import format_pr_link, format_pr_text


def test_pr_number_is_clickable_and_prefixed() -> None:
    assert (
        format_pr_link("42", "https://github.com/acme/repo")
        == "[link=https://github.com/acme/repo/pull/42]#42[/link]"
    )


def test_pr_number_is_still_identifiable_without_github_url() -> None:
    assert format_pr_link("42", None) == "#42"


def test_target_pr_count_keeps_aggregate_link() -> None:
    assert (
        format_pr_link("target:3", "https://github.com/acme/repo")
        == "[link=https://github.com/acme/repo/pulls]3[/link]"
    )


def test_tui_pr_text_carries_native_link_and_click_action() -> None:
    url = "https://github.com/acme/repo/pull/42"
    pr_text = format_pr_text("42", "https://github.com/acme/repo")

    assert pr_text.plain == "#42"
    assert isinstance(pr_text.style, Style)
    assert pr_text.style.link == url
    assert pr_text.style.meta["@click"] == ("app.open_pr", (url,))
