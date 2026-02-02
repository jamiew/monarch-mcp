"""Tests for new Monarch Money API tools."""

import json
from unittest.mock import AsyncMock

import pytest

import server


class TestNewMonarchTools:
    """Test newly added Monarch Money API tools."""

    @pytest.mark.asyncio
    async def test_get_account_holdings(self) -> None:
        """Test get_account_holdings functionality."""
        mock_client = AsyncMock()
        mock_holdings = [
            {"symbol": "AAPL", "shares": 100, "value": 15000},
            {"symbol": "GOOGL", "shares": 50, "value": 12500},
        ]
        mock_client.get_account_holdings.return_value = mock_holdings

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_account_holdings()

            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_holdings
            mock_client.get_account_holdings.assert_called_once()

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_account_history(self) -> None:
        """Test get_account_history with date filtering."""
        mock_client = AsyncMock()
        mock_history = [{"date": "2024-01-01", "balance": 1000}, {"date": "2024-01-02", "balance": 1050}]
        mock_client.get_account_history.return_value = mock_history

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_account_history(
                account_id="acc123", start_date="2024-01-01", end_date="2024-01-31"
            )

            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_history

            # Verify call parameters
            mock_client.get_account_history.assert_called_once()
            call_args = mock_client.get_account_history.call_args
            assert call_args.kwargs["account_id"] == "acc123"
            assert "start_date" in call_args.kwargs
            assert "end_date" in call_args.kwargs

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_institutions(self) -> None:
        """Test get_institutions functionality."""
        mock_client = AsyncMock()
        mock_institutions = [{"id": "inst1", "name": "Chase Bank"}, {"id": "inst2", "name": "Wells Fargo"}]
        mock_client.get_institutions.return_value = mock_institutions

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_institutions()

            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_institutions
            mock_client.get_institutions.assert_called_once()

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_get_recurring_transactions(self) -> None:
        """Test get_recurring_transactions functionality."""
        mock_client = AsyncMock()
        mock_recurring = [
            {"id": "rec1", "amount": -500, "description": "Rent"},
            {"id": "rec2", "amount": 3000, "description": "Salary"},
        ]
        mock_client.get_recurring_transactions.return_value = mock_recurring

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_recurring_transactions()

            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_recurring
            mock_client.get_recurring_transactions.assert_called_once()

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_set_budget_amount(self) -> None:
        """Test set_budget_amount functionality."""
        mock_client = AsyncMock()
        mock_result = {"category_id": "cat123", "amount": 500, "status": "updated"}
        mock_client.set_budget_amount.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.set_budget_amount(category_id="cat123", amount=500.0)

            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_result

            # Verify parameters
            mock_client.set_budget_amount.assert_called_once()
            call_args = mock_client.set_budget_amount.call_args
            assert call_args.kwargs["category_id"] == "cat123"
            assert call_args.kwargs["amount"] == 500.0

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_manual_account(self) -> None:
        """Test create_manual_account functionality."""
        mock_client = AsyncMock()
        mock_result = {"id": "acc456", "name": "My Savings", "type": "savings"}
        mock_client.create_manual_account.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.create_manual_account(
                account_name="My Savings", account_type="savings", balance=1000.0
            )

            assert isinstance(result, str)
            parsed_result = json.loads(result)
            assert parsed_result == mock_result

            # Verify parameters
            mock_client.create_manual_account.assert_called_once()
            call_args = mock_client.create_manual_account.call_args
            assert call_args.kwargs["account_name"] == "My Savings"
            assert call_args.kwargs["account_type"] == "savings"
            assert call_args.kwargs["balance"] == 1000.0

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_error_handling_in_new_tools(self) -> None:
        """Test that new tools properly handle and log errors."""
        mock_client = AsyncMock()
        mock_client.get_account_holdings.side_effect = Exception("API Error")

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            with pytest.raises(Exception, match="API Error"):
                await server.get_account_holdings()

            mock_client.get_account_holdings.assert_called_once()

        finally:
            server.mm_client = original_client


class TestToolCounts:
    """Test that we have the expected number of tools."""

    def test_all_tools_available(self) -> None:
        """Test that all expected tools are available."""
        expected_tools = [
            "get_accounts",
            "get_transactions",
            "get_budgets",
            "get_cashflow",
            "get_transaction_categories",
            "create_transaction",
            "update_transaction",
            "refresh_accounts",
            "get_account_holdings",
            "get_account_history",
            "get_institutions",
            "get_recurring_transactions",
            "set_budget_amount",
            "create_manual_account",
        ]

        for tool_name in expected_tools:
            assert hasattr(server, tool_name), f"Tool {tool_name} not found"

        # Should have 14 tools total now
        assert len(expected_tools) == 14
