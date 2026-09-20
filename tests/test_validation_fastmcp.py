"""Tests for FastMCP validation and parameter handling."""

from unittest.mock import AsyncMock, patch

import pytest

import server


class TestFastMCPParameterValidation:
    """Test that FastMCP functions handle parameters correctly."""

    @pytest.mark.asyncio
    async def test_get_transactions_with_valid_parameters(self) -> None:
        """Test get_transactions with various valid parameter combinations."""
        mock_client = AsyncMock()
        mock_transactions = [{"id": "1", "amount": -50.0}]
        mock_client.get_transactions.return_value = mock_transactions

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_transactions(
                limit=50,
                offset=10,
                start_date="2024-01-01",
                end_date="2024-01-31",
                account_id="acc123",
                category_id="cat456",
                verbose=True,  # Get full transaction details for testing
            )

            assert isinstance(result, server.TransactionsResult)
            assert result.transactions == mock_transactions

            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            assert call_args.kwargs["limit"] == 50
            assert call_args.kwargs["offset"] == 10

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_transactions_with_defaults(self) -> None:
        """Test get_transactions uses default values correctly."""
        mock_client = AsyncMock()
        mock_transactions = [{"id": "1", "amount": -50.0}]
        mock_client.get_transactions.return_value = mock_transactions

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_transactions(verbose=True)  # Get full transaction details for testing

            assert isinstance(result, server.TransactionsResult)
            assert result.transactions == mock_transactions

            mock_client.get_transactions.assert_called_once()
            call_args = mock_client.get_transactions.call_args
            assert call_args.kwargs["limit"] == 100  # default
            assert call_args.kwargs["offset"] == 0  # default

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_transaction_with_required_parameters(self) -> None:
        """Test create_transaction with all required parameters."""
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
            )

            assert isinstance(result, server.TransactionResult)
            assert result.transaction == mock_result

            mock_client.create_transaction.assert_called_once()
            call_args = mock_client.create_transaction.call_args
            assert call_args.kwargs["amount"] == -45.67
            assert call_args.kwargs["merchant_name"] == "Test transaction"
            assert call_args.kwargs["account_id"] == "acc123"
            assert call_args.kwargs["date"] == "2024-07-29"

        server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_transaction_with_optional_parameters(self) -> None:
        """Test create_transaction with optional parameters."""
        mock_client = AsyncMock()
        mock_result = {"id": "new123", "status": "created"}
        mock_client.create_transaction.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.create_transaction(
                amount=-45.67,
                merchant_name="Test transaction",
                account_id="acc123",
                date="2024-07-29",
                category_id="cat456",
                notes="Test notes",
            )

            assert isinstance(result, server.TransactionResult)
            assert result.transaction == mock_result

            mock_client.create_transaction.assert_called_once()
            call_args = mock_client.create_transaction.call_args
            assert call_args.kwargs["category_id"] == "cat456"
            assert call_args.kwargs["notes"] == "Test notes"

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_update_transaction_with_partial_updates(self) -> None:
        """Test update_transaction with only some fields updated."""
        mock_client = AsyncMock()
        mock_result = {"updateTransaction": {"errors": [], "transaction": {"id": "txn123", "amount": -100.0}}}
        mock_client.update_transaction.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.update_transaction(
                transaction_id="txn123",
                amount=-100.0,
                merchant_name="Updated merchant",
            )

            assert isinstance(result, server.TransactionResult)
            assert result.transaction == mock_result

            mock_client.update_transaction.assert_called_once()
            call_args = mock_client.update_transaction.call_args
            assert call_args.kwargs["transaction_id"] == "txn123"
            assert call_args.kwargs["amount"] == -100.0
            assert call_args.kwargs["merchant_name"] == "Updated merchant"
            assert "category_id" not in call_args.kwargs
            assert "date" not in call_args.kwargs
            assert "notes" not in call_args.kwargs

        finally:
            server.mm_client = original_client
