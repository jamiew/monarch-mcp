"""Live Monarch Money response-contract tests, disabled by default.

Supply MONARCH_EMAIL, MONARCH_PASSWORD, and optional MONARCH_MFA_SECRET in the
process environment, then explicitly opt in:
    MONARCH_RUN_INTEGRATION=true uv run pytest tests/test_integration.py -v

These tests never load .env files or read/write saved sessions.
"""

import os

import pytest
import pytest_asyncio
from monarchmoney import MonarchMoney

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        os.environ.get("MONARCH_RUN_INTEGRATION") != "true",
        reason="Live Monarch calls require MONARCH_RUN_INTEGRATION=true",
    ),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def authenticated_client() -> MonarchMoney:
    """Log in once per suite to avoid reusing a TOTP code or triggering throttling."""
    email = os.environ.get("MONARCH_EMAIL")
    password = os.environ.get("MONARCH_PASSWORD")
    if not email or not password:
        pytest.skip("Live Monarch calls require MONARCH_EMAIL and MONARCH_PASSWORD")

    mm = MonarchMoney()
    await mm.login(
        email,
        password,
        mfa_secret_key=os.environ.get("MONARCH_MFA_SECRET"),
        use_saved_session=False,
        save_session=False,
    )
    return mm


class TestMonarchAPIConnectivity:
    """Verify response envelopes without requiring a populated account."""

    async def test_get_accounts(self, authenticated_client: MonarchMoney) -> None:
        """Accounts are returned as a list inside an object."""
        accounts = await authenticated_client.get_accounts()
        assert isinstance(accounts, dict)
        assert "accounts" in accounts
        assert isinstance(accounts["accounts"], list)

    async def test_get_transactions(self, authenticated_client: MonarchMoney) -> None:
        """Transactions expose a results list and total count."""
        transactions = await authenticated_client.get_transactions(limit=5)
        assert isinstance(transactions, dict)
        assert "allTransactions" in transactions
        result = transactions["allTransactions"]
        assert isinstance(result, dict)
        assert isinstance(result.get("results"), list)
        assert isinstance(result.get("totalCount"), int)

    async def test_get_budgets(self, authenticated_client: MonarchMoney) -> None:
        """Budgets expose category and category-group monthly amounts."""
        budgets = await authenticated_client.get_budgets()
        assert isinstance(budgets, dict)
        assert "budgetData" in budgets
        budget_data = budgets["budgetData"]
        assert isinstance(budget_data, dict)
        assert isinstance(budget_data.get("monthlyAmountsByCategory"), list)
        assert isinstance(budget_data.get("monthlyAmountsByCategoryGroup"), list)
