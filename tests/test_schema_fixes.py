"""Transaction behavior through FastMCP and the real client, with offline GraphQL."""

import json
from unittest.mock import AsyncMock

import pytest
from monarchmoney import MonarchMoney
from pydantic import JsonValue

import server


@pytest.fixture
def graphql(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = MonarchMoney()
    call = AsyncMock()
    monkeypatch.setattr(client, "gql_call", call)
    monkeypatch.setattr(server, "mm_client", client)
    return call


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_updates_preserve_omitted_fields_and_apply_false_and_empty_values(graphql: AsyncMock, bulk: bool) -> None:
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
        _, structured = await server.mcp._tool_manager.call_tool("update_transaction", arguments, convert_result=True)
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
        await server.mcp._tool_manager.call_tool("update_transaction", arguments)

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
async def test_pending_filter_distinguishes_posted_pending_and_unfiltered(graphql: AsyncMock, tool_name: str) -> None:
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
        _, structured = await server.mcp._tool_manager.call_tool(
            tool_name, {**arguments, **filter_arguments}, convert_result=True
        )
        assert structured["transactions"] == expected
        if tool_name == "search_transactions":
            assert structured["search_metadata"]["result_count"] == len(expected)
        else:
            assert structured["count"] == len(expected)


async def test_empty_merchant_name_fails_before_creation(graphql: AsyncMock) -> None:
    with pytest.raises(ValueError):
        await server.create_transaction(
            amount=-50.00, merchant_name="", account_id="acc_123", date="2024-01-15", category_id="cat_456"
        )
    graphql.assert_not_awaited()
