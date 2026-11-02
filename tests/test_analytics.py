"""Tests for usage analytics and batch tools."""

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.payload import JsonPayload
from dateutil.relativedelta import relativedelta
from monarchmoney import MonarchMoney
from pydantic import JsonValue

import server


class TestUsageAnalytics:
    """Tool calls are logged, not accumulated in memory."""

    @pytest.mark.asyncio
    async def test_track_usage_logs_events_without_retaining_calls(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = [{"id": "acc_001", "displayName": "Test Account"}]

        with patch.object(server, "log") as log:
            await server.get_accounts()

        events = [call.args[0] for call in log.info.call_args_list]
        assert "tool_call" in events
        assert "tool_success" in events


@pytest.fixture
def overview_client(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = AsyncMock()
    client.get_accounts.return_value = {
        "accounts": [
            {
                "id": "acc_001",
                "displayName": "Checking",
                "currentBalance": 1200,
                "type": {"name": "depository"},
                "institution": {"name": "Synthetic Bank"},
            },
            {
                "id": "acc_002",
                "displayName": "Checking",
                "currentBalance": 800,
                "type": {"name": "depository"},
            },
        ]
    }
    client.get_budgets.return_value = {
        "budgetData": {
            "totalsByMonth": [
                {
                    "__typename": "BudgetTotals",
                    "month": "2026-09-01",
                    "plannedExpenses": 500,
                    "actualExpenses": 75,
                    "remainingExpenses": 425,
                    "optional": None,
                }
            ],
            "monthlyAmountsByCategory": [{"category": {"id": "cat_001"}, "monthlyAmounts": []}],
        }
    }
    client.get_cashflow.return_value = {
        "summary": [
            {
                "summary": {
                    "__typename": "CashflowSummary",
                    "sumIncome": 3000,
                    "sumExpense": -75,
                    "savings": 2925,
                    "optional": None,
                },
            }
        ],
        "byCategory": [{"category": {"id": "cat_001"}, "summary": {"sum": -75}}],
    }
    client.get_transactions.return_value = {
        "allTransactions": {
            "totalCount": 9,
            "results": [
                {
                    "id": "txn_001",
                    "amount": -50,
                    "category": {"name": "Food"},
                    "account": {"id": "acc_001", "displayName": "Checking"},
                },
                {
                    "id": "txn_002",
                    "amount": -25,
                    "category": {"name": "Food"},
                    "account": {"id": "acc_002", "displayName": "Checking"},
                },
                {
                    "id": "txn_003",
                    "amount": 3000,
                    "category": {"name": "Salary"},
                    "account": {"id": "acc_001", "displayName": "Checking"},
                },
            ],
        }
    }
    client.get_transaction_categories.return_value = {"categories": [{"id": "cat_001", "name": "Food"}]}
    monkeypatch.setattr(server, "mm_client", client)
    return client


class TestBatchTools:
    """Test batch financial analysis."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verbose", [False, True])
    async def test_get_complete_financial_overview(self, overview_client: AsyncMock, verbose: bool) -> None:
        if verbose:
            result = await server.get_complete_financial_overview("this month", verbose=True)
        else:
            result = await server.get_complete_financial_overview("this month")
        overview = json.loads(result.model_dump_json())

        assert overview["transaction_summary"] == {
            "total_count": 3,
            "total_expenses": 75,
            "total_income": 3000,
            "transfers_excluded": 0,
            "unique_categories": 2,
            "unique_accounts": 2,
        }
        assert overview["verbose"] is verbose
        assert overview["batch_metadata"]["transactions_truncated"] is True
        if verbose:
            assert overview["accounts"] == overview_client.get_accounts.return_value
            assert overview["budgets"] == overview_client.get_budgets.return_value
            assert overview["cashflow"] == overview_client.get_cashflow.return_value
            assert (
                overview["transactions"] == overview_client.get_transactions.return_value["allTransactions"]["results"]
            )
            assert overview["categories"] == overview_client.get_transaction_categories.return_value
        else:
            assert overview["accounts"] == [
                {
                    "id": "acc_001",
                    "displayName": "Checking",
                    "currentBalance": 1200,
                    "type": {"name": "depository"},
                },
                {
                    "id": "acc_002",
                    "displayName": "Checking",
                    "currentBalance": 800,
                    "type": {"name": "depository"},
                },
            ]
            assert overview["budgets"] == {
                "totalsByMonth": [
                    {"month": "2026-09-01", "plannedExpenses": 500, "actualExpenses": 75, "remainingExpenses": 425}
                ]
            }
            assert overview["cashflow"] == {"sumIncome": 3000, "sumExpense": -75, "savings": 2925}
            assert overview["transactions"] is None
            assert overview["categories"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total_count, truncated", [(3, False), (None, None)])
    async def test_overview_distinguishes_complete_and_unknown_transaction_coverage(
        self, overview_client: AsyncMock, total_count: int | None, truncated: bool | None
    ) -> None:
        page = overview_client.get_transactions.return_value["allTransactions"]
        if total_count is None:
            del page["totalCount"]
        else:
            page["totalCount"] = total_count

        result = await server.get_complete_financial_overview()

        assert result.batch_metadata["transactions_truncated"] is truncated
        assert result.transaction_summary["total_expenses"] == 75

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verbose", [False, True])
    async def test_analyze_spending_patterns(self, monkeypatch: pytest.MonkeyPatch, verbose: bool) -> None:
        """Exercise analysis through the real client's JSON request boundary."""
        older = (date.today().replace(day=1) - relativedelta(months=2)).strftime("%Y-%m")
        newer = (date.today().replace(day=1) - relativedelta(months=1)).strftime("%Y-%m")
        transactions: list[JsonValue] = [
            {
                "date": f"{older}-15",
                "amount": -100,
                "category": {"name": "Food"},
                "account": {"id": "acc_001", "displayName": "Checking"},
            },
            {
                "date": f"{older}-20",
                "amount": -50,
                "category": {"name": "Transit"},
                "account": {"id": "acc_002", "displayName": "Travel"},
            },
            {
                "date": f"{newer}-10",
                "amount": 3000,
                "category": {"name": "Salary"},
                "account": {"id": "acc_001", "displayName": "Checking"},
            },
        ]
        budgets: dict[str, JsonValue] = {
            "budgetData": {
                "totalsByMonth": [
                    {
                        "__typename": "BudgetTotals",
                        "month": f"{older}-01",
                        "plannedExpenses": 200,
                        "actualExpenses": 150,
                        "optional": None,
                    }
                ],
                "monthlyAmountsByCategory": [
                    {
                        "category": {"id": "cat_001"},
                        "monthlyAmounts": [{"month": f"{older}-01", "plannedCashFlowAmount": 200, "actualAmount": 100}],
                    }
                ],
            }
        }
        responses: dict[str, dict[str, JsonValue]] = {
            "GetTransactionsList": {"allTransactions": {"results": transactions, "totalCount": 4}},
            "GetJointPlanningData": budgets,
            "GetCategories": {"categories": [{"id": "cat_001", "name": "Food", "group": {"type": "expense"}}]},
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

        if verbose:
            result = await server.analyze_spending_patterns(lookback_months=12, include_forecasting=True, verbose=True)
        else:
            result = await server.analyze_spending_patterns(lookback_months=12, include_forecasting=True)
        analysis = json.loads(result.model_dump_json())

        assert analysis["monthly_trends"] == {
            older: {"expenses": 150, "income": 0, "net": -150, "transaction_count": 2},
            newer: {"expenses": 0, "income": 3000, "net": 3000, "transaction_count": 1},
        }
        assert analysis["category_analysis"]["Food"]["total"] == 100
        assert analysis["category_analysis"]["Transit"]["total"] == 50
        assert analysis["account_usage"] == {
            "Checking": {"total_volume": 3100, "transactions": 2},
            "Travel": {"total_volume": 50, "transactions": 1},
        }
        assert analysis["verbose"] is verbose
        assert analysis["metadata"]["transactions_truncated"] is True
        assert analysis["budget_performance"] == (
            budgets
            if verbose
            else {"totalsByMonth": [{"month": f"{older}-01", "plannedExpenses": 200, "actualExpenses": 150}]}
        )
        # The page is truncated, so its oldest month may be partial and only the newer month counts.
        assert analysis["forecast"]["predicted_expenses"] == 0
        assert analysis["forecast"]["predicted_income"] == 3000
        assert analysis["forecast"]["predicted_net"] == 3000
        assert analysis["errors"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("section", ["budgets", "cashflow"])
    @pytest.mark.parametrize("failure", ["exception", "malformed"])
    @pytest.mark.parametrize("verbose", [False, True])
    async def test_overview_preserves_partial_errors(
        self, overview_client: AsyncMock, section: str, failure: str, verbose: bool
    ) -> None:
        api = getattr(overview_client, f"get_{section}")
        if failure == "exception":
            api.side_effect = RuntimeError("Synthetic upstream failure")
        else:
            api.return_value = {"unexpected": []}

        result = await server.get_complete_financial_overview("this month", verbose=verbose)
        overview = json.loads(result.model_dump_json())

        assert set(overview[section]) == {"error"}
        assert overview[section]["error"]
        assert overview["transaction_summary"]["total_expenses"] == 75
        assert overview["transaction_summary"]["total_income"] == 3000
        if section == "budgets":
            assert overview["cashflow"] == (
                overview_client.get_cashflow.return_value
                if verbose
                else {"sumIncome": 3000, "sumExpense": -75, "savings": 2925}
            )
        elif verbose:
            assert overview["budgets"] == overview_client.get_budgets.return_value
        else:
            assert overview["budgets"]["totalsByMonth"][0]["actualExpenses"] == 75
