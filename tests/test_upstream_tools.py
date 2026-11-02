"""Tools built on community client 1.7.x methods, with synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from dateutil.relativedelta import relativedelta
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError

import server
from tests.conftest import CallTool


def by_method(responses: dict[str, Any]) -> Callable[..., Any]:
    def side_effect(method_name: str, *args: Any, **kwargs: Any) -> Any:
        return responses[method_name]

    return side_effect


def calls_to(api: AsyncMock, method_name: str) -> list[dict[str, Any]]:
    return [call.kwargs for call in api.await_args_list if call.args[0] == method_name]


def stream(stream_id: str, active: bool) -> dict[str, Any]:
    return {
        "stream": {
            "id": stream_id,
            "name": "Corner Gym",
            "frequency": "monthly",
            "amount": -40.0,
            "baseDate": "2026-01-05",
            "isApproximate": False,
            "isActive": active,
            "reviewStatus": "approved",
            "recurringType": "expense",
            "merchant": {"id": "merchant_1", "name": "Corner Gym"},
            "creditReportLiabilityAccount": None,
        },
        "nextForecastedTransaction": {"date": "2026-12-05", "amount": -40.0},
        "category": {"id": "cat_001", "name": "Fitness", "icon": "x"},
        "account": {"id": "acc_1", "displayName": "Everyday Checking", "icon": "x", "logoUrl": None},
    }


async def test_recurring_streams_compact_and_active_filter(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {"recurringTransactionStreams": [stream("s_1", True), stream("s_2", False)]}
    result = await call_tool("get_recurring_streams", {"include_liabilities": False})
    assert mock_api.await_args.kwargs == {"include_liabilities": False}
    assert result["count"] == 1
    row = result["streams"][0]
    assert row["id"] == "s_1"
    assert row["merchant"] == {"id": "merchant_1", "name": "Corner Gym"}
    assert (row["next_date"], row["next_amount"]) == ("2026-12-05", -40.0)
    assert (row["category_id"], row["account_id"]) == ("cat_001", "acc_1")
    assert row["is_approximate"] is False

    everything = await call_tool("get_recurring_streams", {"active_only": False, "verbose": True})
    assert everything["count"] == 2
    assert "stream" in everything["streams"][1]


def merchant(index: int) -> dict[str, Any]:
    return {
        "id": f"merchant_{index}",
        "name": f"Corner Deli {index}",
        "logoUrl": None,
        "transactionCount": index,
        "ruleCount": 0,
        "canBeDeleted": True,
        "hasActiveRecurringStreams": False,
    }


async def test_merchants_page_and_next_offset(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {"merchantCount": 7, "merchants": [merchant(3), merchant(4)]}
    full = await call_tool("get_merchants", {"limit": 2, "offset": 2})
    assert mock_api.await_args.kwargs == {"limit": 2, "offset": 2}
    assert full["next_offset"] == 4
    assert full["total_count"] == 7
    assert full["merchants"][0] == {
        "id": "merchant_3",
        "name": "Corner Deli 3",
        "transaction_count": 3,
        "rule_count": 0,
        "has_active_recurring_streams": False,
        "can_be_deleted": True,
    }

    mock_api.return_value = {"merchantCount": 7, "merchants": [merchant(5)]}
    last = await call_tool("get_merchants", {"limit": 2, "offset": 4})
    assert last["count"] == 1
    assert last["next_offset"] is None


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 501}, {"offset": -1}])
async def test_merchants_rejects_bad_paging_before_api(mock_api: AsyncMock, arguments: dict[str, int]) -> None:
    with pytest.raises(ToolError):
        await server.get_merchants(**arguments)
    mock_api.assert_not_awaited()


async def test_transaction_tags_compact(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "householdTransactionTags": [
            {"id": "tag_1", "name": "Travel", "color": "#19D2A5", "order": 1, "transactionCount": 3, "__typename": "T"}
        ]
    }
    result = await call_tool("get_transaction_tags", {})
    assert result["tags"] == [{"id": "tag_1", "name": "Travel", "color": "#19D2A5", "transaction_count": 3}]


async def test_net_worth_defaults_to_one_year_of_daily_totals(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {"aggregateSnapshots": [{"date": "2026-01-01", "balance": 1000.0, "__typename": "S"}]}
    result = await call_tool("get_net_worth_history", {})
    today = date.today()
    expected = {"start_date": (today - relativedelta(years=1)).isoformat(), "end_date": today.isoformat()}
    assert calls_to(mock_api, "get_aggregate_snapshots") == [expected]
    assert result["history"] == [{"date": "2026-01-01", "balance": 1000.0}]


async def test_net_worth_by_type_filters_months_locally(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "snapshotsByAccountType": [
            {"accountType": "depository", "month": "2026-01", "balance": 10.0},
            {"accountType": "depository", "month": "2026-03", "balance": 30.0},
            {"accountType": "loan", "month": "2026-04", "balance": -5.0},
        ],
        "accountTypes": [{"name": "depository", "group": "asset"}, {"name": "loan", "group": "liability"}],
    }
    result = await call_tool(
        "get_net_worth_history", {"start_date": "2026-01-15", "end_date": "2026-03-31", "by_account_type": True}
    )
    assert calls_to(mock_api, "get_account_snapshots_by_type") == [{"start_date": "2026-01-15", "timeframe": "month"}]
    assert [row["month"] for row in result["history"]] == ["2026-01", "2026-03"]
    assert result["account_types"][1] == {"name": "loan", "group": "liability"}


async def test_net_worth_rejects_reversed_dates_before_api(mock_api: AsyncMock) -> None:
    with pytest.raises(ToolError):
        await server.get_net_worth_history("2026-03-01", "2026-02-01")
    mock_api.assert_not_awaited()


def duplicate_group() -> list[dict[str, Any]]:
    txn = {
        "id": "txn_1",
        "date": "2026-10-01",
        "amount": -4.5,
        "plaidName": "CORNER DELI 123",
        "merchant": {"name": "Corner Deli"},
        "category": {"id": "cat_001", "name": "Coffee"},
        "account": {"id": "acc_1", "displayName": "Everyday Card"},
        "needsReview": False,
        "createdAt": "2026-10-01T00:00:00Z",
    }
    return [
        {
            "date": "2026-10-01",
            "amount": -4.5,
            "plaidName": "CORNER DELI 123",
            "account_id": "acc_1",
            "account_name": "Everyday Card",
            "transactions": [txn, {**txn, "id": "txn_2"}],
        }
    ]


async def test_duplicates_default_range_and_bounded_scan(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.side_effect = by_method(
        {
            "get_transactions": {"allTransactions": {"totalCount": 12, "results": []}},
            "find_duplicate_transactions": duplicate_group(),
        }
    )
    result = await call_tool("find_duplicate_transactions", {})
    today = date.today()
    dates = {"start_date": (today - timedelta(days=90)).isoformat(), "end_date": today.isoformat()}
    [scan] = calls_to(mock_api, "find_duplicate_transactions")
    assert {key: scan[key] for key in dates} == dates
    assert scan["account_ids"] is None
    assert scan["max_pages"] * scan["page_size"] < 100_000
    assert result["scan_truncated"] is False
    assert result["transactions_in_range"] == 12
    group = result["groups"][0]
    assert [txn["id"] for txn in group["transactions"]] == ["txn_1", "txn_2"]
    assert group["transactions"][0]["merchant"] == "Corner Deli"


async def test_duplicates_report_truncated_scan(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.side_effect = by_method(
        {
            "get_transactions": {"allTransactions": {"totalCount": 1_000_000, "results": []}},
            "find_duplicate_transactions": [],
        }
    )
    result = await call_tool("find_duplicate_transactions", {"start_date": "2020-01-01", "account_id": "acc_1"})
    [scan] = calls_to(mock_api, "find_duplicate_transactions")
    assert scan["account_ids"] == ["acc_1"]
    assert scan["start_date"] == "2020-01-01"
    assert result["scan_truncated"] is True
    assert result["count"] == 0


async def test_savings_goals_skip_archived_and_compact(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "savingsGoals": [
            {"id": "goal_1", "name": "Trip", "status": "active", "archivedAt": None, "targetAmount": 500, "x": 1},
            {"id": "goal_2", "name": "Old", "status": "active", "archivedAt": "2026-01-01T00:00:00Z"},
            {"id": "goal_3", "name": "Older", "status": "archived", "archivedAt": None},
        ]
    }
    result = await call_tool("get_savings_goals", {})
    assert result["goals"] == [
        {"id": "goal_1", "name": "Trip", "status": "active", "archivedAt": None, "targetAmount": 500}
    ]
    everything = await call_tool("get_savings_goals", {"include_archived": True})
    assert [goal["id"] for goal in everything["goals"]] == ["goal_1", "goal_2", "goal_3"]


async def test_credit_history_sorted_without_pictures(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "me": {"id": "user_001"},
        "myHousehold": {
            "id": "household_1",
            "users": [{"id": "user_001", "name": "Alex", "displayName": "Alex", "profilePictureUrl": "https://x"}],
        },
        "spinwheelUser": None,
        "creditScoreSnapshots": [
            {"reportedDate": "2026-05-01", "score": 700, "user": {"id": "user_001"}},
            {"reportedDate": "2026-01-01", "score": 690, "user": {"id": "user_001"}},
        ],
    }
    result = await call_tool("get_credit_score_history", {})
    assert result["enrolled"] is False
    assert result["users"] == [{"id": "user_001", "name": "Alex"}]
    assert [row["score"] for row in result["snapshots"]] == [690, 700]
    assert result["snapshots"][0] == {"date": "2026-01-01", "score": 690, "user_id": "user_001"}


async def test_account_types_resource(mock_api: AsyncMock) -> None:
    mock_api.return_value = {"accountTypeOptions": [{"type": {"name": "loan", "possibleSubtypes": []}}]}
    async with Client(server.mcp) as client:
        result = await client.read_resource("accounts://types")
    assert '"loan"' in str(result.contents[0])
    mock_api.assert_awaited_once_with("get_account_type_options")


async def test_set_transaction_tags_replaces_and_returns_tags(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "setTransactionTags": {
            "errors": None,
            "transaction": {"id": "txn_123", "tags": [{"id": "tag_1"}]},
        }
    }
    result = await call_tool("set_transaction_tags", {"transaction_id": "txn_123", "tag_ids": ["tag_1"]})
    assert mock_api.await_args.kwargs == {"transaction_id": "txn_123", "tag_ids": ["tag_1"]}
    assert result["tag_ids"] == ["tag_1"]

    mock_api.return_value = {"setTransactionTags": {"errors": [], "transaction": {"id": "txn_123", "tags": []}}}
    cleared = await call_tool("set_transaction_tags", {"transaction_id": "txn_123", "tag_ids": []})
    assert mock_api.await_args.kwargs["tag_ids"] == []
    assert cleared["tag_ids"] == []


@pytest.mark.parametrize(
    "response",
    [
        {"setTransactionTags": {"errors": {"message": "Rejected", "code": None}, "transaction": None}},
        {"setTransactionTags": {"errors": None, "transaction": None}},
        {},
    ],
)
async def test_set_transaction_tags_rejections(mock_api: AsyncMock, response: dict[str, Any]) -> None:
    mock_api.return_value = response
    with pytest.raises(ToolError):
        await server.set_transaction_tags("txn_123", ["tag_1"])


@pytest.mark.parametrize(("transaction_id", "tag_ids"), [(" ", ["tag_1"]), ("txn_123", ["tag_1", " "])])
async def test_set_transaction_tags_validates_before_api(
    mock_api: AsyncMock, transaction_id: str, tag_ids: list[str]
) -> None:
    with pytest.raises(ToolError):
        await server.set_transaction_tags(transaction_id, tag_ids)
    mock_api.assert_not_awaited()


async def test_create_tag_accepts_null_error_fields(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "createTransactionTag": {
            "tag": {"id": "tag_9", "name": "Travel", "color": "#112233", "__typename": "Tag"},
            "errors": {"message": None, "fieldErrors": [], "__typename": "PayloadError"},
        }
    }
    result = await call_tool("create_transaction_tag", {"name": "Travel", "color": "#112233"})
    assert result["tag"] == {"id": "tag_9", "name": "Travel", "color": "#112233"}


@pytest.mark.parametrize(
    "response",
    [
        {"createTransactionTag": {"tag": None, "errors": {"message": "Duplicate tag"}}},
        {"createTransactionTag": {"tag": None, "errors": None}},
    ],
)
async def test_create_tag_rejections(mock_api: AsyncMock, response: dict[str, Any]) -> None:
    mock_api.return_value = response
    with pytest.raises(ToolError):
        await server.create_transaction_tag("Travel")


@pytest.mark.parametrize(("name", "color"), [(" ", "#19D2A5"), ("Travel", "19D2A5"), ("Travel", "#19D2A")])
async def test_create_tag_validates_before_api(mock_api: AsyncMock, name: str, color: str) -> None:
    with pytest.raises(ToolError):
        await server.create_transaction_tag(name, color)
    mock_api.assert_not_awaited()


async def test_update_account_sends_only_changes(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {
        "updateAccount": {
            "errors": None,
            "account": {
                "id": "acc_1",
                "displayName": "Rainy Day",
                "displayBalance": 0.0,
                "includeInNetWorth": False,
                "type": {"name": "depository"},
                "subtype": {"name": "savings"},
                "institution": {"name": "Example Bank"},
            },
        }
    }
    result = await call_tool("update_account", {"account_id": "acc_1", "include_in_net_worth": False, "balance": 0})
    sent = mock_api.await_args.kwargs
    assert (sent["account_balance"], sent["include_in_net_worth"], sent["account_name"]) == (0, False, None)
    assert result["account"]["include_in_net_worth"] is False
    assert result["account"]["balance"] == 0.0
    assert "institution" not in result["account"]


@pytest.mark.parametrize(
    "response",
    [
        {"updateAccount": {"errors": {"message": "Rejected"}, "account": None}},
        {"updateAccount": {"errors": [], "account": None}},
    ],
)
async def test_update_account_rejections(mock_api: AsyncMock, response: dict[str, Any]) -> None:
    mock_api.return_value = response
    with pytest.raises(ToolError):
        await server.update_account("acc_1", name="Rainy Day")


@pytest.mark.parametrize("arguments", [{"account_id": "acc_1"}, {"account_id": "acc_1", "name": " "}])
async def test_update_account_validates_before_api(mock_api: AsyncMock, arguments: dict[str, str]) -> None:
    with pytest.raises(ToolError):
        await server.update_account(**arguments)
    mock_api.assert_not_awaited()


async def test_delete_transaction(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = True
    result = await call_tool("delete_transaction", {"transaction_id": "txn_123"})
    assert result == {"transaction_id": "txn_123", "deleted": True}

    mock_api.return_value = False
    with pytest.raises(ToolError):
        await server.delete_transaction("txn_123")


async def test_delete_merchant_requires_success(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {"deleteMerchant": {"success": True}}
    result = await call_tool("delete_merchant", {"merchant_id": "merchant_1", "move_to_merchant_id": "merchant_2"})
    assert mock_api.await_args.kwargs == {"merchant_id": "merchant_1", "move_to_merchant_id": "merchant_2"}
    assert result["deleted"] is True

    mock_api.return_value = {"deleteMerchant": {"success": False}}
    with pytest.raises(ToolError):
        await server.delete_merchant("merchant_1")


async def test_delete_merchant_rejects_self_merge_before_api(mock_api: AsyncMock) -> None:
    with pytest.raises(ToolError):
        await server.delete_merchant("merchant_1", "merchant_1")
    mock_api.assert_not_awaited()


async def test_goal_budget_normalizes_month(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {"setSavingsGoalBudgetAmount": {"success": True, "errors": None}}
    result = await call_tool(
        "set_savings_goal_budget_amount", {"goal_id": "goal_1", "amount": 50, "month": "2027-03-15"}
    )
    assert mock_api.await_args.kwargs == {
        "goal_id": "goal_1",
        "month": "2027-03-01",
        "amount": 50,
        "apply_to_future": False,
    }
    assert result["month"] == "2027-03-01"

    await call_tool("set_savings_goal_budget_amount", {"goal_id": "goal_1", "amount": 50})
    assert mock_api.await_args.kwargs["month"] == date.today().replace(day=1).isoformat()


@pytest.mark.parametrize(
    "response",
    [
        {"setSavingsGoalBudgetAmount": {"success": False, "errors": None}},
        {"setSavingsGoalBudgetAmount": {"success": True, "errors": {"message": "Rejected"}}},
    ],
)
async def test_goal_budget_rejections(mock_api: AsyncMock, response: dict[str, Any]) -> None:
    mock_api.return_value = response
    with pytest.raises(ToolError):
        await server.set_savings_goal_budget_amount("goal_1", 50.0)


async def test_set_budget_amount_month_and_future(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = {"updateOrCreateBudgetItem": {"budgetItem": {"id": "budget_1", "budgetAmount": 0}}}
    result = await call_tool(
        "set_budget_amount", {"category_id": "cat_001", "amount": 0, "month": "2027-02-20", "apply_to_future": True}
    )
    sent = mock_api.await_args.kwargs
    assert (sent["start_date"], sent["apply_to_future"], sent["amount"]) == ("2027-02-01", True, 0)
    assert result["month"] == "2027-02-01"

    await call_tool("set_budget_amount", {"category_id": "cat_001", "amount": 10})
    sent = mock_api.await_args.kwargs
    assert (sent["start_date"], sent["apply_to_future"]) == (date.today().replace(day=1).isoformat(), False)


async def test_set_budget_amount_rejects_null_budget_item(mock_api: AsyncMock) -> None:
    mock_api.return_value = {"updateOrCreateBudgetItem": {"budgetItem": None}}
    with pytest.raises(ToolError):
        await server.set_budget_amount("cat_001", 10.0)


def transactions_page(total: int | None, ids: list[str]) -> dict[str, Any]:
    results = [
        {
            "id": txn_id,
            "date": "2026-10-01",
            "amount": -4.5,
            "merchant": {"name": "Corner Deli"},
            "tags": [{"id": "tag_1", "name": "Travel"}] if txn_id == "txn_1" else [],
        }
        for txn_id in ids
    ]
    page: dict[str, Any] = {"results": results}
    if total is not None:
        page["totalCount"] = total
    return {"allTransactions": page}


async def test_get_transactions_paging_review_filter_and_tags(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = transactions_page(5, ["txn_1", "txn_2"])
    result = await call_tool("get_transactions", {"limit": 2, "offset": 0, "needs_review": True})
    assert mock_api.await_args.kwargs["needs_review"] is True
    assert (result["count"], result["total_count"], result["next_offset"]) == (2, 5, 2)
    assert result["transactions"][0]["tags"] == ["Travel"]
    assert "tags" not in result["transactions"][1]

    mock_api.return_value = transactions_page(5, ["txn_5"])
    last = await call_tool("get_transactions", {"limit": 2, "offset": 4})
    assert "needs_review" not in mock_api.await_args.kwargs
    assert last["next_offset"] is None


async def test_search_transactions_paging_without_total(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = transactions_page(None, ["txn_1", "txn_2"])
    full = await call_tool("search_transactions", {"query": "deli", "limit": 2, "needs_review": False})
    assert mock_api.await_args.kwargs["needs_review"] is False
    assert (full["total_count"], full["next_offset"]) == (None, 2)

    mock_api.return_value = transactions_page(None, ["txn_3"])
    short = await call_tool("search_transactions", {"query": "deli", "limit": 2, "offset": 2})
    assert short["next_offset"] is None
