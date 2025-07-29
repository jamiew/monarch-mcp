"""Tests for Pydantic validation and type safety improvements."""

import pytest
from pydantic import ValidationError

import server


class TestPydanticValidation:
    """Test Pydantic model validation for tool arguments."""
    
    def test_get_transactions_args_valid(self) -> None:
        """Test valid get_transactions arguments."""
        args = {
            "limit": 50,
            "offset": 10,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "account_id": "12345"
        }
        
        parsed = server.parse_tool_arguments("get_transactions", args)
        assert isinstance(parsed, server.GetTransactionsArgs)
        assert parsed.limit == 50
        assert parsed.offset == 10
        assert parsed.start_date == "2024-01-01"
        assert parsed.end_date == "2024-01-31"
        assert parsed.account_id == "12345"

    def test_get_transactions_args_defaults(self) -> None:
        """Test get_transactions with default values."""
        args = {}
        
        parsed = server.parse_tool_arguments("get_transactions", args)
        assert isinstance(parsed, server.GetTransactionsArgs)
        assert parsed.limit == 100  # default
        assert parsed.offset == 0   # default
        assert parsed.start_date is None
        assert parsed.end_date is None

    def test_get_transactions_args_invalid_date(self) -> None:
        """Test get_transactions with invalid date format."""
        args = {"start_date": "invalid-date"}
        
        with pytest.raises(ValidationError) as exc_info:
            server.parse_tool_arguments("get_transactions", args)
        
        error = exc_info.value
        assert "start_date" in str(error)

    def test_get_transactions_args_invalid_limit(self) -> None:
        """Test get_transactions with invalid limit."""
        args = {"limit": -1}
        
        with pytest.raises(ValidationError) as exc_info:
            server.parse_tool_arguments("get_transactions", args)
        
        error = exc_info.value
        assert "limit" in str(error)

    def test_create_transaction_args_valid(self) -> None:
        """Test valid create_transaction arguments."""
        args = {
            "amount": -45.67,
            "description": "Grocery shopping",
            "account_id": "acc123",
            "date": "2024-07-29",
            "notes": "Weekly groceries"
        }
        
        parsed = server.parse_tool_arguments("create_transaction", args)
        assert isinstance(parsed, server.CreateTransactionArgs)
        assert parsed.amount == -45.67
        assert parsed.description == "Grocery shopping"
        assert parsed.account_id == "acc123"
        assert parsed.date == "2024-07-29"
        assert parsed.notes == "Weekly groceries"

    def test_create_transaction_args_missing_required(self) -> None:
        """Test create_transaction with missing required fields."""
        args = {"amount": 100.0}  # Missing description, account_id, date
        
        with pytest.raises(ValidationError) as exc_info:
            server.parse_tool_arguments("create_transaction", args)
        
        error = exc_info.value
        assert "description" in str(error)
        assert "account_id" in str(error)
        assert "date" in str(error)

    def test_tools_with_no_arguments(self) -> None:
        """Test tools that accept no arguments."""
        for tool_name in ["get_accounts", "get_transaction_categories", "refresh_accounts"]:
            parsed = server.parse_tool_arguments(tool_name, {})
            assert parsed == {}

    def test_budgets_args_validation(self) -> None:
        """Test budget arguments validation."""
        args = {"start_date": "2024-01-01", "end_date": "2024-12-31"}
        
        parsed = server.parse_tool_arguments("get_budgets", args)
        assert isinstance(parsed, server.GetBudgetsArgs)
        assert parsed.start_date == "2024-01-01"
        assert parsed.end_date == "2024-12-31"


class TestTypeDefinitions:
    """Test the new type definitions work correctly."""
    
    def test_date_conversion_with_typed_data(self) -> None:
        """Test date conversion preserves type safety."""
        from datetime import date, datetime
        
        # Test with properly typed data
        test_data = {
            "string_field": "test",
            "number_field": 42,
            "date_field": date(2024, 7, 29),
            "datetime_field": datetime(2024, 7, 29, 10, 30, 45),
            "nested": {
                "inner_date": date(2024, 7, 29),
                "inner_list": [date(2024, 7, 29), "string", 123]
            }
        }
        
        result = server.convert_dates_to_strings(test_data)
        
        # Verify types and values
        assert isinstance(result, dict)
        assert result["string_field"] == "test"
        assert result["number_field"] == 42
        assert result["date_field"] == "2024-07-29"
        assert result["datetime_field"] == "2024-07-29T10:30:45"
        assert isinstance(result["nested"], dict)
        assert result["nested"]["inner_date"] == "2024-07-29"
        assert result["nested"]["inner_list"] == ["2024-07-29", "string", 123]