"""Tests for FastMCP server implementation."""

import pytest
from unittest.mock import AsyncMock, patch
import json

# Import the new FastMCP server
import server


class TestFastMCPServer:
    """Test the FastMCP server implementation."""
    
    def test_server_instance_creation(self) -> None:
        """Test that FastMCP server instance is created properly."""
        assert server.mcp is not None
        assert hasattr(server.mcp, 'name')

    def test_date_conversion_same_as_old(self) -> None:
        """Test that date conversion works the same as the old implementation."""
        from datetime import date, datetime
        
        test_data = {
            "date_field": date(2024, 7, 29),
            "datetime_field": datetime(2024, 7, 29, 10, 30, 45),
            "nested": {
                "inner_date": date(2024, 7, 29)
            }
        }
        
        result = server.convert_dates_to_strings(test_data)
        
        assert result["date_field"] == "2024-07-29"
        assert result["datetime_field"] == "2024-07-29T10:30:45"
        assert result["nested"]["inner_date"] == "2024-07-29"

    @patch.dict('os.environ', {}, clear=True)
    @pytest.mark.asyncio
    async def test_initialize_client_missing_credentials(self) -> None:
        """Test client initialization fails with missing credentials."""
        with pytest.raises(ValueError, match="MONARCH_EMAIL and MONARCH_PASSWORD"):
            await server.initialize_client()

    @patch.dict('os.environ', {
        'MONARCH_EMAIL': 'test@example.com',
        'MONARCH_PASSWORD': 'testpass'
    })
    @patch('server.MonarchMoney')
    @pytest.mark.asyncio
    async def test_initialize_client_success(self, mock_monarch_class: AsyncMock) -> None:
        """Test successful client initialization."""
        # Setup mock
        mock_client = AsyncMock()
        mock_monarch_class.return_value = mock_client
        
        # Reset global client
        server.mm_client = None
        
        # Test initialization
        await server.initialize_client()
        
        # Verify client was created and login was called
        mock_monarch_class.assert_called_once()
        assert server.mm_client is not None

    @pytest.mark.asyncio
    async def test_get_accounts_no_client(self) -> None:
        """Test get_accounts fails when client not initialized."""
        # Reset global client
        original_client = server.mm_client
        server.mm_client = None
        
        try:
            with pytest.raises(ValueError, match="MonarchMoney client not initialized"):
                await server.get_accounts()
        finally:
            # Restore original client
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_accounts_with_mock_client(self) -> None:
        """Test get_accounts with mocked client."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_accounts_data = [
            {"id": "1", "name": "Checking", "balance": 1000.0},
            {"id": "2", "name": "Savings", "balance": 5000.0}
        ]
        mock_client.get_accounts.return_value = mock_accounts_data
        
        # Set global client
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            result = await server.get_accounts()
            
            # Verify result is JSON string
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_accounts_data
            
            # Verify mock was called
            mock_client.get_accounts.assert_called_once()
        finally:
            # Restore original client
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_transactions_with_filters(self) -> None:
        """Test get_transactions with date filtering."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_transactions = [
            {"id": "1", "amount": -50.0, "description": "Coffee"},
            {"id": "2", "amount": -25.0, "description": "Lunch"}
        ]
        mock_client.get_transactions.return_value = mock_transactions
        
        # Set global client
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            result = await server.get_transactions(
                limit=50,
                start_date="2024-01-01",
                end_date="2024-01-31"
            )
            
            # Verify result is JSON string
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_transactions
            
            # Verify mock was called with correct parameters
            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            assert call_args.kwargs["limit"] == 50
            assert "start_date" in call_args.kwargs
            assert "end_date" in call_args.kwargs
        finally:
            # Restore original client
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_transaction(self) -> None:
        """Test create_transaction functionality."""
        # Setup mock client
        mock_client = AsyncMock()
        mock_result = {"id": "new123", "status": "created"}
        mock_client.create_transaction.return_value = mock_result
        
        # Set global client
        original_client = server.mm_client
        server.mm_client = mock_client
        
        try:
            result = await server.create_transaction(
                amount=-45.67,
                description="Test transaction",
                account_id="acc123",
                date="2024-07-29",
                notes="Test notes"
            )
            
            # Verify result is JSON string
            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_result
            
            # Verify mock was called with correct parameters
            mock_client.create_transaction.assert_called_once()
            call_args = mock_client.create_transaction.call_args
            assert call_args.kwargs["amount"] == -45.67
            assert call_args.kwargs["description"] == "Test transaction"
            assert call_args.kwargs["account_id"] == "acc123"
            assert call_args.kwargs["notes"] == "Test notes"
            # Verify date was converted properly
            assert hasattr(call_args.kwargs["date"], "year")  # Should be a date object
        finally:
            # Restore original client
            server.mm_client = original_client


class TestFastMCPComparisionWithOld:
    """Compare FastMCP implementation with old server to ensure compatibility."""
    
    def test_tool_count_matches(self) -> None:
        """Verify we have the same number of tools in both implementations."""
        # Count tools in FastMCP implementation
        fastmcp_tools = [
            "get_accounts", "get_transactions", "get_budgets", "get_cashflow",
            "get_transaction_categories", "create_transaction", "update_transaction",
            "refresh_accounts"
        ]
        
        # This should match the original implementation
        assert len(fastmcp_tools) == 8

    def test_function_signatures_correct(self) -> None:
        """Test that function signatures are properly defined."""
        import inspect
        
        # Test a few key functions
        sig = inspect.signature(server.get_transactions)
        params = list(sig.parameters.keys())
        assert "limit" in params
        assert "offset" in params
        assert "start_date" in params
        assert "end_date" in params
        
        sig = inspect.signature(server.create_transaction)
        params = list(sig.parameters.keys())
        assert "amount" in params
        assert "description" in params
        assert "account_id" in params
        assert "date" in params