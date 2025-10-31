"""Date and time formatting utilities."""

from typing import Any


def format_date(date: Any) -> str:
    """
    Format a date object to YYYY-MM-DD string.

    Args:
        date: Date object (datetime or string)

    Returns:
        Formatted date string
    """
    if hasattr(date, "strftime"):
        return date.strftime("%Y-%m-%d")
    return str(date)


def format_age(age_days: int) -> str:
    """
    Format age in days.

    Args:
        age_days: Number of days

    Returns:
        Formatted age string
    """
    return f"{age_days}d"
