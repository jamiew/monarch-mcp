"""Tests for schema fixes and new parameters."""

import json
from unittest.mock import AsyncMock, patch

import pytest

# Import the tools
from server import (
    create_transaction,
    get_transactions,
    search_transactions,
    update_transaction,
    update_transactions_bulk,
)


class TestUpdateTransactionSchema:
    """Test update_transaction with new merchant_name and other fields."""

    @pytest.mark.asyncio
    async def test_merchant_name_parameter(self):
        """Verify merchant_name parameter works (not 'description')."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {
                    "id": "txn_123",
                    "merchant": {"name": "New Merchant Name"},
                    "amount": -50.00,
                }

                result = await update_transaction(transaction_id="txn_123", merchant_name="New Merchant Name")

                # Verify API was called with merchant_name (not description)
                mock_api.assert_called_once()
                call_kwargs = mock_api.call_args[1]
                assert "merchant_name" in call_kwargs
                assert call_kwargs["merchant_name"] == "New Merchant Name"
                assert "description" not in call_kwargs

    @pytest.mark.asyncio
    async def test_goal_id_parameter(self):
        """Test goal_id parameter for savings goals."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_123", "goal": {"id": "goal_456"}}

                result = await update_transaction(transaction_id="txn_123", goal_id="goal_456")

                call_kwargs = mock_api.call_args[1]
                assert "goal_id" in call_kwargs
                assert call_kwargs["goal_id"] == "goal_456"

    @pytest.mark.asyncio
    async def test_hide_from_reports_parameter(self):
        """Test hide_from_reports boolean parameter."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_123", "hideFromReports": True}

                result = await update_transaction(transaction_id="txn_123", hide_from_reports=True)

                call_kwargs = mock_api.call_args[1]
                assert "hide_from_reports" in call_kwargs
                assert call_kwargs["hide_from_reports"] is True

    @pytest.mark.asyncio
    async def test_needs_review_parameter(self):
        """Test needs_review boolean parameter."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_123", "needsReview": False}

                result = await update_transaction(transaction_id="txn_123", needs_review=False)

                call_kwargs = mock_api.call_args[1]
                assert "needs_review" in call_kwargs
                assert call_kwargs["needs_review"] is False

    @pytest.mark.asyncio
    async def test_all_new_parameters_together(self):
        """Test all new parameters can be used together."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_123"}

                result = await update_transaction(
                    transaction_id="txn_123",
                    merchant_name="Starbucks",
                    goal_id="goal_savings",
                    hide_from_reports=True,
                    needs_review=False,
                    notes="Updated via API",
                )

                call_kwargs = mock_api.call_args[1]
                assert call_kwargs["merchant_name"] == "Starbucks"
                assert call_kwargs["goal_id"] == "goal_savings"
                assert call_kwargs["hide_from_reports"] is True
                assert call_kwargs["needs_review"] is False
                assert call_kwargs["notes"] == "Updated via API"


class TestCreateTransactionSchema:
    """Test create_transaction with merchant_name instead of description."""

    @pytest.mark.asyncio
    async def test_merchant_name_required(self):
        """Verify merchant_name is used (not description)."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_new", "merchant": {"name": "Test Merchant"}}

                result = await create_transaction(
                    amount=-25.00,
                    merchant_name="Test Merchant",
                    account_id="acc_123",
                    date="2024-01-15",
                    category_id="cat_456",
                )

                # Verify API was called with merchant_name
                call_kwargs = mock_api.call_args[1]
                assert "merchant_name" in call_kwargs
                assert call_kwargs["merchant_name"] == "Test Merchant"
                assert "description" not in call_kwargs

    @pytest.mark.asyncio
    async def test_update_balance_parameter(self):
        """Test new update_balance parameter."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_new"}

                result = await create_transaction(
                    amount=-100.00,
                    merchant_name="Manual Transaction",
                    account_id="acc_manual",
                    date="2024-01-15",
                    category_id="cat_expense",
                    update_balance=True,
                )

                call_kwargs = mock_api.call_args[1]
                assert "update_balance" in call_kwargs
                assert call_kwargs["update_balance"] is True

    @pytest.mark.asyncio
    async def test_empty_merchant_name_fails(self):
        """Verify empty merchant_name is rejected."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="merchant_name cannot be empty"):
                await create_transaction(
                    amount=-50.00, merchant_name="", account_id="acc_123", date="2024-01-15", category_id="cat_456"
                )


