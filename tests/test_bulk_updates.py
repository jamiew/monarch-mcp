"""Tests for bulk transaction update functionality."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestBulkTransactionUpdates:
    """Test bulk transaction update tool."""

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_success(self):
        """Test successful bulk update of multiple transactions."""
        from server import update_transactions_bulk

        mock_update_results = [
            {"updateTransaction": {"errors": [], "transaction": {"id": "txn_123", "amount": 50.0}}},
            {"updateTransaction": {"errors": [], "transaction": {"id": "txn_456", "category_id": "cat_789"}}},
        ]

        mock_client = MagicMock()
        mock_client.update_transaction = AsyncMock(side_effect=mock_update_results)

        updates_json = json.dumps(
            [
                {"transaction_id": "txn_123", "amount": 50.0, "notes": "Updated amount"},
                {"transaction_id": "txn_456", "category_id": "cat_789"},
            ]
        )

        with patch("server.mm_client", mock_client), patch("server.ensure_authenticated", new_callable=AsyncMock):
            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str.model_dump_json())

            assert result["summary"]["total"] == 2
            assert result["summary"]["succeeded"] == 2
            assert result["summary"]["failed"] == 0

            assert len(result["results"]) == 2
            assert all(r["status"] == "success" for r in result["results"])
            assert result["results"][0]["transaction_id"] == "txn_123"
            assert result["results"][1]["transaction_id"] == "txn_456"

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_partial_failure(self):
        """Test bulk update with some failures."""
        from server import update_transactions_bulk

        mock_client = MagicMock()

        async def mock_update(**kwargs):
            if kwargs["transaction_id"] == "txn_123":
                return {"updateTransaction": {"errors": [], "transaction": {"id": "txn_123"}}}
            else:
                raise Exception("Transaction not found")

        mock_client.update_transaction = AsyncMock(side_effect=mock_update)

        updates_json = json.dumps(
            [{"transaction_id": "txn_123", "amount": 50.0}, {"transaction_id": "txn_999", "amount": 100.0}]
        )

        with patch("server.mm_client", mock_client), patch("server.ensure_authenticated", new_callable=AsyncMock):
            result_str = await update_transactions_bulk(updates_json)
            result = json.loads(result_str.model_dump_json())

            assert result["summary"]["total"] == 2
            assert result["summary"]["succeeded"] == 1
            assert result["summary"]["failed"] == 1

            assert result["results"][0]["status"] == "success"
            assert result["results"][1]["status"] == "error"
            assert "not found" in result["results"][1]["error"].lower()

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_invalid_json(self):
        """Test error handling for invalid JSON."""
        from server import update_transactions_bulk

        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="Invalid JSON"):
                await update_transactions_bulk("not valid json {")

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_not_array(self):
        """Test error handling when updates is not an array."""
        from server import update_transactions_bulk

        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="must be a JSON array"):
                await update_transactions_bulk('{"transaction_id": "123"}')

    @pytest.mark.asyncio
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
            {"transaction_id": "txn_bad", "amount": float("inf")},
            {"transaction_id": "txn_bad", "amount": float("nan")},
            {"transaction_id": "txn_bad", "merchant_name": {"name": "Wrong type"}},
            {"transaction_id": "txn_bad", "date": "2024-02-30"},
            {"transaction_id": "txn_bad", "amount": 50, "unknown_field": True},
        ],
    )
    async def test_malformed_item_does_not_mutate_or_abort_batch(self, invalid_update: object) -> None:
        from server import update_transactions_bulk

        api = AsyncMock(return_value={"updateTransaction": {"errors": [], "transaction": {"id": "txn_valid"}}})
        updates = json.dumps([invalid_update, {"transaction_id": "txn_valid", "amount": 25}])
        with (
            patch("server.api_call_with_retry", api),
            patch("server.ensure_authenticated", new_callable=AsyncMock),
        ):
            result = await update_transactions_bulk(updates)

        assert result.summary.total == 2
        assert result.summary.failed == 1
        assert result.summary.succeeded == 1
        assert [item.status for item in result.results] == ["error", "success"]
        assert result.results[1].transaction_id == "txn_valid"
        assert api.await_count == 1
        assert api.await_args.kwargs["transaction_id"] == "txn_valid"

    @pytest.mark.asyncio
    async def test_false_and_empty_values_clear_fields_without_stringifying_null(self) -> None:
        from server import update_transactions_bulk

        transaction = {"hide_from_reports": True, "notes": "Old note", "goal_id": "goal_old", "merchant_name": "Shop"}

        async def apply_update(method_name: str, transaction_id: str, **changes: object) -> dict[str, object]:
            transaction.update(changes)
            return {"updateTransaction": {"errors": [], "transaction": {"id": transaction_id, **transaction}}}

        updates = json.dumps(
            [
                {
                    "transaction_id": "txn_valid",
                    "hide_from_reports": False,
                    "notes": "",
                    "goal_id": "",
                    "merchant_name": None,
                }
            ]
        )
        with (
            patch("server.api_call_with_retry", side_effect=apply_update),
            patch("server.ensure_authenticated", new_callable=AsyncMock),
        ):
            result = await update_transactions_bulk(updates)

        assert result.summary.succeeded == 1
        assert transaction == {"hide_from_reports": False, "notes": "", "goal_id": "", "merchant_name": "Shop"}

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_empty_array(self):
        """Test handling of empty updates array."""
        from server import update_transactions_bulk

        with patch("server.ensure_authenticated", new_callable=AsyncMock):
            result_str = await update_transactions_bulk("[]")
            result = json.loads(result_str.model_dump_json())

            assert result["message"] == "No updates provided"
            assert result["results"] == []

    @pytest.mark.asyncio
    async def test_update_transactions_bulk_parallel_execution(self):
        """Test that bulk updates execute in parallel."""
        import asyncio

        from server import update_transactions_bulk

        mock_client = MagicMock()

        execution_order = []

        async def mock_update(**kwargs):
            txn_id = kwargs["transaction_id"]
            execution_order.append(f"start_{txn_id}")
            await asyncio.sleep(0.01)  # Simulate API call
            execution_order.append(f"end_{txn_id}")
            return {"updateTransaction": {"errors": [], "transaction": {"id": txn_id}}}

        mock_client.update_transaction = AsyncMock(side_effect=mock_update)

        updates_json = json.dumps(
            [
                {"transaction_id": "txn_1", "amount": 10.0},
                {"transaction_id": "txn_2", "amount": 20.0},
                {"transaction_id": "txn_3", "amount": 30.0},
            ]
        )

        with patch("server.mm_client", mock_client), patch("server.ensure_authenticated", new_callable=AsyncMock):
            await update_transactions_bulk(updates_json)

            # Overlapping calls start before the previous call ends.
            starts = [i for i, item in enumerate(execution_order) if item.startswith("start_")]
            assert len(starts) == 3
            assert execution_order.index("start_txn_2") < execution_order.index("end_txn_1")


class TestBulkUpdatePerformance:
    """Test performance characteristics of bulk updates."""

    @pytest.mark.asyncio
    async def test_bulk_update_faster_than_sequential(self):
        """Verify bulk update is faster than sequential updates."""
        import time

        from server import update_transactions_bulk

        mock_client = MagicMock()

        async def mock_update(**kwargs):
            await asyncio.sleep(0.05)  # Simulate 50ms API call
            return {"updateTransaction": {"errors": [], "transaction": {"id": kwargs["transaction_id"]}}}

        mock_client.update_transaction = AsyncMock(side_effect=mock_update)

        updates_json = json.dumps([{"transaction_id": f"txn_{i}", "amount": float(i * 10)} for i in range(5)])

        with patch("server.mm_client", mock_client), patch("server.ensure_authenticated", new_callable=AsyncMock):
            bulk_start = time.time()
            await update_transactions_bulk(updates_json)
            bulk_duration = time.time() - bulk_start

            # Allow scheduling overhead above a single 50 ms call.
            assert bulk_duration < 0.15  # 5 parallel calls @ 50ms each should be ~50-100ms

            # Five sequential calls would take at least 250 ms.
            sequential_estimate = 0.25  # 5 * 50ms
            assert bulk_duration < sequential_estimate / 1.5
