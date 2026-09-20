"""Test tool and resource responses, error propagation, and batch degradation.

The offline ``mock_api`` fixture isolates API calls and authentication.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

import server


def dispatch(by_method: dict[str, Any]) -> Callable[..., Any]:
    """Return or raise the value mapped to each API method name.

    Use exceptions to simulate individual failures within a batch call.
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
    pytest.param(lambda: server.get_transaction_splits(transaction_id="txn_1"), id="get_transaction_splits"),
    pytest.param(
        lambda: server.update_transaction_splits(
            transaction_id="txn_1",
            splits=[server.TransactionSplit(amount=-10.0, category_id="cat_1")],
        ),
        id="update_transaction_splits",
    ),
    pytest.param(lambda: server.get_account_holdings(account_id="acc_1"), id="get_account_holdings"),
    pytest.param(lambda: server.get_account_history(account_id="acc_1"), id="get_account_history"),
    pytest.param(lambda: server.get_institutions(), id="get_institutions"),
    pytest.param(lambda: server.get_recurring_transactions(), id="get_recurring_transactions"),
    pytest.param(
        lambda: server.update_recurring_transaction(
            merchant_id="merchant_1", merchant_name="Example Merchant", is_active=False
        ),
        id="update_recurring_transaction",
    ),
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
        mock_api.return_value = {
            "budgetData": {"totalsByMonth": [{"month": "2026-09-01", "plannedExpenses": 500, "actualExpenses": 75}]}
        }
        result = await server.get_budgets()
        assert result.budgets["budgetData"]["totalsByMonth"][0]["plannedExpenses"] == 500

    @pytest.mark.asyncio
    async def test_get_budgets_empty_when_none_configured(self, mock_api: AsyncMock) -> None:
        # The API raises this specific string when no budgets exist; the tool maps
        # it to an empty result rather than an error.
        mock_api.side_effect = Exception("Something went wrong while processing: None")
        result = await server.get_budgets()
        assert result.budgets == []
        assert "No budgets" in (result.message or "")

    @pytest.mark.asyncio
    async def test_get_cashflow_returns_cashflow_data(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"summary": {"summary": {"sumIncome": 5000, "sumExpense": -3200}}}
        result = await server.get_cashflow()
        assert result.cashflow["summary"]["summary"] == {"sumIncome": 5000, "sumExpense": -3200}

    @pytest.mark.asyncio
    async def test_get_transaction_categories_compact_strips_to_id_and_name(self, mock_api: AsyncMock) -> None:
        # The real client wraps the list under a "categories" key.
        mock_api.return_value = {
            "categories": [
                {"id": "cat_1", "name": "Groceries", "group": {"name": "Food"}, "order": 3},
                {"id": "cat_2", "name": "Transit", "group": {"name": "Auto"}, "order": 4},
            ]
        }
        result = await server.get_transaction_categories(verbose=False)
        assert result.categories == [{"id": "cat_1", "name": "Groceries"}, {"id": "cat_2", "name": "Transit"}]
        assert result.count == 2
        assert result.verbose is False

    @pytest.mark.asyncio
    async def test_get_transaction_categories_verbose_keeps_all_fields(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "categories": [{"id": "cat_1", "name": "Groceries", "group": {"name": "Food"}, "order": 3}]
        }
        result = await server.get_transaction_categories(verbose=True)
        assert result.categories[0]["group"] == {"name": "Food"}
        assert result.categories[0]["order"] == 3

    @pytest.mark.asyncio
    async def test_get_spending_summary_aggregates_by_category(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [
            {"amount": -40.0, "category": {"name": "Groceries"}, "date": "2024-01-05"},
            {"amount": -10.0, "category": {"name": "Groceries"}, "date": "2024-01-09"},
            {"amount": 2000.0, "category": {"name": "Income"}, "date": "2024-01-01"},
        ]
        result = await server.get_spending_summary(group_by="category")
        assert result.groups["Groceries"].expenses == 50.0
        assert result.groups["Groceries"].count == 2
        assert result.totals.income == 2000.0
        assert result.totals.expenses == 50.0
        assert result.totals.net == 1950.0

    @pytest.mark.asyncio
    async def test_get_spending_summary_groups_by_account_display_name(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "allTransactions": {
                "totalCount": 3,
                "results": [
                    {"amount": -40, "account": {"id": "acc_001", "displayName": "Checking"}},
                    {"amount": 2000, "account": {"id": "acc_001", "displayName": "Checking"}},
                    {"amount": -10, "account": {"id": "acc_002", "displayName": "Travel"}},
                ],
            }
        }

        result = await server.get_spending_summary(group_by="account")

        assert set(result.groups) == {"Checking", "Travel"}
        assert result.groups["Checking"].income == 2000
        assert result.groups["Checking"].expenses == 40
        assert result.groups["Checking"].net == 1960
        assert result.groups["Travel"].expenses == 10
        assert result.groups["Travel"].net == -10
        assert result.totals.income == 2000
        assert result.totals.expenses == 50
        assert result.totals.net == 1950

    @pytest.mark.asyncio
    async def test_get_spending_summary_paginates_beyond_one_page(self, mock_api: AsyncMock) -> None:
        """Regression test: a single limit=1000 call used to silently drop everything
        past the first page. A category could then report a total smaller than a
        single merchant within it. Build 1,500 nested-response transactions across
        two pages and assert the aggregation reflects all of them, not just page one.
        """
        page_one = [{"amount": -10.0, "category": {"name": "Restaurants"}, "date": "2024-01-01"} for _ in range(1000)]
        page_two = [{"amount": -10.0, "category": {"name": "Restaurants"}, "date": "2024-01-02"} for _ in range(500)]

        def _side_effect(method_name: str, *args: object, **kwargs: object) -> dict[str, object]:
            assert method_name == "get_transactions"
            offset = kwargs.get("offset", 0)
            results = page_one if offset == 0 else page_two if offset == 1000 else []
            return {"allTransactions": {"totalCount": 1500, "results": results}}

        mock_api.side_effect = _side_effect
        result = await server.get_spending_summary(group_by="category")

        assert result.groups["Restaurants"].count == 1500
        assert result.groups["Restaurants"].expenses == 15000.0
        assert result.totals.expenses == 15000.0
        assert mock_api.await_count == 2

    @pytest.mark.asyncio
    async def test_get_spending_summary_flat_list_response_paginates_on_short_page(self, mock_api: AsyncMock) -> None:
        """extract_transactions_list also accepts a bare list (no totalCount). A
        naive ``total_count = 0`` fallback would stop after page one regardless of
        whether the page was full, since any real page length is >= 0. Simulate a
        full first page (page_size items, so the "short page" signal is absent) to
        prove pagination continues into a second call rather than stopping early.
        """
        page_one = [{"amount": -1.0, "category": {"name": "Misc"}, "date": "2024-01-01"} for _ in range(1000)]
        page_two = [{"amount": -1.0, "category": {"name": "Misc"}, "date": "2024-01-02"} for _ in range(3)]

        def _side_effect(method_name: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            offset = kwargs.get("offset", 0)
            return page_one if offset == 0 else page_two if offset == 1000 else []

        mock_api.side_effect = _side_effect
        result = await server.get_spending_summary(group_by="category")

        assert result.groups["Misc"].count == 1003
        assert mock_api.await_count == 2

    @pytest.mark.asyncio
    async def test_refresh_accounts_returns_result(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"status": "refresh_requested"}
        result = await server.refresh_accounts()
        assert result.result["status"] == "refresh_requested"


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
        budgets = {
            "budgetData": {
                "totalsByMonth": [{"month": "2026-09-01", "plannedExpenses": 200}],
                "monthlyAmountsByCategory": [],
            }
        }
        mock_api.side_effect = dispatch(
            {
                "get_transactions": RuntimeError("transactions service down"),
                "get_budgets": budgets,
            }
        )
        result = await server.analyze_spending_patterns(lookback_months=3, include_forecasting=True)
        assert result.monthly_trends == {}
        assert result.category_analysis == {}
        assert result.analysis_period["months_analyzed"] == 3
        assert set(result.errors) == {"transactions"}
        assert result.budget_performance == {"totalsByMonth": [{"month": "2026-09-01", "plannedExpenses": 200}]}
        assert result.metadata["transactions_truncated"] is None
        assert result.forecast is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [RuntimeError("budgets service down"), {"unexpected": []}])
    async def test_analyze_spending_patterns_retains_trends_when_budgets_fail(
        self, mock_api: AsyncMock, failure: object
    ) -> None:
        transactions = [
            {
                "date": "2024-01-15",
                "amount": -50.0,
                "category": {"name": "Food"},
                "account": {"id": "acc_001", "displayName": "Checking"},
            },
            {
                "date": "2024-02-10",
                "amount": -75.0,
                "category": {"name": "Food"},
                "account": {"id": "acc_001", "displayName": "Checking"},
            },
        ]
        mock_api.side_effect = dispatch(
            {
                "get_transactions": {"allTransactions": {"totalCount": 2, "results": transactions}},
                "get_budgets": failure,
            }
        )
        result = await server.analyze_spending_patterns(lookback_months=3, include_forecasting=True)
        assert result.monthly_trends == {
            "2024-01": {"income": 0, "expenses": 50, "net": -50, "transaction_count": 1},
            "2024-02": {"income": 0, "expenses": 75, "net": -75, "transaction_count": 1},
        }
        assert result.category_analysis["Food"]["total"] == 125.0
        assert set(result.errors) == {"budgets"}
        assert result.budget_performance == {}
        assert result.forecast["predicted_expenses"] == 62.5
        assert result.account_usage == {"Checking": {"total_volume": 125, "transactions": 2}}
        assert result.metadata["transactions_truncated"] is False
