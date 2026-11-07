"""Check tool output schemas, structured payloads, and text fallbacks over the MCP protocol.

Validate representative payloads against their Pydantic models.
"""

from unittest.mock import AsyncMock

import pytest
from mcp.client import Client
from mcp.types import Tool

import server


async def list_tools() -> dict[str, Tool]:
    async with Client(server.mcp) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


async def test_every_tool_advertises_title_annotations_and_output_schema() -> None:
    tools = await list_tools()
    assert [name for name, tool in tools.items() if tool.output_schema is None] == []
    assert [name for name, tool in tools.items() if not tool.title] == []
    assert [name for name, tool in tools.items() if tool.annotations is None] == []


async def test_annotations_state_read_only_and_destructive_hints() -> None:
    tools = await list_tools()
    unset = [
        name
        for name, tool in tools.items()
        if tool.annotations is None
        or tool.annotations.read_only_hint is None
        or tool.annotations.destructive_hint is None
    ]
    assert unset == []
    assert [
        name for name, tool in tools.items() if tool.annotations.read_only_hint and tool.annotations.destructive_hint
    ] == []
    # Replacing a whole set or deleting data removes what the caller didn't name.
    for name in ("update_transaction_splits", "set_transaction_tags", "delete_transaction", "delete_merchant"):
        assert tools[name].annotations.destructive_hint is True, name


@pytest.mark.parametrize(
    ("tool_name", "schema_props"),
    [
        ("get_accounts", {"accounts", "count"}),
        ("get_transactions", {"transactions", "count", "verbose"}),
        ("get_spending_summary", {"period", "group_by", "groups", "totals"}),
        ("update_transactions_bulk", {"summary", "results", "message"}),
        ("get_complete_financial_overview", {"period", "accounts", "transaction_summary"}),
    ],
)
async def test_output_schema_declares_expected_properties(tool_name: str, schema_props: set[str]) -> None:
    tool = (await list_tools())[tool_name]
    declared = set((tool.output_schema or {}).get("properties", {}).keys())
    assert schema_props.issubset(declared)


async def test_call_tool_returns_text_and_structured_content(mock_api: AsyncMock) -> None:
    """Tool calls yield structured content and a text fallback for older clients."""
    mock_api.return_value = [{"id": "acc_1", "displayName": "Checking"}]
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_accounts", {})

    assert result.content and result.content[0].type == "text"
    assert result.structured_content == {"accounts": [{"id": "acc_1", "displayName": "Checking"}], "count": 1}
    server.AccountsResult.model_validate(result.structured_content)


async def test_structured_content_validates_for_derived_model(mock_api: AsyncMock) -> None:
    """Spending summary structured content validates against its precise model."""
    mock_api.return_value = [
        {"amount": -40.0, "category": {"name": "Groceries"}, "date": "2024-01-05"},
        {"amount": 2000.0, "category": {"name": "Income"}, "date": "2024-01-01"},
    ]
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_spending_summary", {"group_by": "category"})
    parsed = server.SpendingSummaryResult.model_validate(result.structured_content)
    assert parsed.totals.income == 2000.0
    assert parsed.groups["Groceries"].expenses == 40.0
    assert parsed.groups["Groceries"].count == 1
