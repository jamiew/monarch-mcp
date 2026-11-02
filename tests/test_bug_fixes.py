"""Regression tests for authentication, date, analysis, and write-validation fixes."""

import asyncio
import json
import pickle
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from dateutil.relativedelta import relativedelta
from gql.transport.exceptions import TransportServerError
from mcp.server.mcpserver.exceptions import ToolError
from monarchmoney import MonarchMoney, RequestFailedException, RequireMFAException

import server


def dispatch(by_method: dict[str, Any]) -> Any:
    def _side_effect(method_name: str, *args: Any, **kwargs: Any) -> Any:
        result = by_method[method_name]
        if isinstance(result, BaseException):
            raise result
        return result

    return _side_effect


@pytest.fixture
def fresh_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Start unauthenticated with synthetic credentials and a private temp session dir."""
    monkeypatch.setattr(server, "mm_client", None)
    monkeypatch.setattr(server, "auth_state", server.AuthState.NOT_INITIALIZED)
    monkeypatch.setattr(server, "auth_lock", None)
    monkeypatch.setattr(server, "session_dir", tmp_path)
    monkeypatch.setattr(server, "session_file", tmp_path / "session.pickle")
    monkeypatch.setenv("MONARCH_EMAIL", "example@example.invalid")
    monkeypatch.setenv("MONARCH_PASSWORD", "synthetic-password")
    monkeypatch.delenv("MONARCH_MFA_SECRET", raising=False)
    monkeypatch.delenv("MONARCH_FORCE_LOGIN", raising=False)
    return tmp_path


class TestAuthentication:
    async def test_cancelled_login_does_not_block_later_authentication(
        self, fresh_auth: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def hanging_init() -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "initialize_client", hanging_init)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server.ensure_authenticated(), timeout=0.01)

        async def working_init() -> None:
            server.mm_client = AsyncMock()
            server.auth_state = server.AuthState.AUTHENTICATED

        monkeypatch.setattr(server, "initialize_client", working_init)
        await server.ensure_authenticated()

        assert server.auth_state is server.AuthState.AUTHENTICATED

    async def test_concurrent_calls_with_expired_session_log_in_once(
        self, fresh_auth: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        all_in_flight = asyncio.Event()
        relogged = asyncio.Event()
        logins = 0

        class ExpiredClient:
            calls = 0

            async def get_accounts(self) -> dict[str, Any]:
                self.calls += 1
                order = self.calls
                if order == 4:
                    all_in_flight.set()
                await all_in_flight.wait()
                # The first call fails at once; the rest fail only after the re-login.
                if order > 1:
                    await relogged.wait()
                raise TransportServerError("401, message='Unauthorized'", 401)

        class FreshClient:
            async def login(self, *args: object, **kwargs: object) -> None:
                nonlocal logins
                logins += 1
                published_before_login.append(server.mm_client is self)
                relogged.set()

            def save_session(self, filename: str) -> None:
                Path(filename).write_bytes(b"synthetic")

            async def get_accounts(self) -> dict[str, Any]:
                return {"accounts": [{"id": "acc_001"}]}

        published_before_login: list[bool] = []
        monkeypatch.setattr(server, "MonarchMoney", lambda **_: FreshClient())
        monkeypatch.setattr(server, "mm_client", ExpiredClient())
        monkeypatch.setattr(server, "auth_state", server.AuthState.AUTHENTICATED)

        results = await asyncio.gather(*(server.api_call_with_retry("get_accounts") for _ in range(4)))

        assert logins == 1
        assert published_before_login == [False]
        assert results == [{"accounts": [{"id": "acc_001"}]}] * 4
        assert isinstance(server.mm_client, FreshClient)

    @pytest.mark.parametrize(
        "error",
        [
            Exception("Transaction 1984015 not found"),
            ValueError("Invalid amount 403.20 for split"),
            TransportServerError("upstream unauthorized proxy message", 500),
            aiohttp.ClientResponseError(MagicMock(), (), status=404, message="Not Found"),
            asyncio.TimeoutError(),
        ],
    )
    def test_non_auth_errors_do_not_trigger_relogin(self, error: Exception) -> None:
        assert not server.is_auth_error(error)

    @pytest.mark.parametrize(
        "error",
        [
            TransportServerError("401, message='Unauthorized'", 401),
            TransportServerError("Forbidden", 403),
            aiohttp.ClientResponseError(MagicMock(), (), status=401, message="Unauthorized"),
            RequestFailedException("HTTP Code 401: Unauthorized"),
        ],
    )
    def test_auth_errors_are_recognized(self, error: Exception) -> None:
        assert server.is_auth_error(error)

    def test_status_is_found_through_wrapped_errors(self) -> None:
        try:
            try:
                raise TransportServerError("status error", 401)
            except TransportServerError as inner:
                raise RuntimeError("request failed") from inner
        except RuntimeError as outer:
            assert server.is_auth_error(outer)

    async def test_rejected_mfa_code_allows_retry_after_cooldown(
        self, fresh_auth: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MONARCH_MFA_SECRET", "SYNTHETICSECRET")
        login = AsyncMock(side_effect=RequireMFAException("Multi-Factor Auth Required"))
        client = MagicMock()
        client.login = login
        monkeypatch.setattr(server, "MonarchMoney", lambda **_: client)

        with pytest.raises(ValueError):
            await server.ensure_authenticated()

        with pytest.raises(ValueError):
            await server.ensure_authenticated()
        assert login.await_count == 1

        assert server.auth_failed_at is not None
        server.auth_failed_at -= server.AUTH_RETRY_COOLDOWN_SECONDS + 1
        with pytest.raises(ValueError):
            await server.ensure_authenticated()
        assert login.await_count == 2

    async def test_clients_use_a_longer_request_timeout(self, fresh_auth: Path) -> None:
        with (fresh_auth / "session.pickle").open("wb") as fh:
            pickle.dump({"token": "synthetic-session-token", "auth_mode": "token"}, fh)

        await server.initialize_client()

        assert isinstance(server.mm_client, MonarchMoney)
        assert server.mm_client.timeout >= 30
        # The write wrapper must not cancel a request the client is still allowed to wait for.
        assert server.mm_client.timeout < server.WRITE_TIMEOUT_SECONDS


class TestDateRanges:
    @pytest.mark.parametrize(
        ("value", "start", "end"),
        [
            ("2024", "2024-01-01", "2024-12-31"),
            ("2024-02", "2024-02-01", "2024-02-29"),
            ("January 2024", "2024-01-01", "2024-01-31"),
            ("2024-01-15", "2024-01-15", "2024-01-15"),
        ],
    )
    def test_partial_dates_cover_their_whole_period(self, value: str, start: str, end: str) -> None:
        assert server.build_date_filter(value, value) == {"start_date": start, "end_date": end}

    def test_named_period_as_both_bounds_covers_the_period(self) -> None:
        first = date.today().replace(day=1) - relativedelta(months=1)
        last = date.today().replace(day=1) - relativedelta(days=1)

        assert server.build_date_filter("last month", "last month") == {
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
        }

    def test_named_period_as_only_end_covers_the_period(self) -> None:
        year = date.today().year - 1
        assert server.build_date_filter(None, "last year") == {
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
        }

    async def test_spending_summary_reports_resolved_period(self, mock_api: AsyncMock) -> None:
        mock_api.side_effect = dispatch({"get_transaction_categories": {"categories": []}, "get_transactions": []})

        result = await server.get_spending_summary(start_date="2024-02", end_date="2024-02")

        assert result.period.start == "2024-02-01"
        assert result.period.end == "2024-02-29"


CATEGORIES = {
    "categories": [
        {"id": "cat_food", "name": "Groceries", "group": {"id": "grp_1", "type": "expense"}},
        {"id": "cat_pay", "name": "Credit Card Payment", "group": {"id": "grp_2", "type": "transfer"}},
        {"id": "cat_pay_in", "name": "Paychecks", "group": {"id": "grp_3", "type": "income"}},
    ]
}


def _txn(txn_date: str, amount: float, category_id: str, name: str = "Groceries") -> dict[str, Any]:
    return {
        "id": f"txn_{category_id}_{txn_date}_{amount}",
        "date": txn_date,
        "amount": amount,
        "category": {"id": category_id, "name": name},
        "account": {"id": "acc_001", "displayName": "Checking"},
    }


class TestTransfers:
    TRANSACTIONS = [
        _txn("2024-01-05", -40.0, "cat_food"),
        # A card payment appears as an outflow from checking and an inflow to the card.
        _txn("2024-01-10", -500.0, "cat_pay", "Credit Card Payment"),
        _txn("2024-01-10", 500.0, "cat_pay", "Credit Card Payment"),
        _txn("2024-01-01", 2000.0, "cat_pay_in", "Paychecks"),
    ]

    async def test_spending_summary_excludes_transfers(self, mock_api: AsyncMock) -> None:
        mock_api.side_effect = dispatch(
            {"get_transaction_categories": CATEGORIES, "get_transactions": self.TRANSACTIONS}
        )

        result = await server.get_spending_summary("2024-01-01", "2024-01-31")

        assert (result.totals.income, result.totals.expenses) == (2000.0, 40.0)
        assert "Credit Card Payment" not in result.groups
        assert result.transfers_excluded == 2

    async def test_overview_summary_excludes_transfers(self, mock_api: AsyncMock) -> None:
        mock_api.side_effect = dispatch(
            {
                "get_accounts": {"accounts": []},
                "get_budgets": {"budgetData": {"totalsByMonth": []}},
                "get_cashflow": {"summary": [{"summary": {"sumIncome": 2000}}]},
                "get_transactions": {"allTransactions": {"totalCount": 4, "results": self.TRANSACTIONS}},
                "get_transaction_categories": CATEGORIES,
            }
        )

        result = await server.get_complete_financial_overview("this month")

        summary = result.transaction_summary
        assert isinstance(summary, dict)
        assert (summary["total_income"], summary["total_expenses"]) == (2000.0, 40.0)
        assert summary["transfers_excluded"] == 2

    async def test_spending_patterns_exclude_transfers(self, mock_api: AsyncMock) -> None:
        mock_api.side_effect = dispatch(
            {
                "get_transactions": {"allTransactions": {"totalCount": 4, "results": self.TRANSACTIONS}},
                "get_budgets": {"budgetData": {"totalsByMonth": []}},
                "get_transaction_categories": CATEGORIES,
            }
        )

        result = await server.analyze_spending_patterns(lookback_months=3, include_forecasting=False)

        assert result.monthly_trends == {
            "2024-01": {"income": 2000.0, "expenses": 40.0, "net": 1960.0, "transaction_count": 2}
        }
        assert isinstance(result.metadata, dict)
        assert result.metadata["transfers_excluded"] == 2


class TestSpendingPatterns:
    async def test_forecast_uses_latest_complete_months(self, mock_api: AsyncMock) -> None:
        this_month = date.today().replace(day=1)
        month = [(this_month - relativedelta(months=n)).isoformat() for n in range(5)]
        # Monarch returns transactions newest first.
        transactions = [
            _txn(month[0], -1000.0, "cat_food"),  # current, incomplete month
            _txn(month[1], -100.0, "cat_food"),
            _txn(month[2], -200.0, "cat_food"),
            _txn(month[3], -300.0, "cat_food"),
            _txn(month[4], -4000.0, "cat_food"),  # older than the latest three
        ]
        mock_api.side_effect = dispatch(
            {
                "get_transactions": {"allTransactions": {"totalCount": 5, "results": transactions}},
                "get_budgets": {"budgetData": {"totalsByMonth": []}},
                "get_transaction_categories": CATEGORIES,
            }
        )

        result = await server.analyze_spending_patterns(lookback_months=6, include_forecasting=True)

        assert isinstance(result.forecast, dict)
        assert result.forecast["predicted_expenses"] == 200.0

    async def test_forecast_skips_partial_first_month(self, mock_api: AsyncMock) -> None:
        this_month = date.today().replace(day=1)
        month = [(this_month - relativedelta(months=n)).isoformat() for n in range(4)]
        transactions = [
            _txn(month[1], -100.0, "cat_food"),
            _txn(month[2], -200.0, "cat_food"),
            _txn(month[3], -30.0, "cat_food"),  # first month of a 3-month window
        ]
        mock_api.side_effect = dispatch(
            {
                "get_transactions": {"allTransactions": {"totalCount": 3, "results": transactions}},
                "get_budgets": {"budgetData": {"totalsByMonth": []}},
                "get_transaction_categories": CATEGORIES,
            }
        )

        result = await server.analyze_spending_patterns(lookback_months=3, include_forecasting=True)

        assert isinstance(result.forecast, dict)
        # The window starts mid-month unless today is the 1st.
        expected = (100.0 + 200.0 + 30.0) / 3 if date.today().day == 1 else 150.0
        assert result.forecast["predicted_expenses"] == pytest.approx(expected)

    async def test_forecast_skips_oldest_month_of_truncated_page(self, mock_api: AsyncMock) -> None:
        this_month = date.today().replace(day=1)
        month = [(this_month - relativedelta(months=n)).isoformat() for n in range(4)]
        transactions = [
            _txn(month[1], -100.0, "cat_food"),
            _txn(month[2], -200.0, "cat_food"),
            _txn(month[3], -30.0, "cat_food"),  # the page ran out partway through this month
        ]
        mock_api.side_effect = dispatch(
            {
                "get_transactions": {"allTransactions": {"totalCount": 5000, "results": transactions}},
                "get_budgets": {"budgetData": {"totalsByMonth": []}},
                "get_transaction_categories": CATEGORIES,
            }
        )

        result = await server.analyze_spending_patterns(lookback_months=6, include_forecasting=True)

        assert isinstance(result.forecast, dict)
        assert result.forecast["predicted_expenses"] == pytest.approx(150.0)

    async def test_category_average_counts_only_expenses(self, mock_api: AsyncMock) -> None:
        transactions = [
            _txn("2024-01-05", -100.0, "cat_food"),
            _txn("2024-01-06", -50.0, "cat_food"),
            _txn("2024-01-07", 30.0, "cat_food"),  # refund
        ]
        mock_api.side_effect = dispatch(
            {
                "get_transactions": {"allTransactions": {"totalCount": 3, "results": transactions}},
                "get_budgets": {"budgetData": {"totalsByMonth": []}},
                "get_transaction_categories": CATEGORIES,
            }
        )

        result = await server.analyze_spending_patterns(lookback_months=3, include_forecasting=False)

        assert isinstance(result.category_analysis, dict)
        assert result.category_analysis["Groceries"]["avg_amount"] == 75.0


class TestTransactionWrites:
    async def test_update_rejects_zero_amount(self, mock_api: AsyncMock) -> None:
        with pytest.raises(ToolError):
            await server.update_transaction(transaction_id="txn_123", amount=0)

        mock_api.assert_not_awaited()

    async def test_bulk_update_rejects_zero_amount_per_item(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"updateTransaction": {"errors": None, "transaction": {"id": "txn_456"}}}
        updates = [{"transaction_id": "txn_123", "amount": 0.0}, {"transaction_id": "txn_456", "amount": 5.0}]

        result = await server.update_transactions_bulk(json.dumps(updates))

        assert [item.status for item in result.results] == ["error", "success"]
        assert mock_api.await_count == 1
        assert mock_api.await_args is not None
        assert mock_api.await_args.kwargs["transaction_id"] == "txn_456"

    async def test_split_update_without_transaction_fails(self, mock_api: AsyncMock) -> None:
        mock_api.return_value = {"updateTransactionSplit": {"errors": None, "transaction": None}}

        with pytest.raises(ToolError):
            await server.update_transaction_splits(
                transaction_id="txn_123", splits=[server.TransactionSplit(amount=-10.0, category_id="cat_food")]
            )
