"""Tests for holdings, history, institutions, budgets, and manual accounts."""

from unittest.mock import AsyncMock

import pytest
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


class TestNewMonarchTools:
    """Test account and budget tools."""

    @pytest.mark.asyncio
    async def test_get_account_holdings(self) -> None:
        """Test get_account_holdings functionality."""
        mock_client = AsyncMock()
        mock_holdings = [
            {"symbol": "AAPL", "shares": 100, "value": 15000},
            {"symbol": "GOOGL", "shares": 50, "value": 12500},
        ]
        mock_client.get_account_holdings.return_value = mock_holdings

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_account_holdings(account_id="acc123")

            assert isinstance(result, server.HoldingsResult)
            assert result.holdings == mock_holdings
            mock_client.get_account_holdings.assert_called_once_with(account_id="acc123")

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
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
        self,
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
            {**snapshots[index], "accountId": "acc_001", "accountName": "Synthetic account"}
            for index in expected_indices
        ]
        history_graphql.return_value = {
            "account": {"displayName": "Synthetic account"},
            "snapshots": snapshots,
        }

        result = await server.get_account_history(account_id="acc_001", start_date=start_date, end_date=end_date)

        assert result.account_id == "acc_001"
        assert result.history == expected_history

    @pytest.mark.asyncio
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
        self, history_graphql: AsyncMock, start_date: str | None, end_date: str | None
    ) -> None:
        with pytest.raises(ValueError):
            await server.get_account_history(account_id="acc_001", start_date=start_date, end_date=end_date)
        history_graphql.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_institutions(self) -> None:
        """Test get_institutions functionality."""
        mock_client = AsyncMock()
        # The real client returns a dict (credentials/accounts/subscription), not a list.
        mock_institutions = {
            "credentials": [{"id": "cred1", "institution": {"name": "Chase Bank"}}],
            "accounts": [{"id": "acc1", "displayName": "Checking"}],
            "subscription": {"hasPremiumEntitlement": True},
        }
        mock_client.get_institutions.return_value = mock_institutions

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.get_institutions()

            assert isinstance(result, server.InstitutionsResult)
            assert result.institutions == mock_institutions
            mock_client.get_institutions.assert_called_once()

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_set_budget_amount(self) -> None:
        """Test set_budget_amount functionality."""
        mock_client = AsyncMock()
        mock_result = {"category_id": "cat123", "amount": 500, "status": "updated"}
        mock_client.set_budget_amount.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.set_budget_amount(category_id="cat123", amount=500.0)

            assert isinstance(result, server.SetBudgetResult)
            assert result.result == mock_result
            assert result.category_id == "cat123"
            assert result.amount == 500.0

            mock_client.set_budget_amount.assert_called_once()
            call_args = mock_client.set_budget_amount.call_args
            assert call_args.kwargs["category_id"] == "cat123"
            assert call_args.kwargs["amount"] == 500.0

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_create_manual_account(self) -> None:
        """Test create_manual_account functionality."""
        mock_client = AsyncMock()
        mock_result = {"id": "acc456", "name": "My Savings", "type": "savings"}
        mock_client.create_manual_account.return_value = mock_result

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            result = await server.create_manual_account(
                account_name="My Savings", account_type="savings", balance=1000.0
            )

            assert isinstance(result, server.CreateAccountResult)
            assert result.account == mock_result

            mock_client.create_manual_account.assert_called_once()
            call_args = mock_client.create_manual_account.call_args
            assert call_args.kwargs["account_name"] == "My Savings"
            assert call_args.kwargs["account_type"] == "savings"
            assert call_args.kwargs["balance"] == 1000.0

        finally:
            server.mm_client = original_client

    @pytest.mark.asyncio
    async def test_error_handling_in_new_tools(self) -> None:
        """Propagate API errors from holdings requests."""
        mock_client = AsyncMock()
        mock_client.get_account_holdings.side_effect = Exception("API Error")

        original_client = server.mm_client
        server.mm_client = mock_client

        try:
            with pytest.raises(Exception, match="API Error"):
                await server.get_account_holdings(account_id="acc123")

            mock_client.get_account_holdings.assert_called_once()

        finally:
            server.mm_client = original_client
