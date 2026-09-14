"""Recurring schedules through FastMCP and the real client, with offline GraphQL."""

from unittest.mock import AsyncMock

import pytest
from monarchmoney import MonarchMoney
from pydantic import JsonValue

import server


@pytest.fixture
def graphql(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = MonarchMoney()
    call = AsyncMock(return_value={"recurringTransactionItems": []})
    monkeypatch.setattr(client, "gql_call", call)
    monkeypatch.setattr(server, "mm_client", client)
    return call


@pytest.mark.parametrize(
    ("arguments", "expected_start", "expected_end"),
    [
        ({"start_date": "Feb 15 2028"}, "2028-02-15", "2028-02-29"),
        ({"end_date": "2027-12-15"}, "2027-12-01", "2027-12-15"),
        ({"start_date": "2027-12-15", "end_date": "2028-01-15"}, "2027-12-15", "2028-01-15"),
    ],
)
async def test_recurring_calendar_bounds(
    graphql: AsyncMock, arguments: dict[str, str], expected_start: str, expected_end: str
) -> None:
    await server.mcp._tool_manager.call_tool("get_recurring_transactions", arguments)
    variables = graphql.call_args.args[2]
    assert variables == {"startDate": expected_start, "endDate": expected_end}


async def test_recurring_reversed_dates_never_reach_monarch(graphql: AsyncMock) -> None:
    with pytest.raises(ValueError):
        await server.get_recurring_transactions("2028-03-01", "2028-02-01")
    graphql.assert_not_awaited()


async def test_recurring_update_preserves_false_zero_and_omitted_settings(graphql: AsyncMock) -> None:
    graphql.return_value = {
        "updateMerchant": {
            "errors": [],
            "merchant": {
                "id": "merchant_1",
                "name": "Example Merchant",
                "recurringTransactionStream": {"id": "stream_1", "isActive": False, "amount": 0},
            },
        }
    }
    _, structured = await server.mcp._tool_manager.call_tool(
        "update_recurring_transaction",
        {"merchant_id": "merchant_1", "merchant_name": "Example Merchant", "is_active": False, "amount": 0},
        convert_result=True,
    )
    # Exercise the client too: omitted fields must not clear an existing schedule.
    assert graphql.call_args.kwargs["variables"]["input"] == {
        "merchantId": "merchant_1",
        "name": "Example Merchant",
        "recurrence": {"isActive": False, "amount": 0},
    }
    assert structured["merchant"]["recurringTransactionStream"]["isActive"] is False


@pytest.mark.parametrize(
    "response",
    [
        {"updateMerchant": {"errors": [{"message": "Rejected", "code": "INVALID_INPUT"}], "merchant": None}},
        {"updateMerchant": {"errors": [], "merchant": None}},
        {},
    ],
)
async def test_recurring_update_cannot_report_false_success(graphql: AsyncMock, response: dict[str, JsonValue]) -> None:
    graphql.return_value = response
    with pytest.raises(ValueError):
        await server.update_recurring_transaction("merchant_1", "Example Merchant", is_active=False)


async def test_recurring_noop_never_reaches_monarch(graphql: AsyncMock) -> None:
    with pytest.raises(ValueError):
        await server.update_recurring_transaction("merchant_1", "Example Merchant")
    graphql.assert_not_awaited()
