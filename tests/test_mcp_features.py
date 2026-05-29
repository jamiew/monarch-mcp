"""Tests for MCP resources and prompts."""

from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import CompletionArgument, PromptReference, ResourceTemplateReference

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


class TestDisplayTitles:
    """Every tool and prompt advertises a human-friendly title (2025-06-18)."""

    def test_all_tools_have_titles(self) -> None:
        untitled = [t.name for t in server.mcp._tool_manager.list_tools() if not t.title]
        assert untitled == []

    def test_all_prompts_have_titles(self) -> None:
        untitled = [p.name for p in server.mcp._prompt_manager.list_prompts() if not p.title]
        assert untitled == []

    def test_specific_tool_title(self) -> None:
        assert server.mcp._tool_manager.get_tool("get_accounts").title == "Get Accounts"


class TestResourceTemplates:
    """Parameterized resource templates for per-account holdings and history."""

    @pytest.mark.asyncio
    async def test_templates_are_registered(self) -> None:
        templates = await server.mcp.list_resource_templates()
        uris = {t.uriTemplate for t in templates}
        assert "accounts://{account_id}/holdings" in uris
        assert "accounts://{account_id}/history" in uris

    @pytest.mark.asyncio
    async def test_holdings_template_calls_api_with_account_id(self) -> None:
        with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
            with patch.object(
                server, "api_call_with_retry", new_callable=AsyncMock, return_value=[{"symbol": "AAPL"}]
            ) as mock_api:
                result = await server.account_holdings_resource(account_id="acc_1")
                mock_api.assert_awaited_once_with("get_account_holdings", account_id="acc_1")
                assert "AAPL" in result

    @pytest.mark.asyncio
    async def test_history_template_calls_api_with_account_id(self) -> None:
        with patch.object(server, "ensure_authenticated", new_callable=AsyncMock):
            with patch.object(
                server, "api_call_with_retry", new_callable=AsyncMock, return_value=[{"balance": 100}]
            ) as mock_api:
                result = await server.account_history_resource(account_id="acc_9")
                mock_api.assert_awaited_once_with("get_account_history", account_id="acc_9")
                assert "100" in result


class TestCompletions:
    """Argument autocompletion for prompts and resource templates."""

    @pytest.mark.asyncio
    async def test_category_completion_filters_live_names(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"categories": [{"id": "c1", "name": "Groceries"}, {"id": "c2", "name": "Gas"}]}
        ref = PromptReference(type="ref/prompt", name="analyze_spending")
        completion = await server.handle_completion(ref, CompletionArgument(name="category", value="gro"), None)
        assert completion is not None
        assert completion.values == ["Groceries"]

    @pytest.mark.asyncio
    async def test_account_id_completion_filters_live_ids(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"accounts": [{"id": "acc_123"}, {"id": "acc_999"}]}
        ref = ResourceTemplateReference(type="ref/resource", uri="accounts://{account_id}/holdings")
        completion = await server.handle_completion(ref, CompletionArgument(name="account_id", value="123"), None)
        assert completion is not None
        assert completion.values == ["acc_123"]

    @pytest.mark.asyncio
    async def test_completion_returns_none_for_unknown_argument(self, mock_api: AsyncMock) -> None:
        ref = PromptReference(type="ref/prompt", name="budget_review")
        completion = await server.handle_completion(ref, CompletionArgument(name="month", value="Jan"), None)
        assert completion is None

    @pytest.mark.asyncio
    async def test_completion_is_best_effort_on_api_failure(self, mock_api: AsyncMock) -> None:
        # A failing upstream call must not raise out of a completion request.
        mock_api.side_effect = RuntimeError("API down")
        ref = PromptReference(type="ref/prompt", name="analyze_spending")
        completion = await server.handle_completion(ref, CompletionArgument(name="category", value="x"), None)
        assert completion is not None
        assert completion.values == []


class TestProgressReporting:
    """Batch tools report progress through an injected Context."""

    @pytest.mark.asyncio
    async def test_overview_reports_progress(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = []
        ctx = AsyncMock()
        await server.get_complete_financial_overview(period="this month", ctx=ctx)
        assert ctx.report_progress.await_count >= 2
        first_progress = ctx.report_progress.await_args_list[0].args[0]
        last_progress = ctx.report_progress.await_args_list[-1].args[0]
        assert first_progress == 0
        assert last_progress == 5

    @pytest.mark.asyncio
    async def test_analyze_patterns_reports_progress(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = []
        ctx = AsyncMock()
        await server.analyze_spending_patterns(lookback_months=2, include_forecasting=False, ctx=ctx)
        assert ctx.report_progress.await_count >= 2

    @pytest.mark.asyncio
    async def test_overview_works_without_context(self, mock_api: AsyncMock) -> None:
        # ctx is optional; omitting it must not raise.
        mock_api.return_value = []
        result = await server.get_complete_financial_overview(period="this month")
        assert isinstance(result, server.FinancialOverview)
