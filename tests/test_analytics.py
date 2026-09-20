"""Tests for usage analytics and batch tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.payload import JsonPayload
from monarchmoney import MonarchMoney
from pydantic import JsonValue

import server


class TestUsageAnalytics:
    """Test usage analytics and tracking functionality."""

    @pytest.mark.asyncio
    async def test_track_usage_decorator(self) -> None:
        """Test that usage tracking decorator works correctly."""
        server.usage_patterns.clear()

        mock_client = AsyncMock()
        mock_client.get_accounts.return_value = [{"id": "1", "name": "Test Account"}]

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
                await server.get_accounts()

                assert "get_accounts" in server.usage_patterns
                assert len(server.usage_patterns["get_accounts"]) == 1

                call_info = server.usage_patterns["get_accounts"][0]
                assert call_info["tool_name"] == "get_accounts"
                assert call_info["status"] == "success"
                assert "execution_time" in call_info
                assert "session_id" in call_info

        finally:
            server.mm_client = original_client


class TestBatchTools:
    """Test batch financial analysis."""

    @pytest.mark.asyncio
    async def test_get_complete_financial_overview(self) -> None:
        """Test comprehensive financial overview batch tool."""
        mock_client = AsyncMock()
        mock_client.get_accounts.return_value = [{"id": "1", "name": "Test Account"}]
        mock_client.get_budgets.return_value = [{"category": "Food", "amount": 500}]
        mock_client.get_cashflow.return_value = {"income": 3000, "expenses": 2000}
        mock_client.get_transactions.return_value = [
            {"id": "1", "amount": -50, "category": {"name": "Food"}, "account": {"name": "Checking"}}
        ]
        mock_client.get_transaction_categories.return_value = [{"id": "1", "name": "Food"}]

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
                result = await server.get_complete_financial_overview("this month")

                assert isinstance(result, server.FinancialOverview)
                overview = json.loads(result.model_dump_json())

                assert "accounts" in overview
                assert "budgets" in overview
                assert "cashflow" in overview
                assert "transactions" in overview
                assert "categories" in overview
                assert "transaction_summary" in overview
                assert "batch_metadata" in overview

                summary = overview["transaction_summary"]
                assert summary["total_count"] == 1
                assert summary["total_expenses"] == 50
                assert summary["unique_categories"] == 1

                metadata = overview["batch_metadata"]
                assert metadata["api_calls_made"] == 5
                assert "timestamp" in metadata

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_analyze_spending_patterns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exercise analysis through the real client's JSON request boundary."""
        transactions: list[JsonValue] = [
            {
                "date": "2026-08-15",
                "amount": -100,
                "category": {"name": "Food"},
                "account": {"name": "Synthetic account"},
            },
            {
                "date": "2026-08-20",
                "amount": -50,
                "category": {"name": "Transit"},
                "account": {"name": "Synthetic account"},
            },
            {
                "date": "2026-09-10",
                "amount": 3000,
                "category": {"name": "Salary"},
                "account": {"name": "Synthetic account"},
            },
        ]
        budgets: dict[str, JsonValue] = {
            "budgetData": {
                "monthlyAmountsByCategory": [
                    {
                        "category": {"id": "cat_001"},
                        "monthlyAmounts": [{"month": "2026-08-01", "plannedCashFlowAmount": 200, "actualAmount": 100}],
                    }
                ]
            }
        }
        responses: dict[str, dict[str, JsonValue]] = {
            "GetTransactionsList": {"allTransactions": {"results": transactions, "totalCount": 3}},
            "GetJointPlanningData": budgets,
            "GetAccounts": {"accounts": [{"id": "acc_001", "displayName": "Synthetic account"}]},
            "GetCategories": {"categories": [{"id": "cat_001", "name": "Food"}]},
        }

        async def gql_call(
            operation: str, graphql_query: object, variables: dict[str, object] | None = None
        ) -> dict[str, JsonValue]:
            # Match aiohttp's transport: date objects must fail rather than be
            # accepted silently by a high-level AsyncMock.
            JsonPayload(variables)
            return responses[operation]

        client = MonarchMoney()
        monkeypatch.setattr(client, "gql_call", gql_call)
        monkeypatch.setattr(server, "mm_client", client)

        result = await server.analyze_spending_patterns(lookback_months=3, include_forecasting=True)
        analysis = json.loads(result.model_dump_json())

        assert analysis["monthly_trends"] == {
            "2026-08": {"expenses": 150, "income": 0, "net": -150, "transaction_count": 2},
            "2026-09": {"expenses": 0, "income": 3000, "net": 3000, "transaction_count": 1},
        }
        assert analysis["category_analysis"]["Food"]["total"] == 100
        assert analysis["category_analysis"]["Transit"]["total"] == 50
        assert analysis["account_usage"]["Synthetic account"] == {"total_volume": 3150, "transactions": 3}
        assert analysis["budget_performance"] == budgets
        assert analysis["forecast"]["predicted_expenses"] == 75
        assert analysis["forecast"]["predicted_income"] == 1500
        assert analysis["forecast"]["predicted_net"] == 1425
        assert analysis["errors"] == {}

    @pytest.mark.asyncio
    async def test_batch_error_handling(self) -> None:
        """Test that batch operations handle API errors gracefully."""
        mock_client = AsyncMock()
        mock_client.get_accounts.return_value = [{"id": "1", "name": "Test"}]
        mock_client.get_budgets.side_effect = Exception("Budget API error")
        mock_client.get_cashflow.return_value = {"income": 1000}
        mock_client.get_transactions.return_value = []
        mock_client.get_transaction_categories.return_value = []

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
                result = await server.get_complete_financial_overview("this month")

                assert isinstance(result, server.FinancialOverview)
                overview = json.loads(result.model_dump_json())

                assert "accounts" in overview
                assert isinstance(overview["accounts"], list)

                assert "budgets" in overview
                assert "error" in overview["budgets"]
                assert "Budget API error" in overview["budgets"]["error"]

                assert "cashflow" in overview
                assert overview["cashflow"]["income"] == 1000

        finally:
            server.mm_client = original_client


class TestLoggingConfiguration:
    """Test logging and analytics configuration."""

    def test_analytics_tracking_configured(self) -> None:
        """Test that usage analytics tracking is configured."""
        assert hasattr(server, "current_session_id")
        assert hasattr(server, "usage_patterns")
        assert hasattr(server, "track_usage")

        # Verify session ID is UUID format
        import uuid

        try:
            uuid.UUID(server.current_session_id)
        except ValueError:
            pytest.fail("Session ID is not a valid UUID")


class TestToolCounts:
    """Test batch tool registration."""

    def test_new_batch_tools_available(self) -> None:
        """Check that batch analysis tools are available."""
        new_tools = ["get_complete_financial_overview", "analyze_spending_patterns"]

        for tool_name in new_tools:
            assert hasattr(server, tool_name), f"Tool {tool_name} not found"

        for tool_name in new_tools:
            func = getattr(server, tool_name)
            assert hasattr(func, "__wrapped__"), f"Tool {tool_name} not properly decorated with @track_usage"