class TestGetTransactionsFilters:
    """Test new filter parameters for get_transactions."""

    @pytest.mark.asyncio
    async def test_has_attachments_filter(self):
        """Test filtering by attachment presence."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": [{"id": "txn_1", "hasAttachments": True}]}}

                result = await get_transactions(has_attachments=True)

                call_kwargs = mock_api.call_args[1]
                assert "has_attachments" in call_kwargs
                assert call_kwargs["has_attachments"] is True

    @pytest.mark.asyncio
    async def test_has_notes_filter(self):
        """Test filtering by notes presence."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": [{"id": "txn_1", "notes": "Has notes"}]}}

                result = await get_transactions(has_notes=True)

                call_kwargs = mock_api.call_args[1]
                assert "has_notes" in call_kwargs
                assert call_kwargs["has_notes"] is True

    @pytest.mark.asyncio
    async def test_is_split_filter(self):
        """Test filtering for split transactions."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": [{"id": "txn_1", "isSplit": True}]}}

                result = await get_transactions(is_split=True)

                call_kwargs = mock_api.call_args[1]
                assert "is_split" in call_kwargs
                assert call_kwargs["is_split"] is True

    @pytest.mark.asyncio
    async def test_is_recurring_filter(self):
        """Test filtering for recurring transactions."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": [{"id": "txn_1", "isRecurring": True}]}}

                result = await get_transactions(is_recurring=True)

                call_kwargs = mock_api.call_args[1]
                assert "is_recurring" in call_kwargs
                assert call_kwargs["is_recurring"] is True

    @pytest.mark.asyncio
    async def test_tag_ids_filter(self):
        """Test filtering by tag IDs with comma-separated string."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": [{"id": "txn_1"}]}}

                result = await get_transactions(tag_ids="tag_1,tag_2,tag_3")

                call_kwargs = mock_api.call_args[1]
                assert "tag_ids" in call_kwargs
                assert call_kwargs["tag_ids"] == ["tag_1", "tag_2", "tag_3"]

    @pytest.mark.asyncio
    async def test_multiple_filters_combined(self):
        """Test combining multiple new filters."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": []}}

                result = await get_transactions(
                    has_attachments=True, has_notes=False, is_split=True, hidden_from_reports=False
                )

                call_kwargs = mock_api.call_args[1]
                assert call_kwargs["has_attachments"] is True
                assert call_kwargs["has_notes"] is False
                assert call_kwargs["is_split"] is True
                assert call_kwargs["hidden_from_reports"] is False


class TestSearchTransactionsFilters:
    """Test new filter parameters for search_transactions (same as get_transactions)."""

    @pytest.mark.asyncio
    async def test_search_with_attachments_filter(self):
        """Test search with has_attachments filter."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"allTransactions": {"results": [{"id": "txn_1"}]}}

                result = await search_transactions(query="Starbucks", has_attachments=True)

                call_kwargs = mock_api.call_args[1]
                assert "search" in call_kwargs
                assert call_kwargs["search"] == "Starbucks"
                assert "has_attachments" in call_kwargs
                assert call_kwargs["has_attachments"] is True


class TestBulkUpdateTransactions:
    """Test bulk updates with new fields."""

    @pytest.mark.asyncio
    async def test_bulk_update_with_merchant_name(self):
        """Test bulk update with merchant_name field."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_123"}

                updates_json = json.dumps(
                    [
                        {"transaction_id": "txn_1", "merchant_name": "Starbucks"},
                        {"transaction_id": "txn_2", "merchant_name": "Whole Foods"},
                    ]
                )

                result = await update_transactions_bulk(updates=updates_json)

                # Should have been called twice (once per transaction)
                assert mock_api.call_count == 2

                # Check both calls used merchant_name
                for call in mock_api.call_args_list:
                    assert "merchant_name" in call[1]

    @pytest.mark.asyncio
    async def test_bulk_update_with_all_new_fields(self):
        """Test bulk update with all new fields."""
        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with patch("server.api_call_with_retry", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"id": "txn_123"}

                updates_json = json.dumps(
                    [
                        {
                            "transaction_id": "txn_1",
                            "merchant_name": "Updated Merchant",
                            "goal_id": "goal_123",
                            "hide_from_reports": True,
                            "needs_review": False,
                        }
                    ]
                )

                result = await update_transactions_bulk(updates=updates_json)

                # Verify all fields were passed to API
                call_kwargs = mock_api.call_args[1]
                assert call_kwargs["merchant_name"] == "Updated Merchant"
                assert call_kwargs["goal_id"] == "goal_123"
                assert call_kwargs["hide_from_reports"] is True
                assert call_kwargs["needs_review"] is False
