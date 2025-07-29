"""Tests for FastMCP validation and parameter handling."""

import pytest
from unittest.mock import AsyncMock
import json

import server


class TestFastMCPParameterValidation:
    """Test that FastMCP functions handle parameters correctly."""
    
    @pytest.mark.asyncio
    async def test_get_transactions_with_valid_parameters(self) -> None:
        """Test get_transactions with various valid parameter combinations."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_transactions = [{"id": "1", "amount": -50.0}]
        mock_client.get_transactions.return_value = mock_transactions
        
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            # Test with all parameters
            result = await server.get_transactions(
                limit=50,
                offset=10,
                start_date="2024-01-01",
                end_date="2024-01-31",
                account_id="acc123",
                category_id="cat456"
            )
            
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_transactions
            
            # Verify mock was called with correct parameters
            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            assert call_args.kwargs["limit"] == 50
            assert call_args.kwargs["offset"] == 10
            
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_transactions_with_defaults(self) -> None:
        """Test get_transactions uses default values correctly."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_transactions = [{"id": "1", "amount": -50.0}]
        mock_client.get_transactions.return_value = mock_transactions
        
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            # Test with no parameters (should use defaults)
            result = await server.get_transactions()
            
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_transactions
            
            # Verify defaults were used
            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            assert call_args.kwargs["limit"] == 100  # default
            assert call_args.kwargs["offset"] == 0   # default
            
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio 
    async def test_create_transaction_with_required_parameters(self) -> None:
        """Test create_transaction with all required parameters."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_result = {"id": "new123", "status": "created"}
        mock_client.create_transaction.return_value = mock_result
        
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            result = await server.create_transaction(
                amount=-45.67,
                description="Test transaction",
                account_id="acc123",
                date="2024-07-29"
            )
            
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_result
            
            # Verify parameters were passed correctly
            mock_client.create_transaction.assert_called_once()
            call_args = mock_client.create_transaction.call_args
            assert call_args.kwargs["amount"] == -45.67
            assert call_args.kwargs["description"] == "Test transaction"
            assert call_args.kwargs["account_id"] == "acc123"
            # Date should be converted to date object
            assert hasattr(call_args.kwargs["date"], "year")
            
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_transaction_with_optional_parameters(self) -> None:
        """Test create_transaction with optional parameters."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_result = {"id": "new123", "status": "created"}
        mock_client.create_transaction.return_value = mock_result
        
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            result = await server.create_transaction(
                amount=-45.67,
                description="Test transaction",
                account_id="acc123", 
                date="2024-07-29",
                category_id="cat456",
                notes="Test notes"
            )
            
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_result
            
            # Verify optional parameters were passed
            mock_client.create_transaction.assert_called_once()
            call_args = mock_client.create_transaction.call_args
            assert call_args.kwargs["category_id"] == "cat456"
            assert call_args.kwargs["notes"] == "Test notes"
            
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_update_transaction_with_partial_updates(self) -> None:
        """Test update_transaction with only some fields updated."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_result = {"id": "txn123", "status": "updated"}
        mock_client.update_transaction.return_value = mock_result
        
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            result = await server.update_transaction(
                transaction_id="txn123",
                amount=-100.0,
                description="Updated description"
                # Other fields left as None (default)
            )
            
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_result
            
            # Verify only specified fields were passed
            mock_client.update_transaction.assert_called_once()
            call_args = mock_client.update_transaction.call_args
            assert call_args.kwargs["transaction_id"] == "txn123"
            assert call_args.kwargs["amount"] == -100.0
            assert call_args.kwargs["description"] == "Updated description"
            # category_id, date, notes should not be in kwargs since they're None
            assert "category_id" not in call_args.kwargs
            assert "date" not in call_args.kwargs  
            assert "notes" not in call_args.kwargs
            
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_date_parameter_conversion(self) -> None:
        """Test that date string parameters are converted to date objects."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_transactions = [{"id": "1", "amount": -50.0}]
        mock_client.get_transactions.return_value = mock_transactions
        
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            await server.get_transactions(
                start_date="2024-01-01",
                end_date="2024-12-31"
            )
            
            # Verify dates were converted to date objects
            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            
            start_date = call_args.kwargs["start_date"]
            end_date = call_args.kwargs["end_date"]
            
            # Should be ISO date strings for JSON serialization safety
            assert isinstance(start_date, str)
            assert isinstance(end_date, str)
            assert start_date == "2024-01-01"
            assert end_date == "2024-12-31"
            
        finally:
            server.mm_client = original_client


class TestPydanticModelsStillExist:
    """Test that Pydantic models are still available for reference."""
    
    def test_pydantic_models_exist(self) -> None:
        """Test that Pydantic model classes still exist."""
        # These models provide type information even if not used directly
        assert hasattr(server, 'GetTransactionsArgs')
        assert hasattr(server, 'GetBudgetsArgs')
        assert hasattr(server, 'GetCashflowArgs')
        assert hasattr(server, 'CreateTransactionArgs')
        assert hasattr(server, 'UpdateTransactionArgs')
        
    def test_pydantic_model_validation_still_works(self) -> None:
        """Test that Pydantic models can still validate data if needed."""
        # Valid data should validate
        valid_data = {
            "limit": 100,
            "offset": 0,
            "start_date": "2024-01-01"
        }
        
        args = server.GetTransactionsArgs.model_validate(valid_data)
        assert args.limit == 100
        assert args.offset == 0
        assert args.start_date == "2024-01-01"
        
        # Invalid data should raise ValidationError
        from pydantic import ValidationError
        invalid_data = {"limit": -1}  # Invalid: limit must be >= 1
        
        with pytest.raises(ValidationError):
            server.GetTransactionsArgs.model_validate(invalid_data)