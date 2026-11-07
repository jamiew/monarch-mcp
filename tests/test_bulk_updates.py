"""Bulk transaction updates validate and fail per item, and run with bounded concurrency."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from monarchmoney import RequestFailedException

import server
from tests.conftest import CallTool


def updated(transaction_id: str) -> dict[str, Any]:
    return {"updateTransaction": {"errors": [], "transaction": {"id": transaction_id}}}


async def test_upstream_failure_fails_only_that_item(mock_api: AsyncMock) -> None:
    async def update(method_name: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs["transaction_id"] == "txn_999":
            raise RequestFailedException("Transaction not found")
        return updated(kwargs["transaction_id"])

    mock_api.side_effect = update
    updates = [{"transaction_id": "txn_123", "amount": 50.0}, {"transaction_id": "txn_999", "amount": 100.0}]

    result = await server.update_transactions_bulk(json.dumps(updates))

    assert (result.summary.total, result.summary.succeeded, result.summary.failed) == (2, 1, 1)
    assert [(item.transaction_id, item.status) for item in result.results] == [
        ("txn_123", "success"),
        ("txn_999", "error"),
    ]
    assert "not found" in (result.results[1].error or "")


async def test_unexpected_item_error_keeps_details_out_of_the_result(mock_api: AsyncMock) -> None:
    mock_api.side_effect = KeyError("internal_field")

    result = await server.update_transactions_bulk(json.dumps([{"transaction_id": "txn_1", "notes": "x"}]))

    assert result.results[0].status == "error"
    assert "internal_field" not in (result.results[0].error or "")


@pytest.mark.parametrize("updates", ["not valid json {", '{"transaction_id": "123"}'])
async def test_payload_must_be_a_json_array(mock_api: AsyncMock, updates: str) -> None:
    with pytest.raises(ToolError):
        await server.update_transactions_bulk(updates)
    mock_api.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid_update",
    [
        None,
        [],
        "not an update",
        {"amount": 50},
        {"transaction_id": {"id": "txn_bad"}, "amount": 50},
        {"transaction_id": 123, "amount": 50},
        {"transaction_id": "", "amount": 50},
        {"transaction_id": "txn_bad", "hide_from_reports": "false"},
        {"transaction_id": "txn_bad", "needs_review": 1},
        {"transaction_id": "txn_bad", "amount": True},
        {"transaction_id": "txn_bad", "amount": "50"},
        {"transaction_id": "txn_bad", "amount": 0.0},
        {"transaction_id": "txn_bad", "amount": float("inf")},
        {"transaction_id": "txn_bad", "amount": float("nan")},
        {"transaction_id": "txn_bad", "merchant_name": {"name": "Wrong type"}},
        {"transaction_id": "txn_bad", "date": "2024-02-30"},
        {"transaction_id": "txn_bad", "amount": 50, "unknown_field": True},
    ],
)
async def test_malformed_item_does_not_mutate_or_abort_batch(mock_api: AsyncMock, invalid_update: object) -> None:
    mock_api.return_value = updated("txn_valid")
    updates = json.dumps([invalid_update, {"transaction_id": "txn_valid", "amount": 25}])

    result = await server.update_transactions_bulk(updates)

    assert (result.summary.total, result.summary.succeeded, result.summary.failed) == (2, 1, 1)
    assert [item.status for item in result.results] == ["error", "success"]
    assert result.results[1].transaction_id == "txn_valid"
    assert mock_api.await_count == 1
    assert mock_api.await_args.kwargs["transaction_id"] == "txn_valid"


async def test_empty_array_updates_nothing(mock_api: AsyncMock) -> None:
    result = await server.update_transactions_bulk("[]")

    assert result.results == []
    assert result.summary.total == 0
    mock_api.assert_not_awaited()


async def test_items_run_concurrently_up_to_the_cap(mock_api: AsyncMock) -> None:
    active = 0
    peak = 0

    async def update(method_name: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return updated(kwargs["transaction_id"])

    mock_api.side_effect = update
    updates = [{"transaction_id": f"txn_{i}", "amount": 10.0} for i in range(server.BULK_UPDATE_CONCURRENCY * 3)]

    result = await server.update_transactions_bulk(updates)

    assert result.summary.succeeded == len(updates)
    assert peak == server.BULK_UPDATE_CONCURRENCY


async def test_oversized_batch_is_rejected_before_login(mock_api: AsyncMock) -> None:
    updates = [{"transaction_id": f"txn_{i}", "amount": 10.0} for i in range(server.BULK_UPDATE_MAX_ITEMS + 1)]

    with pytest.raises(ToolError):
        await server.update_transactions_bulk(updates)

    assert isinstance(server.ensure_authenticated, AsyncMock)
    server.ensure_authenticated.assert_not_awaited()
    mock_api.assert_not_awaited()


async def test_accepts_a_structured_array_over_mcp(mock_api: AsyncMock, call_tool: CallTool) -> None:
    mock_api.return_value = updated("txn_1")

    result = await call_tool(
        "update_transactions_bulk", {"updates": [{"transaction_id": "txn_1", "notes": ""}, {"amount": 5}]}
    )

    assert result["summary"] == {"total": 2, "succeeded": 1, "failed": 1}
    mock_api.assert_awaited_once_with("update_transaction", transaction_id="txn_1", notes="")
