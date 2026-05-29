"""Tests for MCP structured tool output (outputSchema + structured content).

Every tool returns a Pydantic model so FastMCP advertises an ``outputSchema`` and
emits machine-readable structured content alongside a text fallback. These tests
assert the schema is present for all tools and that a representative call produces
both content forms, with the structured payload validating against the tool's model.
"""

from unittest.mock import AsyncMock

import pytest

import server

mgr = server.mcp._tool_manager


def test_every_tool_advertises_output_schema() -> None:
    """All registered tools must expose a non-null outputSchema."""
    without_schema = [t.name for t in mgr.list_tools() if getattr(t, "output_schema", None) is None]
    assert without_schema == []


def test_tool_count_is_nineteen() -> None:
    assert len(mgr.list_tools()) == 19


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
def test_output_schema_declares_expected_properties(tool_name: str, schema_props: set[str]) -> None:
    tool = mgr.get_tool(tool_name)
    declared = set((tool.output_schema or {}).get("properties", {}).keys())
    assert schema_props.issubset(declared)


@pytest.mark.asyncio
async def test_call_tool_returns_text_and_structured_content(mock_api: AsyncMock) -> None:
    """A converted tool yields both a text fallback and structured content."""
    mock_api.return_value = [{"id": "acc_1", "displayName": "Checking"}]
    content, structured = await mgr.call_tool("get_accounts", {}, convert_result=True)

    # Text fallback present for older clients.
    assert content and content[0].type == "text"
    # Structured content matches the AccountsResult shape and re-validates.
    assert structured == {"accounts": [{"id": "acc_1", "displayName": "Checking"}], "count": 1}
    server.AccountsResult.model_validate(structured)


@pytest.mark.asyncio
async def test_structured_content_validates_for_derived_model(mock_api: AsyncMock) -> None:
    """Spending summary structured content validates against its precise model."""
    mock_api.return_value = [
        {"amount": -40.0, "category": {"name": "Groceries"}, "date": "2024-01-05"},
        {"amount": 2000.0, "category": {"name": "Income"}, "date": "2024-01-01"},
    ]
    _content, structured = await mgr.call_tool("get_spending_summary", {"group_by": "category"}, convert_result=True)
    parsed = server.SpendingSummaryResult.model_validate(structured)
    assert parsed.totals.income == 2000.0
    assert parsed.groups["Groceries"].expenses == 40.0
    assert parsed.groups["Groceries"].count == 1
