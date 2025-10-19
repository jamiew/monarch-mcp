"""Tests for automatic date filling behavior in build_date_filter."""
import pytest
from datetime import date
from server import build_date_filter


class TestDateAutoFill:
    """Test automatic date filling for incomplete date ranges."""

    def test_start_date_only_auto_fills_end_date(self) -> None:
        """When only start_date is provided, end_date should auto-fill with 'today'."""
        result = build_date_filter("2024-01-01", None)

        assert "start_date" in result
        assert "end_date" in result  # Should be auto-filled
        assert result["start_date"] == "2024-01-01"
        assert result["end_date"] == date.today().isoformat()

    def test_end_date_only_auto_fills_start_date(self) -> None:
        """When only end_date is provided in the past, start_date should auto-fill with that month."""
        result = build_date_filter(None, "2024-12-31")

        assert "start_date" in result  # Should be auto-filled
        assert "end_date" in result
        # Start date should be beginning of the end_date's month (December 2024)
        assert result["start_date"] == "2024-12-01"
        assert result["end_date"] == "2024-12-31"

    def test_both_dates_provided_no_autofill(self) -> None:
        """When both dates are provided, no auto-filling should occur."""
        result = build_date_filter("2024-01-01", "2024-12-31")

        assert result["start_date"] == "2024-01-01"
        assert result["end_date"] == "2024-12-31"

    def test_neither_date_provided_returns_empty(self) -> None:
        """When neither date is provided, should return empty dict."""
        result = build_date_filter(None, None)

        assert result == {}

    def test_natural_language_start_date_auto_fills_end(self) -> None:
        """Natural language start dates should also trigger end_date auto-fill."""
        result = build_date_filter("last month", None)

        assert "start_date" in result
        assert "end_date" in result  # Should be auto-filled with today
        assert result["end_date"] == date.today().isoformat()

    def test_natural_language_end_date_auto_fills_start(self) -> None:
        """Natural language end dates should also trigger start_date auto-fill."""
        result = build_date_filter(None, "today")

        assert "start_date" in result  # Should be auto-filled with this month
        assert "end_date" in result
        today = date.today()
        expected_start = date(today.year, today.month, 1).isoformat()
        assert result["start_date"] == expected_start
        assert result["end_date"] == today.isoformat()

    def test_end_date_in_current_month_uses_this_month(self) -> None:
        """When end_date is in current month, start should be 'this month'."""
        today = date.today()
        # Use a date in the current month
        current_month_date = date(today.year, today.month, 15).isoformat()
        result = build_date_filter(None, current_month_date)

        assert "start_date" in result
        assert "end_date" in result
        expected_start = date(today.year, today.month, 1).isoformat()
        assert result["start_date"] == expected_start
        assert result["end_date"] == current_month_date
