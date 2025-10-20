"""Tests for bulk transaction update functionality."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date


class TestBulkTransactionUpdates:
    """Test bulk transaction update tool."""

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_success(self):
        """Test successful bulk update of multiple transactions."""
        from server import update_transactions_bulk

        # Mock successful updates
        mock_update_results = [
            {"id": "txn_123", "amount": 50.0, "updated": True},
            {"id": "txn_456", "category_id": "cat_789", "updated": True}
        ]

        mock_client = MagicMock()
        mock_client.update_transaction = AsyncMock(side_effect=mock_update_results)

        updates_json = json.dumps([
            {"transaction_id": "txn_123", "amount": 50.0, "notes": "Updated amount"},
            {"transaction_id": "txn_456", "category_id": "cat_789"}
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str)

            # Verify summary
            assert result["summary"]["total"] == 2
            assert result["summary"]["succeeded"] == 2
            assert result["summary"]["failed"] == 0

            # Verify individual results
            assert len(result["results"]) == 2
            assert all(r["status"] == "success" for r in result["results"])
            assert result["results"][0]["transaction_id"] == "txn_123"
            assert result["results"][1]["transaction_id"] == "txn_456"

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_partial_failure(self):
        """Test bulk update with some failures."""
        from server import update_transactions_bulk

        mock_client = MagicMock()

        # First update succeeds, second fails
        async def mock_update(**kwargs):
            if kwargs["transaction_id"] == "txn_123":
                return {"id": "txn_123", "updated": True}
            else:
                raise Exception("Transaction not found")

        mock_client.update_transaction = AsyncMock(side_effect=mock_update)

        updates_json = json.dumps([
            {"transaction_id": "txn_123", "amount": 50.0},
            {"transaction_id": "txn_999", "amount": 100.0}
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str)

            # Verify summary shows mixed results
            assert result["summary"]["total"] == 2
            assert result["summary"]["succeeded"] == 1
            assert result["summary"]["failed"] == 1

            # Check individual results
            assert result["results"][0]["status"] == "success"
            assert result["results"][1]["status"] == "error"
            assert "not found" in result["results"][1]["error"].lower()

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_invalid_json(self):
        """Test error handling for invalid JSON."""
        from server import update_transactions_bulk

        with patch('server.ensure_authenticated', new_callable=AsyncMock):
            with pytest.raises(ValueError, match="Invalid JSON"):
                await update_transactions_bulk("not valid json {")

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_not_array(self):
        """Test error handling when updates is not an array."""
        from server import update_transactions_bulk

        with patch('server.ensure_authenticated', new_callable=AsyncMock):
            with pytest.raises(ValueError, match="must be a JSON array"):
                await update_transactions_bulk('{"transaction_id": "123"}')

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_missing_transaction_id(self):
        """Test error handling when transaction_id is missing."""
        from server import update_transactions_bulk

        mock_client = MagicMock()

        updates_json = json.dumps([
            {"amount": 50.0, "notes": "Missing ID"}
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str)

            # Should have error result
            assert result["summary"]["failed"] == 1
            assert result["results"][0]["status"] == "error"
            assert "transaction_id is required" in result["results"][0]["error"]

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_empty_array(self):
        """Test handling of empty updates array."""
        from server import update_transactions_bulk

        with patch('server.ensure_authenticated', new_callable=AsyncMock):
            result_str = await update_transactions_bulk("[]")
            result = json.loads(result_str)

            assert result["message"] == "No updates provided"
            assert result["results"] == []

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_date_parsing(self):
        """Test that dates are properly parsed in bulk updates."""
        from server import update_transactions_bulk

        mock_client = MagicMock()
        mock_client.update_transaction = AsyncMock(return_value={"id": "txn_123", "updated": True})

        updates_json = json.dumps([
            {"transaction_id": "txn_123", "date": "2024-01-15"}
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str)

            # Verify date was passed correctly
            assert result["results"][0]["status"] == "success"

            # Check that update_transaction was called with a date object
            call_kwargs = mock_client.update_transaction.call_args[1]
            assert "date" in call_kwargs
            assert isinstance(call_kwargs["date"], date)
            assert call_kwargs["date"].year == 2024
            assert call_kwargs["date"].month == 1
            assert call_kwargs["date"].day == 15

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_all_fields(self):
        """Test bulk update with all possible fields."""
        from server import update_transactions_bulk

        mock_client = MagicMock()
        mock_client.update_transaction = AsyncMock(return_value={"id": "txn_123", "updated": True})

        updates_json = json.dumps([
            {
                "transaction_id": "txn_123",
                "amount": 75.50,
                "merchant_name": "Updated merchant",
                "category_id": "cat_456",
                "date": "2024-02-20",
                "notes": "Updated notes"
            }
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str)

            assert result["results"][0]["status"] == "success"

            # Verify all fields were passed
            call_kwargs = mock_client.update_transaction.call_args[1]
            assert call_kwargs["transaction_id"] == "txn_123"
            assert call_kwargs["amount"] == 75.50
            assert call_kwargs["merchant_name"] == "Updated merchant"
            assert call_kwargs["category_id"] == "cat_456"
            assert call_kwargs["notes"] == "Updated notes"
            assert isinstance(call_kwargs["date"], date)

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_parallel_execution(self):
        """Test that bulk updates execute in parallel."""
        from server import update_transactions_bulk
        import asyncio

        mock_client = MagicMock()

        # Track execution order
        execution_order = []

        async def mock_update(**kwargs):
            txn_id = kwargs["transaction_id"]
            execution_order.append(f"start_{txn_id}")
            await asyncio.sleep(0.01)  # Simulate API call
            execution_order.append(f"end_{txn_id}")
            return {"id": txn_id, "updated": True}

        mock_client.update_transaction = AsyncMock(side_effect=mock_update)

        updates_json = json.dumps([
            {"transaction_id": "txn_1", "amount": 10.0},
            {"transaction_id": "txn_2", "amount": 20.0},
            {"transaction_id": "txn_3", "amount": 30.0}
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            await update_transactions_bulk(updates_json)

            # If executed in parallel, we should see interleaved starts/ends
            # rather than sequential start1->end1->start2->end2->start3->end3
            # Check that we have at least one "start" after another "start"
            # (meaning they were running concurrently)
            starts = [i for i, item in enumerate(execution_order) if item.startswith("start_")]
            assert len(starts) == 3
            # If parallel, all starts should happen before all ends complete
            # (at minimum, start_2 should happen before end_1)
            assert execution_order.index("start_txn_2") < execution_order.index("end_txn_1")


class TestBulkUpdatePerformance:
    """Test performance characteristics of bulk updates."""

    @pytest.mark.asyncio
    async def test_bulk_update_faster_than_sequential(self):
        """Verify bulk update is faster than sequential updates."""
        from server import update_transactions_bulk, update_transaction
        import time

        mock_client = MagicMock()

        async def mock_update(**kwargs):
            await asyncio.sleep(0.05)  # Simulate 50ms API call
            return {"id": kwargs["transaction_id"], "updated": True}

        mock_client.update_transaction = AsyncMock(side_effect=mock_update)

        # Test bulk update (parallel)
        updates_json = json.dumps([
            {"transaction_id": f"txn_{i}", "amount": float(i * 10)}
            for i in range(5)
        ])

        with patch('server.mm_client', mock_client), \
             patch('server.ensure_authenticated', new_callable=AsyncMock):

            bulk_start = time.time()
            await update_transactions_bulk(updates_json)
            bulk_duration = time.time() - bulk_start

            # Bulk should take roughly the time of one API call (parallel execution)
            # Allow some overhead, but should be < 2x a single call
            assert bulk_duration < 0.15  # 5 parallel calls @ 50ms each should be ~50-100ms

            # Sequential would take 5 * 50ms = 250ms minimum
            # So bulk should be significantly faster (at least 1.5x)
            sequential_estimate = 0.25  # 5 * 50ms
            assert bulk_duration < sequential_estimate / 1.5
