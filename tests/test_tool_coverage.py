"""Success + failure coverage matrix for every MCP tool and resource.

Goal: each tool and resource has at least one success test (asserting real output
shape) and one failure test (an upstream error propagates, or a batch tool degrades
gracefully). Some success cases already live in topic-specific files; this file fills
the gaps and guarantees a failure case for every tool via one parametrized test.

All tests use the ``mock_api`` fixture (see conftest.py), which patches
``ensure_authenticated`` and ``api_call_with_retry`` so tests are offline and
independent of global auth state.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

import server


def dispatch(by_method: dict[str, Any]) -> Callable[..., Any]:
    """Build an api_call_with_retry side_effect that returns/raises per method name.

    Values that are exceptions are raised, simulating a single failing API call
    among several that a batch tool makes.
    """

    def _side_effect(method_name: str, *args: Any, **kwargs: Any) -> Any:
        result = by_method[method_name]
        if isinstance(result, BaseException):
            raise result
        return result

    return _side_effect


# Every tool routes API access through api_call_with_retry and re-raises non-auth
# errors. Each entry builds the coroutine for one tool with the minimum valid args.
TOOL_CALLS: list[Any] = [
    pytest.param(lambda: server.get_accounts(), id="get_accounts"),
    pytest.param(lambda: server.get_transactions(), id="get_transactions"),
    pytest.param(lambda: server.search_transactions(query="coffee"), id="search_transactions"),
    pytest.param(lambda: server.get_budgets(), id="get_budgets"),
    pytest.param(lambda: server.get_cashflow(), id="get_cashflow"),
    pytest.param(lambda: server.get_transaction_categories(), id="get_transaction_categories"),
    pytest.param(
        lambda: server.create_transaction(
            amount=-12.5, merchant_name="Corner Deli", account_id="acc_1", date="2024-01-15", category_id="cat_1"
        ),
        id="create_transaction",
    ),
    pytest.param(lambda: server.update_transaction(transaction_id="txn_1", notes="memo"), id="update_transaction"),
    pytest.param(lambda: server.get_account_holdings(), id="get_account_holdings"),
    pytest.param(lambda: server.get_account_history(account_id="acc_1"), id="get_account_history"),
    pytest.param(lambda: server.get_institutions(), id="get_institutions"),
    pytest.param(lambda: server.get_recurring_transactions(), id="get_recurring_transactions"),
    pytest.param(lambda: server.set_budget_amount(category_id="cat_1", amount=500.0), id="set_budget_amount"),
    pytest.param(
        lambda: server.create_manual_account(account_name="Savings", account_type="savings", balance=1000.0),
        id="create_manual_account",
    ),
    pytest.param(lambda: server.get_spending_summary(), id="get_spending_summary"),
    pytest.param(lambda: server.refresh_accounts(), id="refresh_accounts"),
]

RESOURCE_CALLS: list[Any] = [
    pytest.param(lambda: server.list_categories_resource(), id="list_categories_resource"),
    pytest.param(lambda: server.list_accounts_resource(), id="list_accounts_resource"),
    pytest.param(lambda: server.list_institutions_resource(), id="list_institutions_resource"),
]


class TestToolFailurePropagation:
    """Every tool surfaces upstream API errors rather than swallowing them."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("make_coro", TOOL_CALLS)
    async def test_tool_propagates_api_error(
        self, mock_api: AsyncMock, make_coro: Callable[[], Awaitable[str]]
    ) -> None:
        mock_api.side_effect = RuntimeError("upstream API failure")
        with pytest.raises(RuntimeError, match="upstream API failure"):
            await make_coro()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("make_coro", RESOURCE_CALLS)
    async def test_resource_propagates_api_error(
        self, mock_api: AsyncMock, make_coro: Callable[[], Awaitable[str]]
    ) -> None:
        mock_api.side_effect = RuntimeError("upstream API failure")
        with pytest.raises(RuntimeError, match="upstream API failure"):
            await make_coro()


