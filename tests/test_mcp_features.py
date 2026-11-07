"""MCP resources, prompts, completions, progress, and error visibility."""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import CompletionArgument, PromptReference, ResourceTemplateReference, TextContent

import server


class TestResources:
    @pytest.mark.parametrize(
        ("read", "method", "response", "expected"),
        [
            (lambda: server.list_categories_resource(), "get_transaction_categories", [{"name": "Food"}], "Food"),
            (lambda: server.list_accounts_resource(), "get_accounts", [{"displayName": "Checking"}], "Checking"),
            (
                lambda: server.list_institutions_resource(),
                "get_institutions",
                [{"name": "Example Bank"}],
                "Example Bank",
            ),
            (
                lambda: server.account_history_resource(account_id="acc_9"),
                "get_account_history",
                [{"balance": 100}],
                "100",
            ),
        ],
        ids=["categories", "accounts", "institutions", "history"],
    )
    async def test_resource_returns_upstream_data(
        self,
        mock_api: AsyncMock,
        read: Callable[[], Awaitable[str]],
        method: str,
        response: list[dict[str, Any]],
        expected: str,
    ) -> None:
        mock_api.return_value = response
        assert expected in await read()
        assert mock_api.await_args.args[0] == method

    async def test_templates_are_registered(self) -> None:
        uris = {t.uri_template for t in await server.mcp.list_resource_templates()}
        assert "accounts://{account_id}/holdings" in uris
        assert "accounts://{account_id}/history" in uris

    async def test_template_read_through_protocol(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [{"symbol": "AAPL"}]
        async with Client(server.mcp) as client:
            result = await client.read_resource("accounts://acc_1/holdings")
        mock_api.assert_awaited_once_with("get_account_holdings", account_id="acc_1")
        assert "AAPL" in str(result.contents[0])

    async def test_blank_account_id_is_rejected_before_api(self, mock_api: AsyncMock) -> None:
        # RFC 6570 matching lets {account_id} capture an empty string.
        async with Client(server.mcp) as client:
            with pytest.raises(MCPError, match="account_id"):
                await client.read_resource("accounts:///holdings")
        mock_api.assert_not_awaited()


class TestPrompts:
    def test_analyze_spending_includes_arguments(self) -> None:
        result = server.analyze_spending(period="last month", category="Food")
        assert "last month" in result
        assert "Food" in result

    def test_budget_review_includes_month(self) -> None:
        assert "January" in server.budget_review(month="January")

    async def test_all_prompts_have_titles(self) -> None:
        untitled = [p.name for p in await server.mcp.list_prompts() if not p.title]
        assert untitled == []


class TestCompletions:
    async def test_category_completion_filters_live_names(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"categories": [{"id": "c1", "name": "Groceries"}, {"id": "c2", "name": "Gas"}]}
        ref = PromptReference(type="ref/prompt", name="analyze_spending")
        completion = await server.handle_completion(ref, CompletionArgument(name="category", value="gro"), None)
        assert completion is not None
        assert completion.values == ["Groceries"]

    async def test_completion_returns_none_for_unknown_argument(self, mock_api: AsyncMock) -> None:
        ref = PromptReference(type="ref/prompt", name="budget_review")
        completion = await server.handle_completion(ref, CompletionArgument(name="month", value="Jan"), None)
        assert completion is None

    async def test_completion_is_best_effort_on_api_failure(self, mock_api: AsyncMock) -> None:
        # A failing upstream call must not raise out of a completion request.
        mock_api.side_effect = RuntimeError("API down")
        ref = PromptReference(type="ref/prompt", name="analyze_spending")
        completion = await server.handle_completion(ref, CompletionArgument(name="category", value="x"), None)
        assert completion is not None
        assert completion.values == []

    async def test_account_id_completion_through_protocol(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"accounts": [{"id": "acc_123"}, {"id": "acc_999"}]}
        ref = ResourceTemplateReference(type="ref/resource", uri="accounts://{account_id}/history")
        async with Client(server.mcp) as client:
            result = await client.complete(ref, {"name": "account_id", "value": "999"})
        assert result.completion.values == ["acc_999"]


class TestProgressReporting:
    async def test_overview_reports_progress(self, mock_api: AsyncMock) -> None:
        responses = {
            "get_accounts": {"accounts": []},
            "get_budgets": {"budgetData": {"totalsByMonth": []}},
            "get_cashflow": {"summary": [{"summary": {"sumIncome": 0, "sumExpense": 0}}]},
            "get_transactions": {"allTransactions": {"results": [], "totalCount": 0}},
            "get_transaction_categories": {"categories": []},
        }
        mock_api.side_effect = lambda method, **kwargs: responses[method]
        ctx = AsyncMock()
        await server.get_complete_financial_overview(period="this month", ctx=ctx)
        first_progress = ctx.report_progress.await_args_list[0].args[0]
        last_progress = ctx.report_progress.await_args_list[-1].args[0]
        assert first_progress == 0
        assert last_progress == ctx.report_progress.await_args_list[-1].args[1]

    async def test_injected_context_reports_progress_to_client(self, mock_api: AsyncMock) -> None:
        responses = {
            "get_transactions": {"allTransactions": {"results": [], "totalCount": 0}},
            "get_budgets": {"budgetData": {"totalsByMonth": []}},
        }
        mock_api.side_effect = lambda method, **kwargs: responses[method]
        updates: list[tuple[float, float | None]] = []

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            updates.append((progress, total))

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "analyze_spending_patterns",
                {"lookback_months": 2, "include_forecasting": False},
                progress_callback=on_progress,
            )
        assert not result.is_error
        assert updates[0] == (0, 2)
        assert updates[-1] == (2, 2)


class TestToolErrors:
    """The model sees anticipated failure messages but not internal error details."""

    async def test_validation_message_names_the_field(self, mock_api: AsyncMock) -> None:
        async with Client(server.mcp) as client:
            result = await client.call_tool("get_transaction_rules", {"limit": 0})
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert "limit" in result.content[0].text
        mock_api.assert_not_awaited()

    async def test_unexpected_error_text_stays_on_server(self, mock_api: AsyncMock) -> None:
        mock_api.side_effect = KeyError("internal detail")
        async with Client(server.mcp) as client:
            result = await client.call_tool("get_accounts", {})
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert "internal detail" not in result.content[0].text

    async def test_resource_auth_failure_message_reaches_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        failure = ValueError("MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required")
        monkeypatch.setattr(server, "ensure_authenticated", AsyncMock(side_effect=failure))
        async with Client(server.mcp) as client:
            with pytest.raises(MCPError, match="MONARCH_EMAIL"):
                await client.read_resource("accounts://list")
