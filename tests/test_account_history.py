"""Account history filters snapshots locally, then pages the filtered list."""

from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from monarchmoney import MonarchMoney
from pydantic import JsonValue

import server


@pytest.fixture
def history_graphql(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = MonarchMoney()
    call = AsyncMock()
    monkeypatch.setattr(client, "gql_call", call)
    monkeypatch.setattr(server, "mm_client", client)
    return call


@pytest.mark.parametrize(
    ("start_date", "end_date", "expected_indices"),
    [
        ("2026-09-01", "2026-09-30", [1, 2]),
        ("2026-09-01", "2026-09-01", [1]),
        ("2026-09-01", None, [1, 2, 3]),
        (None, "2026-09-30", [0, 1, 2]),
        (None, None, [0, 1, 2, 3]),
        ("2026-09-02", "2026-09-29", []),
    ],
)
async def test_get_account_history_filters_snapshots(
    history_graphql: AsyncMock,
    start_date: str | None,
    end_date: str | None,
    expected_indices: list[int],
) -> None:
    snapshots: list[dict[str, JsonValue]] = [
        {"date": "2026-08-31", "signedBalance": 900, "source": {"kind": "synthetic"}},
        {"date": "2026-09-01", "signedBalance": 1000, "source": {"kind": "synthetic"}},
        {"date": "2026-09-30", "signedBalance": 1050, "source": {"kind": "synthetic"}},
        {"date": "2026-10-01", "signedBalance": 1100, "source": {"kind": "synthetic"}},
    ]
    expected_history = [
        {**snapshots[index], "accountId": "acc_001", "accountName": "Synthetic account"} for index in expected_indices
    ]
    history_graphql.return_value = {
        "account": {"displayName": "Synthetic account"},
        "snapshots": snapshots,
    }

    result = await server.get_account_history(account_id="acc_001", start_date=start_date, end_date=end_date)

    assert result.account_id == "acc_001"
    assert result.history == expected_history


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        ("2026-09-30", "2026-09-01"),
        ("2026-02-30", None),
        (None, "not-a-date"),
        ("last month", None),
    ],
)
async def test_get_account_history_rejects_invalid_bounds_before_api(
    history_graphql: AsyncMock, start_date: str | None, end_date: str | None
) -> None:
    with pytest.raises(ToolError):
        await server.get_account_history(account_id="acc_001", start_date=start_date, end_date=end_date)
    history_graphql.assert_not_awaited()


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
async def test_get_account_history_rejects_invalid_paging_before_api(
    mock_api: AsyncMock, arguments: dict[str, int]
) -> None:
    with pytest.raises(ToolError):
        await server.get_account_history("acc_001", **arguments)
    mock_api.assert_not_awaited()


async def test_get_account_history_filters_before_paging_in_upstream_order(history_graphql: AsyncMock) -> None:
    snapshots: list[dict[str, JsonValue]] = [
        {"date": "2026-10-01", "signedBalance": 1100},
        {"date": "2026-09-30", "signedBalance": 1050},
        {"date": "2026-08-31", "signedBalance": 900},
        {"date": "2026-09-01", "signedBalance": 1000},
        {"date": "2026-09-15", "signedBalance": 1025},
    ]
    history_graphql.return_value = {
        "account": {"displayName": "Synthetic account"},
        "snapshots": snapshots,
    }
    expected = [{**snapshots[index], "accountId": "acc_001", "accountName": "Synthetic account"} for index in (1, 3, 4)]

    first = await server.get_account_history("acc_001", start_date="2026-09-01", end_date="2026-09-30", limit=2)
    assert first.history == expected[:2]
    assert (first.count, first.total_count, first.offset, first.next_offset) == (2, 3, 0, 2)

    final = await server.get_account_history(
        "acc_001", start_date="2026-09-01", end_date="2026-09-30", limit=2, offset=2
    )
    assert final.history == expected[2:]
    assert (final.count, final.total_count, final.offset, final.next_offset) == (1, 3, 2, None)

    empty = await server.get_account_history(
        "acc_001", start_date="2026-09-01", end_date="2026-09-30", limit=2, offset=4
    )
    assert empty.history == []
    assert (empty.count, empty.total_count, empty.offset, empty.next_offset) == (0, 3, 4, None)
