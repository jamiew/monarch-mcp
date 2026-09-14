"""Tests for FastMCP server implementation."""

from unittest.mock import AsyncMock, patch

import pytest

import server


class TestFastMCPServer:
    """Test the FastMCP server implementation."""

    def test_server_instance_creation(self) -> None:
        """Test that FastMCP server instance is created properly."""
        assert server.mcp is not None
        assert hasattr(server.mcp, "name")

    def test_date_conversion_same_as_old(self) -> None:
        """Convert dates inside nested dictionaries and lists to ISO strings."""
        from datetime import date, datetime

        test_data = {
            "date_field": date(2024, 7, 29),
            "datetime_field": datetime(2024, 7, 29, 10, 30, 45),
            "nested": {"inner_date": date(2024, 7, 29)},
        }

        result = server.convert_dates_to_strings(test_data)

        assert result["date_field"] == "2024-07-29"
        assert result["datetime_field"] == "2024-07-29T10:30:45"
        assert result["nested"]["inner_date"] == "2024-07-29"

    @patch.dict("os.environ", {}, clear=True)
    @pytest.mark.asyncio
    async def test_initialize_client_missing_credentials(self) -> None:
        """Test client initialization fails with missing credentials."""
        with pytest.raises(ValueError, match="MONARCH_EMAIL and MONARCH_PASSWORD"):
            await server.initialize_client()

    @pytest.mark.asyncio
    async def test_get_accounts_no_client(self) -> None:
        """Test get_accounts triggers authentication when client not initialized."""
        original_client = server.mm_client
        original_auth_state = server.auth_state
        server.mm_client = None
        server.auth_state = server.AuthState.NOT_INITIALIZED

        try:
            with patch(
                "server.ensure_authenticated",
                side_effect=ValueError("MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required"),
            ):
                with pytest.raises(ValueError, match="MONARCH_EMAIL and MONARCH_PASSWORD"):
                    await server.get_accounts()
        finally:
            server.mm_client = original_client
            server.auth_state = original_auth_state

    @pytest.mark.asyncio
    async def test_get_accounts_with_mock_client(self) -> None:
        """Test get_accounts with mocked client."""
        # The client wraps accounts alongside householdPreferences.
        mock_client = AsyncMock()
        mock_accounts_list = [
            {"id": "1", "name": "Checking", "balance": 1000.0},
            {"id": "2", "name": "Savings", "balance": 5000.0},
        ]
        mock_client.get_accounts.return_value = {
            "accounts": mock_accounts_list,
            "householdPreferences": {"id": "hp1"},
        }

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_accounts()

            assert isinstance(result, server.AccountsResult)
            assert result.accounts == mock_accounts_list
            assert result.count == len(mock_accounts_list)

            mock_client.get_accounts.assert_called_once()
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_transactions_with_filters(self) -> None:
        """Test get_transactions with date filtering."""
        mock_client = AsyncMock()
        mock_transactions = [
            {"id": "1", "amount": -50.0, "description": "Coffee"},
            {"id": "2", "amount": -25.0, "description": "Lunch"},
        ]
        mock_client.get_transactions.return_value = mock_transactions

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_transactions(
                limit=50,
                start_date="2024-01-01",
                end_date="2024-01-31",
                verbose=True,  # Get full transaction details for testing
            )

            assert isinstance(result, server.TransactionsResult)
            assert result.transactions == mock_transactions
            assert result.count == len(mock_transactions)
            assert result.verbose is True

            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            assert call_args.kwargs["limit"] == 50
            assert "start_date" in call_args.kwargs
            assert "end_date" in call_args.kwargs
        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_transaction(self) -> None:
        """Test create_transaction functionality."""
        mock_client = AsyncMock()
        mock_result = {"id": "new123", "status": "created"}
        mock_client.create_transaction.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            result = await server.create_transaction(
                amount=-45.67,
                merchant_name="Test transaction",
                account_id="acc123",
                date="2024-07-29",
                category_id="cat123",
                notes="Test notes",
            )

            assert isinstance(result, server.TransactionResult)
            assert result.transaction == mock_result

            mock_client.create_transaction.assert_called_once()
            call_args = mock_client.create_transaction.call_args
            assert call_args.kwargs["amount"] == -45.67
            assert call_args.kwargs["merchant_name"] == "Test transaction"
            assert call_args.kwargs["account_id"] == "acc123"
            assert call_args.kwargs["notes"] == "Test notes"
            assert call_args.kwargs["date"] == "2024-07-29"

        server.mm_client = original_client


class TestFastMCPComparisionWithOld:
    """Check core tool availability and parameter names."""

    def test_function_signatures_correct(self) -> None:
        """Test that function signatures are properly defined."""
        import inspect

        sig = inspect.signature(server.get_transactions)
        params = list(sig.parameters.keys())
        assert "limit" in params
        assert "offset" in params
        assert "start_date" in params
        assert "end_date" in params

        sig = inspect.signature(server.create_transaction)
        params = list(sig.parameters.keys())
        assert "amount" in params
        assert "merchant_name" in params
        assert "account_id" in params
        assert "date" in params
        assert "category_id" in params
