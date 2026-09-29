"""Tests for transaction details and split tools.

Uses the ``mock_api`` fixture (see conftest.py) so tests are offline: it patches
``ensure_authenticated`` and ``api_call_with_retry``.
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue, ValidationError

import server


def _split_response(splits: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    """Shape returned by the library's get_transaction_splits."""
    return {
        "getTransaction": {
            "id": "txn_1",
            "amount": -100.0,
            "splitTransactions": splits,
        }
    }


def _update_response(splits: list[dict[str, JsonValue]], has_splits: bool) -> dict[str, JsonValue]:
    """Shape returned by the library's update_transaction_splits."""
    return {
        "updateTransactionSplit": {
            "errors": None,
            "transaction": {
                "id": "txn_1",
                "hasSplitTransactions": has_splits,
                "splitTransactions": splits,
            },
        }
    }


class TestGetTransactionSplits:
    @pytest.mark.asyncio
    async def test_returns_split_legs(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _split_response(
            [
                {"id": "s1", "amount": -70.0, "category": {"id": "cat_1", "name": "Groceries"}},
                {"id": "s2", "amount": -30.0, "category": {"id": "cat_2", "name": "Household"}},
            ]
        )
        result = await server.get_transaction_splits(transaction_id="txn_1")
        assert result.transaction_id == "txn_1"
        assert result.has_split_transactions is True
        assert len(result.splits) == 2
        mock_api.assert_awaited_once_with("get_transaction_splits", transaction_id="txn_1")

    @pytest.mark.asyncio
    async def test_unsplit_transaction_has_empty_splits(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _split_response([])
        result = await server.get_transaction_splits(transaction_id="txn_1")
        assert result.has_split_transactions is False
        assert result.splits == []


class TestUpdateTransactionSplits:
    @pytest.mark.asyncio
    async def test_creates_splits_and_translates_payload(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _update_response(
            [{"id": "s1", "amount": -70.0}, {"id": "s2", "amount": -30.0}], has_splits=True
        )
        result = await server.update_transaction_splits(
            transaction_id="txn_1",
            splits=[
                server.TransactionSplit(amount=-70.0, category_id="cat_1", notes="Food"),
                server.TransactionSplit(amount=-30.0, category_id="cat_2"),
            ],
        )
        assert result.has_split_transactions is True
        assert len(result.splits) == 2
        assert "Set 2 split(s)" in result.message

        # Inputs are translated into the camelCase shape the API expects.
        _, kwargs = mock_api.await_args
        sent = kwargs["split_data"]
        assert sent[0] == {"amount": -70.0, "merchantName": "", "categoryId": "cat_1", "notes": "Food"}
        assert sent[1] == {"amount": -30.0, "merchantName": "", "categoryId": "cat_2"}

    @pytest.mark.asyncio
    async def test_passes_merchant_name_when_provided(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _update_response([{"id": "s1", "amount": -100.0}], has_splits=True)
        await server.update_transaction_splits(
            transaction_id="txn_1",
            splits=[server.TransactionSplit(amount=-100.0, merchant_name="Corner Deli")],
        )
        _, kwargs = mock_api.await_args
        assert kwargs["split_data"][0]["merchantName"] == "Corner Deli"

    @pytest.mark.asyncio
    async def test_empty_list_removes_all_splits(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _update_response([], has_splits=False)
        result = await server.update_transaction_splits(transaction_id="txn_1", splits=[])
        assert result.has_split_transactions is False
        assert result.splits == []
        assert "Removed all splits" in result.message
        _, kwargs = mock_api.await_args
        assert kwargs["split_data"] == []

    @pytest.mark.asyncio
    async def test_api_payload_errors_raise(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "updateTransactionSplit": {
                "errors": {"message": "Split amounts must sum to the transaction total"},
                "transaction": None,
            }
        }
        with pytest.raises(ValueError, match="Monarch rejected the split update"):
            await server.update_transaction_splits(
                transaction_id="txn_1",
                splits=[server.TransactionSplit(amount=-10.0, category_id="cat_1")],
            )


def _details_response(
    *,
    transaction_id: str = "txn_leg",
    is_leg: bool,
    parent_id: str | None,
    split_count: int,
) -> dict[str, JsonValue]:
    """Shape returned by the library's get_transaction_details."""
    return {
        "getTransaction": {
            "id": transaction_id,
            "amount": -100.0,
            "merchant": {"id": "m1", "name": "Corner Deli"},
            "category": {"id": "cat_1"},
            "hasSplitTransactions": not is_leg and split_count > 0,
            "isSplitTransaction": is_leg,
            "originalTransaction": {"id": parent_id} if parent_id else None,
            "splitTransactions": [{"id": f"s{i}"} for i in range(split_count)],
        }
    }


class TestGetTransactionDetails:
    @pytest.mark.asyncio
    async def test_leg_reports_parent_id(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _details_response(is_leg=True, parent_id="txn_parent", split_count=0)
        result = await server.get_transaction_details(transaction_id="txn_leg")
        assert result.transaction_id == "txn_leg"
        assert result.is_split_transaction is True
        assert result.parent_transaction_id == "txn_parent"
        assert result.has_split_transactions is False
        assert result.split_transaction_ids == []
        assert result.amount == -100.0
        assert result.merchant == "Corner Deli"
        assert result.category_id == "cat_1"
        mock_api.assert_awaited_once_with("get_transaction_details", transaction_id="txn_leg")

    @pytest.mark.asyncio
    async def test_parent_reports_no_parent_and_leg_ids(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _details_response(
            transaction_id="txn_parent", is_leg=False, parent_id=None, split_count=3
        )
        result = await server.get_transaction_details(transaction_id="txn_parent")
        assert result.is_split_transaction is False
        assert result.parent_transaction_id is None
        assert result.has_split_transactions is True
        assert result.split_transaction_ids == ["s0", "s1", "s2"]

    @pytest.mark.asyncio
    async def test_unsplit_transaction(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _details_response(
            transaction_id="txn_plain", is_leg=False, parent_id=None, split_count=0
        )
        result = await server.get_transaction_details(transaction_id="txn_plain")
        assert result.is_split_transaction is False
        assert result.parent_transaction_id is None
        assert result.has_split_transactions is False
        assert result.split_transaction_ids == []

    @pytest.mark.asyncio
    async def test_returns_resolved_posted_id(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = _details_response(
            transaction_id="txn_posted", is_leg=False, parent_id=None, split_count=0
        )
        result = await server.get_transaction_details(transaction_id="txn_pending")
        assert result.transaction_id == "txn_posted"

    @pytest.mark.asyncio
    async def test_nullable_details(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {
            "getTransaction": {
                "id": "txn_plain",
                "amount": None,
                "merchant": None,
                "category": None,
                "hasSplitTransactions": False,
                "isSplitTransaction": False,
                "originalTransaction": None,
                "splitTransactions": [],
            }
        }
        result = await server.get_transaction_details(transaction_id="txn_plain")
        assert result.amount is None
        assert result.merchant is None
        assert result.category_id is None
        assert result.split_transaction_ids == []


@pytest.mark.parametrize("tool_name", ["get_transaction_details", "get_transaction_splits"])
@pytest.mark.parametrize("response", [{}, {"getTransaction": None}, None, []])
async def test_missing_transaction_raises(mock_api: AsyncMock, tool_name: str, response: JsonValue) -> None:
    mock_api.return_value = response
    with pytest.raises(ValueError):
        await getattr(server, tool_name)(transaction_id="txn_missing")


@pytest.mark.parametrize("tool_name", ["get_transaction_details", "get_transaction_splits"])
@pytest.mark.parametrize(
    "transaction",
    [
        {},
        {"id": "txn_1", "splitTransactions": "invalid"},
        {"id": "", "splitTransactions": []},
        {"id": "txn_1"},
    ],
)
async def test_malformed_transaction_raises(mock_api: AsyncMock, tool_name: str, transaction: JsonValue) -> None:
    mock_api.return_value = {"getTransaction": transaction}
    with pytest.raises(ValidationError):
        await getattr(server, tool_name)(transaction_id="txn_1")


@pytest.mark.parametrize("split", [{}, {"id": ""}, {"id": None}, "invalid"])
async def test_details_rejects_invalid_split_ids(mock_api: AsyncMock, split: JsonValue) -> None:
    mock_api.return_value = {
        "getTransaction": {
            "id": "txn_parent",
            "amount": -100.0,
            "merchant": None,
            "category": None,
            "hasSplitTransactions": True,
            "isSplitTransaction": False,
            "originalTransaction": None,
            "splitTransactions": [split],
        }
    }
    with pytest.raises(ValidationError):
        await server.get_transaction_details(transaction_id="txn_parent")
