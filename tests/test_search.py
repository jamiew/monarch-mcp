"""search_transactions forwards Monarch's text search and shares get_transactions' row formats."""

from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import server
from tests.conftest import CallTool

ROW = {
    "id": "txn_1",
    "date": "2024-01-15",
    "amount": -50.0,
    "merchant": {"name": "Corner Deli", "id": "merchant_1"},
    "plaidName": "CORNER DELI #123",
    "category": {"name": "Dining", "id": "cat_1"},
    "account": {"displayName": "Everyday Checking", "id": "acc_1"},
    "pending": False,
    "needsReview": False,
    "extraField": "extra data",
}


@pytest.mark.parametrize("tool_name", ["get_transactions", "search_transactions"])
async def test_filters_reach_monarch_as_strings_and_ids(
    mock_api: AsyncMock, call_tool: CallTool, tool_name: str
) -> None:
    mock_api.return_value = [ROW]
    arguments = {"start_date": "2024-01-01", "end_date": "2024-01-31", "account_id": "acc_1", "category_id": "cat_1"}
    if tool_name == "search_transactions":
        arguments["query"] = "Corner"

    await call_tool(tool_name, arguments)

    sent = mock_api.await_args.kwargs
    assert (sent["start_date"], sent["end_date"]) == ("2024-01-01", "2024-01-31")
    assert (sent["account_ids"], sent["category_ids"]) == (["acc_1"], ["cat_1"])
    if tool_name == "search_transactions":
        assert sent["search"] == "Corner"


@pytest.mark.parametrize("query", ["", "   "])
async def test_blank_query_is_rejected_before_api(mock_api: AsyncMock, query: str) -> None:
    with pytest.raises(ToolError):
        await server.search_transactions(query=query)
    mock_api.assert_not_awaited()


async def test_compact_rows_match_get_transactions_and_verbose_keeps_objects(
    mock_api: AsyncMock, call_tool: CallTool
) -> None:
    mock_api.return_value = [ROW]

    compact = (await call_tool("search_transactions", {"query": "Corner"}))["transactions"][0]
    listed = (await call_tool("get_transactions", {}))["transactions"][0]
    verbose = (await call_tool("search_transactions", {"query": "Corner", "verbose": True}))["transactions"][0]

    assert compact == listed
    assert compact["merchant"] == "Corner Deli"
    assert "extraField" not in compact
    assert verbose == ROW
