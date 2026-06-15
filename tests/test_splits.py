"""Tests for transaction split tools: get_transaction_splits and update_transaction_splits.

Uses the ``mock_api`` fixture (see conftest.py) so tests are offline: it patches
``ensure_authenticated`` and ``api_call_with_retry``.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

import server


def _split_response(splits: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape returned by the library's get_transaction_splits."""
    return {
        "getTransaction": {
            "id": "txn_1",
            "amount": -100.0,
            "splitTransactions": splits,
        }
    }


def _update_response(splits: list[dict[str, Any]], has_splits: bool) -> dict[str, Any]:
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

    @pytest.mark.asyncio
    async def test_missing_transaction_key_is_safe(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"getTransaction": None}
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
