"""Ownership and mutation failures through the real Monarch client, without network calls."""

import json
from unittest.mock import AsyncMock

import pytest
from aiohttp.payload import JsonPayload
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


@pytest.fixture
def transactions(graphql: AsyncMock) -> dict[str, dict[str, JsonValue]]:
    rows: dict[str, dict[str, JsonValue]] = {
        "txn_123": {"id": "txn_123", "ownerUserId": "member_original", "notes": "Original note"},
        "txn_valid": {"id": "txn_valid", "ownerUserId": "member_original"},
    }

    async def mutate(operation: str, graphql_query: object, variables: dict[str, JsonValue]) -> dict[str, JsonValue]:
        JsonPayload(variables)
        changes = variables["input"]
        if not isinstance(changes, dict) or not isinstance(changes.get("id"), str):
            raise TypeError("Expected transaction mutation input")
        transaction_id = changes["id"]
        if not isinstance(transaction_id, str):
            raise TypeError("Expected transaction ID")
        row = rows[transaction_id]
        if "ownerUserId" in changes:
            row["ownerUserId"] = changes["ownerUserId"]
        if "notes" in changes:
            row["notes"] = changes["notes"]
        if "date" in changes:
            row["date"] = changes["date"]
        return {"updateTransaction": {"errors": None, "transaction": row.copy()}}

    graphql.side_effect = mutate
    return rows


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_owner_assignment_clear_and_omission(transactions: dict[str, dict[str, JsonValue]], bulk: bool) -> None:
    steps: list[tuple[dict[str, JsonValue], str | None]] = [
        ({"owner_user_id": "member_new"}, "member_new"),
        ({"notes": "Omitted owner"}, "member_new"),
        ({"notes": "Null owner", "owner_user_id": None}, "member_new"),
        ({"owner_user_id": ""}, None),
    ]
    for changes, expected_owner in steps:
        arguments = {"transaction_id": "txn_123", **changes}
        if bulk:
            result = await server.update_transactions_bulk(json.dumps([arguments]))
            assert result.summary.succeeded == 1
            assert result.summary.failed == 0
        else:
            _, structured = await server.mcp._tool_manager.call_tool(
                "update_transaction", arguments, convert_result=True
            )
            assert structured["transaction"] == {
                "updateTransaction": {"errors": None, "transaction": transactions["txn_123"]}
            }
        assert transactions["txn_123"]["ownerUserId"] == expected_owner
    assert transactions["txn_123"]["notes"] == "Null owner"


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_date_update_survives_json_transport(transactions: dict[str, dict[str, JsonValue]], bulk: bool) -> None:
    arguments = {"transaction_id": "txn_123", "date": "2026-09-01"}
    if bulk:
        result = await server.update_transactions_bulk(json.dumps([arguments]))
        assert result.summary.succeeded == 1
        assert result.summary.failed == 0
    else:
        await server.mcp._tool_manager.call_tool("update_transaction", arguments)
    assert transactions["txn_123"]["date"] == "2026-09-01"


@pytest.mark.parametrize("invalid_owner", [123, False, ["member_new"], {"id": "member_new"}])
async def test_invalid_bulk_owner_does_not_mutate_or_abort_valid_item(
    transactions: dict[str, dict[str, JsonValue]], invalid_owner: JsonValue
) -> None:
    result = await server.update_transactions_bulk(
        json.dumps(
            [
                {"transaction_id": "txn_123", "owner_user_id": invalid_owner, "notes": "Must not apply"},
                {"transaction_id": "txn_valid", "owner_user_id": ""},
            ]
        )
    )
    assert result.summary.total == 2
    assert result.summary.failed == 1
    assert result.summary.succeeded == 1
    assert [(item.transaction_id, item.status) for item in result.results] == [
        ("txn_123", "error"),
        ("txn_valid", "success"),
    ]
    assert transactions["txn_123"] == {"id": "txn_123", "ownerUserId": "member_original", "notes": "Original note"}
    assert transactions["txn_valid"]["ownerUserId"] is None


@pytest.mark.parametrize(
    "response",
    [
        {
            "updateTransaction": {
                "errors": [{"message": "Ownership rejected", "code": "INVALID_INPUT"}],
                "transaction": {"id": "txn_123"},
            }
        },
        {},
        {"updateTransaction": None},
        {"updateTransaction": {"errors": []}},
        {"updateTransaction": {"errors": [], "transaction": None}},
    ],
    ids=[
        "nested-rejection-with-transaction",
        "missing-payload",
        "null-payload",
        "missing-transaction",
        "null-transaction",
    ],
)
async def test_mutation_failure_never_reports_success_and_is_isolated_in_bulk(
    graphql: AsyncMock, response: dict[str, JsonValue]
) -> None:
    graphql.return_value = response
    with pytest.raises(ValueError):
        await server.update_transaction("txn_123", owner_user_id="member_new")

    valid_response = {"updateTransaction": {"errors": [], "transaction": {"id": "txn_valid"}}}

    async def mutate(operation: str, graphql_query: object, variables: dict[str, JsonValue]) -> dict[str, JsonValue]:
        changes = variables["input"]
        if not isinstance(changes, dict):
            raise TypeError("Expected mutation input")
        return response if changes["id"] == "txn_123" else valid_response

    graphql.side_effect = mutate
    result = await server.update_transactions_bulk(
        json.dumps(
            [
                {"transaction_id": "txn_123", "owner_user_id": "member_new"},
                {"transaction_id": "txn_valid", "owner_user_id": ""},
            ]
        )
    )
    assert result.summary.total == 2
    assert result.summary.succeeded == 1
    assert result.summary.failed == 1
    assert [(item.transaction_id, item.status) for item in result.results] == [
        ("txn_123", "error"),
        ("txn_valid", "success"),
    ]
