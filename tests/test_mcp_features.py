"""Tests for MCP resources and prompts."""

from unittest.mock import AsyncMock, patch

import pytest

import server


class TestMCPResources:
    """Test MCP resource definitions."""

    def test_resources_are_registered(self) -> None:
        """Verify resources are registered with the MCP server."""
        # Check that the mcp instance has resources registered
        assert hasattr(server.mcp, "_resource_manager")

    def test_list_categories_resource_exists(self) -> None:
        """Verify categories resource function exists."""
        assert hasattr(server, "list_categories_resource")
        assert callable(server.list_categories_resource)

    def test_list_accounts_resource_exists(self) -> None:
        """Verify accounts resource function exists."""
        assert hasattr(server, "list_accounts_resource")
        assert callable(server.list_accounts_resource)

    def test_list_institutions_resource_exists(self) -> None:
        """Verify institutions resource function exists."""
        assert hasattr(server, "list_institutions_resource")
        assert callable(server.list_institutions_resource)

    @pytest.mark.asyncio
    async def test_list_categories_resource_calls_api(self) -> None:
        """Test that categories resource calls the API correctly."""
        mock_categories = [{"id": "cat_1", "name": "Food"}]

        with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
            with patch.object(
                server, "api_call_with_retry", new_callable=AsyncMock, return_value=mock_categories
            ) as mock_api:
                result = await server.list_categories_resource()

                mock_api.assert_called_once_with("get_transaction_categories")
                assert "Food" in result

    @pytest.mark.asyncio
    async def test_list_accounts_resource_calls_api(self) -> None:
        """Test that accounts resource calls the API correctly."""
        mock_accounts = [{"id": "acc_1", "displayName": "Checking"}]

        with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
            with patch.object(
                server, "api_call_with_retry", new_callable=AsyncMock, return_value=mock_accounts
            ) as mock_api:
                result = await server.list_accounts_resource()

                mock_api.assert_called_once_with("get_accounts")
                assert "Checking" in result


class TestMCPPrompts:
    """Test MCP prompt definitions."""

    def test_prompts_are_registered(self) -> None:
        """Verify prompts are registered with the MCP server."""
        assert hasattr(server.mcp, "_prompt_manager")

    def test_analyze_spending_prompt_exists(self) -> None:
        """Verify analyze_spending prompt function exists."""
        assert hasattr(server, "analyze_spending")
        assert callable(server.analyze_spending)

    def test_budget_review_prompt_exists(self) -> None:
        """Verify budget_review prompt function exists."""
        assert hasattr(server, "budget_review")
        assert callable(server.budget_review)

    def test_financial_health_check_prompt_exists(self) -> None:
        """Verify financial_health_check prompt function exists."""
        assert hasattr(server, "financial_health_check")
        assert callable(server.financial_health_check)

    def test_transaction_categorization_help_prompt_exists(self) -> None:
        """Verify transaction_categorization_help prompt function exists."""
        assert hasattr(server, "transaction_categorization_help")
        assert callable(server.transaction_categorization_help)

    def test_analyze_spending_returns_prompt(self) -> None:
        """Test analyze_spending generates expected prompt."""
        result = server.analyze_spending(period="last month", category="Food")

        assert "last month" in result
        assert "Food" in result
        assert "get_transactions" in result

    def test_analyze_spending_default_period(self) -> None:
        """Test analyze_spending uses default period."""
        result = server.analyze_spending()

        assert "this month" in result

    def test_budget_review_returns_prompt(self) -> None:
        """Test budget_review generates expected prompt."""
        result = server.budget_review(month="January")

        assert "January" in result
        assert "get_budgets" in result
        assert "Budget vs Actual" in result

    def test_financial_health_check_returns_prompt(self) -> None:
        """Test financial_health_check generates expected prompt."""
        result = server.financial_health_check()

        assert "Account Overview" in result
        assert "Cash Flow" in result
        assert "Net worth" in result

    def test_transaction_categorization_help_returns_prompt(self) -> None:
        """Test transaction_categorization_help generates expected prompt."""
        result = server.transaction_categorization_help(description="Amazon Purchase")

        assert "Amazon Purchase" in result
        assert "categories://list" in result
        assert "Best Category Match" in result
