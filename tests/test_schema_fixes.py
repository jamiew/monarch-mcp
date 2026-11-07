"""Transaction create/update/filter behavior through MCPServer, mostly via the real client with offline GraphQL."""

import json
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from monarchmoney import MonarchMoney
from pydantic import JsonValue

import server
from tests.conftest import CallTool


@pytest.fixture
def graphql(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = MonarchMoney()
    call = AsyncMock()
    monkeypatch.setattr(client, "gql_call", call)
    monkeypatch.setattr(server, "mm_client", client)
    return call


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_updates_preserve_omitted_fields_and_apply_false_and_empty_values(
    graphql: AsyncMock, call_tool: CallTool, bulk: bool
) -> None:
    transaction: dict[str, JsonValue] = {
        "id": "txn_123",
        "name": "Corner Deli",
        "category": "cat_001",
        "goalId": "goal_old",
        "hideFromReports": True,
        "needsReview": True,
        "notes": "Old note",
    }

    async def mutate(operation: str, graphql_query: object, variables: dict[str, JsonValue]) -> dict[str, JsonValue]:
        changes = variables["input"]
        if not isinstance(changes, dict):
            raise TypeError("Expected mutation input")
        # Monarch ignores null category/name, but accepts empty goal IDs and notes.
        transaction.update({key: value for key, value in changes.items() if value is not None})
        return {"updateTransaction": {"errors": [], "transaction": transaction.copy()}}

    graphql.side_effect = mutate
    arguments: dict[str, JsonValue] = {
        "transaction_id": "txn_123",
        "merchant_name": "Market Stall",
        "goal_id": "goal_new",
        "hide_from_reports": False,
        "needs_review": False,
        "notes": "Updated note",
    }
    if bulk:
        result = await server.update_transactions_bulk(json.dumps([arguments]))
        assert result.summary.succeeded == 1
    else:
        structured = await call_tool("update_transaction", arguments)
        assert structured["transaction"] == {"updateTransaction": {"errors": [], "transaction": transaction}}

    assert transaction == {
        "id": "txn_123",
        "name": "Market Stall",
        "category": "cat_001",
        "goalId": "goal_new",
        "hideFromReports": False,
        "needsReview": False,
        "notes": "Updated note",
    }

    arguments = {"transaction_id": "txn_123", "merchant_name": None, "goal_id": "", "notes": ""}
    if bulk:
        result = await server.update_transactions_bulk(json.dumps([arguments]))
        assert result.summary.succeeded == 1
    else:
        await call_tool("update_transaction", arguments)

    assert transaction == {
        "id": "txn_123",
        "name": "Market Stall",
        "category": "cat_001",
        "goalId": "",
        "hideFromReports": False,
        "needsReview": False,
        "notes": "",
    }


@pytest.mark.parametrize("tool_name", ["get_transactions", "search_transactions"])
async def test_pending_filter_distinguishes_posted_pending_and_unfiltered(
    graphql: AsyncMock, call_tool: CallTool, tool_name: str
) -> None:
    rows: list[JsonValue] = [
        {"id": "txn_posted", "pending": False, "merchant": {"name": "Corner Deli"}},
        {"id": "txn_pending", "pending": True, "merchant": {"name": "Corner Deli"}},
    ]

    async def query(operation: str, graphql_query: object, variables: dict[str, JsonValue]) -> dict[str, JsonValue]:
        filters = variables["filters"]
        if not isinstance(filters, dict):
            raise TypeError("Expected transaction filters")
        pending = filters.get("isPending")
        selected = [row for row in rows if isinstance(row, dict) and (pending is None or row["pending"] is pending)]
        return {"allTransactions": {"results": selected}}

    graphql.side_effect = query
    arguments: dict[str, JsonValue] = {"verbose": True}
    if tool_name == "search_transactions":
        arguments["query"] = "Corner Deli"

    for filter_arguments, expected in [
        ({}, rows),
        ({"is_pending": False}, [rows[0]]),
        ({"is_pending": True}, [rows[1]]),
        ({"is_pending": None}, rows),
    ]:
        structured = await call_tool(tool_name, {**arguments, **filter_arguments})
        assert structured["transactions"] == expected
        if tool_name == "search_transactions":
            assert structured["search_metadata"]["result_count"] == len(expected)
        else:
            assert structured["count"] == len(expected)


async def test_empty_merchant_name_fails_before_creation(graphql: AsyncMock) -> None:
    with pytest.raises(ToolError):
        await server.create_transaction(
            amount=-50.00, merchant_name="", account_id="acc_123", date="2024-01-15", category_id="cat_456"
        )
    graphql.assert_not_awaited()


async def test_create_transaction_sends_fields_without_changing_balance(
    mock_api: AsyncMock, call_tool: CallTool
) -> None:
    mock_api.return_value = {"createTransaction": {"errors": None, "transaction": {"id": "txn_new"}}}
    await call_tool(
        "create_transaction",
        {
            "amount": -12.5,
            "merchant_name": "Corner Deli",
            "account_id": "acc_123",
            "date": "2024-01-15",
            "category_id": "cat_456",
            "notes": "Lunch",
        },
    )
    assert mock_api.await_args.kwargs == {
        "amount": -12.5,
        "merchant_name": "Corner Deli",
        "category_id": "cat_456",
        "account_id": "acc_123",
        "date": "2024-01-15",
        "notes": "Lunch",
        "update_balance": False,
    }


@pytest.mark.parametrize(
    "response",
    [
        {"createTransaction": {"errors": {"message": "Invalid account", "code": "BAD_INPUT"}, "transaction": None}},
        {"createTransaction": {"errors": None, "transaction": None}},
    ],
    ids=["rejected", "missing"],
)
async def test_create_transaction_fails_when_monarch_creates_nothing(mock_api: AsyncMock, response: JsonValue) -> None:
    mock_api.return_value = response
    with pytest.raises(ToolError):
        await server.create_transaction(
            amount=-12.5, merchant_name="Corner Deli", account_id="acc_123", date="2024-01-15", category_id="cat_456"
        )