class TestReadToolSuccess:
    """Success paths for tools without a success test elsewhere."""

    @pytest.mark.asyncio
    async def test_get_budgets_returns_budget_data(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"budgets": [{"category_id": "cat_1", "amount": 500}]}
        data = json.loads(await server.get_budgets())
        assert data["budgets"][0]["category_id"] == "cat_1"

    @pytest.mark.asyncio
    async def test_get_budgets_empty_when_none_configured(self, mock_api: AsyncMock) -> None:
        # The API raises this specific string when no budgets exist; the tool maps
        # it to an empty result rather than an error.
        mock_api.side_effect = Exception("Something went wrong while processing: None")
        data = json.loads(await server.get_budgets())
        assert data["budgets"] == []
        assert "No budgets" in data["message"]

    @pytest.mark.asyncio
    async def test_get_cashflow_returns_cashflow_data(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"income": 5000, "expenses": 3200}
        data = json.loads(await server.get_cashflow())
        assert data["income"] == 5000

    @pytest.mark.asyncio
    async def test_get_transaction_categories_compact_strips_to_id_and_name(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"id": "cat_1", "name": "Groceries", "group": {"name": "Food"}, "order": 3},
            {"id": "cat_2", "name": "Transit", "group": {"name": "Auto"}, "order": 4},
        ]
        data = json.loads(await server.get_transaction_categories(verbose=False))
        assert data == [{"id": "cat_1", "name": "Groceries"}, {"id": "cat_2", "name": "Transit"}]

    @pytest.mark.asyncio
    async def test_get_transaction_categories_verbose_keeps_all_fields(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [{"id": "cat_1", "name": "Groceries", "group": {"name": "Food"}, "order": 3}]
        data = json.loads(await server.get_transaction_categories(verbose=True))
        assert data[0]["group"] == {"name": "Food"}
        assert data[0]["order"] == 3

    @pytest.mark.asyncio
    async def test_get_spending_summary_aggregates_by_category(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"amount": -40.0, "category": {"name": "Groceries"}, "date": "2024-01-05"},
            {"amount": -10.0, "category": {"name": "Groceries"}, "date": "2024-01-09"},
            {"amount": 2000.0, "category": {"name": "Income"}, "date": "2024-01-01"},
        ]
        data = json.loads(await server.get_spending_summary(group_by="category"))
        assert data["groups"]["Groceries"]["expenses"] == 50.0
        assert data["groups"]["Groceries"]["count"] == 2
        assert data["totals"]["income"] == 2000.0
        assert data["totals"]["expenses"] == 50.0
        assert data["totals"]["net"] == 1950.0

    @pytest.mark.asyncio
    async def test_refresh_accounts_returns_result(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"status": "refresh_requested"}
        data = json.loads(await server.refresh_accounts())
        assert data["status"] == "refresh_requested"


class TestResourceSuccess:
    """Success path for the institutions resource (others covered in test_mcp_features)."""

    @pytest.mark.asyncio
    async def test_list_institutions_resource_returns_institutions(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [{"id": "inst_1", "name": "Example Bank"}]
        result = await server.list_institutions_resource()
        assert "Example Bank" in result
        mock_api.assert_awaited_once_with("get_institutions")


class TestBatchToolDegradation:
    """analyze_spending_patterns keeps working when an upstream call fails."""

    @pytest.mark.asyncio
    async def test_analyze_spending_patterns_degrades_when_transactions_fail(self, mock_api: AsyncMock) -> None:
        mock_api.side_effect = dispatch(
            {
                "get_transactions": RuntimeError("transactions service down"),
                "get_budgets": [],
                "get_accounts": [],
                "get_transaction_categories": [],
            }
        )
        data = json.loads(await server.analyze_spending_patterns(lookback_months=3, include_forecasting=False))
        # Tool returns a valid analysis with empty trends rather than raising.
        assert data["monthly_trends"] == {}
        assert data["category_analysis"] == {}
        assert data["analysis_period"]["months_analyzed"] == 3

    @pytest.mark.asyncio
    async def test_analyze_spending_patterns_builds_trends_on_success(self, mock_api: AsyncMock) -> None:
        transactions = [
            {"date": "2024-01-15", "amount": -50.0, "category": {"name": "Food"}, "account": {"name": "Checking"}},
            {"date": "2024-02-10", "amount": -75.0, "category": {"name": "Food"}, "account": {"name": "Checking"}},
        ]
        mock_api.side_effect = dispatch(
            {
                "get_transactions": transactions,
                "get_budgets": [],
                "get_accounts": [],
                "get_transaction_categories": [],
            }
        )
        data = json.loads(await server.analyze_spending_patterns(lookback_months=3, include_forecasting=False))
        assert "2024-01" in data["monthly_trends"]
        assert "2024-02" in data["monthly_trends"]
        assert data["category_analysis"]["Food"]["total"] == 125.0
