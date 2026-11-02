"""Updated existing tools, with synthetic data only."""

from datetime import date
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import JsonValue

import server
from tests.conftest import CallTool


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


def transactions_page(total: int | None, ids: list[str]) -> dict[str, JsonValue]:
    results: list[JsonValue] = [
        {
            "id": transaction_id,
            "date": "2026-10-01",
            "amount": -4.5,
            "merchant": {"name": "Corner Deli"},
            "tags": [{"id": "tag_1", "name": "Travel"}] if transaction_id == "txn_1" else [],
        }
        for transaction_id in ids
    ]
    page: dict[str, JsonValue] = {"results": results}
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
