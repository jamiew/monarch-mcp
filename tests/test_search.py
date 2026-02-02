"""Tests for search_transactions tool."""

import json
from unittest.mock import AsyncMock, patch

import pytest

import server


@pytest.mark.asyncio
class TestSearchTransactions:
    """Test suite for search_transactions functionality."""

    async def test_search_transactions_basic(self):
        """Test basic transaction search."""
        # The API will return only matching transactions (server-side filtering)
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Apple Store"},
                "plaidName": "APPLE.COM/BILL",
                "category": {"name": "Shopping"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            },
            {
                "id": "3",
                "date": "2024-01-17",
                "amount": -99.0,
                "merchant": {"name": "Apple Music"},
                "plaidName": "APPLE.COM/MUSIC",
                "notes": "Monthly subscription",
                "category": {"name": "Entertainment"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            },
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await server.search_transactions(query="Apple")

            result_data = json.loads(result)

            # Should receive 2 Apple transactions from the API
            assert result_data["search_metadata"]["result_count"] == 2
            assert result_data["search_metadata"]["query"] == "Apple"
            assert len(result_data["transactions"]) == 2

            # Verify the API was called with search parameter
            mock_client.get_transactions.assert_called_once()
            call_kwargs = mock_client.get_transactions.call_args[1]
            assert call_kwargs["search"] == "Apple"

    async def test_search_transactions_passes_query_to_api(self):
        """Test that search query is passed to the API."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Apple Store"},
                "plaidName": "APPLE.COM/BILL",
                "category": {"name": "Shopping"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            # Test lowercase query
            await server.search_transactions(query="apple")
            call_kwargs = mock_client.get_transactions.call_args[1]
            assert call_kwargs["search"] == "apple"

            # Test uppercase query
            await server.search_transactions(query="APPLE")
            call_kwargs = mock_client.get_transactions.call_args[1]
            assert call_kwargs["search"] == "APPLE"

    async def test_search_transactions_returns_api_results(self):
        """Test that search returns results from the API."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Generic Store"},
                "plaidName": "STORE #123",
                "notes": "Bought an Apple laptop",
                "category": {"name": "Shopping"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await server.search_transactions(query="laptop")
            result_data = json.loads(result)
            assert result_data["search_metadata"]["result_count"] == 1
            assert len(result_data["transactions"]) == 1

    async def test_search_transactions_with_date_filter(self):
        """Test searching with date filters."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Apple Store"},
                "plaidName": "APPLE.COM/BILL",
                "category": {"name": "Shopping"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await server.search_transactions(query="Apple", start_date="2024-01-01", end_date="2024-01-31")

            result_data = json.loads(result)
            assert result_data["search_metadata"]["result_count"] == 1

            # Verify filters were applied to API call
            call_kwargs = mock_client.get_transactions.call_args[1]
            assert "start_date" in call_kwargs
            assert "end_date" in call_kwargs
            assert call_kwargs["search"] == "Apple"

    async def test_search_transactions_no_matches(self):
        """Test search with no matching results (API returns empty list)."""
        mock_transactions = []

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await server.search_transactions(query="Apple")
            result_data = json.loads(result)

            assert result_data["search_metadata"]["result_count"] == 0
            assert len(result_data["transactions"]) == 0

    async def test_search_transactions_empty_query(self):
        """Test that empty query raises error."""
        with patch.object(server, "mm_client") as mock_client:
            with pytest.raises(ValueError, match="Query parameter cannot be empty"):
                await server.search_transactions(query="")

            with pytest.raises(ValueError, match="Query parameter cannot be empty"):
                await server.search_transactions(query="   ")

    async def test_search_transactions_verbose_format(self):
        """Test search with verbose output format."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Apple Store", "id": "merchant123"},
                "plaidName": "APPLE.COM/BILL",
                "category": {"name": "Shopping", "id": "cat123"},
                "account": {"displayName": "Chase Checking", "id": "acc123"},
                "pending": False,
                "needsReview": False,
                "extraField": "extra data",
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            # Test verbose=True (full details)
            result = await server.search_transactions(query="Apple", verbose=True)
            result_data = json.loads(result)
            txn = result_data["transactions"][0]

            # Should have all fields including nested objects
            assert "extraField" in txn
            assert isinstance(txn["merchant"], dict)
            assert "id" in txn["merchant"]

            # Test verbose=False (compact)
            result = await server.search_transactions(query="Apple", verbose=False)
            result_data = json.loads(result)
            txn = result_data["transactions"][0]

            # Should be compact format (merchant name as string)
            assert "extraField" not in txn
            assert isinstance(txn["merchant"], str)

    async def test_search_api_handles_query(self):
        """Test that API search handles the query."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Applebee's Restaurant"},
                "plaidName": "APPLEBEES #456",
                "category": {"name": "Food & Dining"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            # API does the matching
            result = await server.search_transactions(query="Apple")
            result_data = json.loads(result)
            assert result_data["search_metadata"]["result_count"] == 1

            # Verify search parameter was passed
            call_kwargs = mock_client.get_transactions.call_args[1]
            assert call_kwargs["search"] == "Apple"

    async def test_search_transactions_with_account_filter(self):
        """Test search with account filter."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Apple Store"},
                "plaidName": "APPLE.COM/BILL",
                "category": {"name": "Shopping"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            result = await server.search_transactions(query="Apple", account_id="acc123")

            result_data = json.loads(result)
            assert result_data["search_metadata"]["result_count"] == 1

            # Verify the API was called with the account filter as a list
            mock_client.get_transactions.assert_called_once()
            call_kwargs = mock_client.get_transactions.call_args[1]
            assert call_kwargs["account_ids"] == ["acc123"]
            assert call_kwargs["search"] == "Apple"


@pytest.mark.asyncio
class TestSearchTransactionsIntegration:
    """Integration tests for search with other tools."""

    async def test_search_returns_same_format_as_get_transactions(self):
        """Test that search results match get_transactions format."""
        mock_transactions = [
            {
                "id": "1",
                "date": "2024-01-15",
                "amount": -50.0,
                "merchant": {"name": "Apple Store"},
                "plaidName": "APPLE.COM/BILL",
                "category": {"name": "Shopping"},
                "account": {"displayName": "Chase Checking"},
                "pending": False,
                "needsReview": False,
            }
        ]

        with patch.object(server, "mm_client") as mock_client:
            mock_client.get_transactions = AsyncMock(return_value=mock_transactions)

            # Get search results
            search_result = await server.search_transactions(query="Apple", verbose=False)
            search_data = json.loads(search_result)
            search_txns = search_data["transactions"]

            # Get regular transactions with compact format
            get_result = await server.get_transactions(limit=100, verbose=False)
            get_txns = json.loads(get_result)

            # Both should have same compact format
            if search_txns and get_txns:
                search_keys = set(search_txns[0].keys())
                get_keys = set(get_txns[0].keys())
                assert search_keys == get_keys
