"""Integration tests that verify actual Monarch Money API connectivity.

These tests require valid credentials and are skipped by default.

To run integration tests:
    # Set up .env file with credentials, then:
    uv run pytest tests/test_integration.py -v

Or set environment variables directly:
    MONARCH_EMAIL=... MONARCH_PASSWORD=... MONARCH_MFA_SECRET=... uv run pytest tests/test_integration.py -v
"""

import os
import pytest
import pytest_asyncio
from pathlib import Path

# Load .env file if it exists (for local development)
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if key.strip() not in os.environ:  # Don't override existing env vars
                    os.environ[key.strip()] = value

from monarchmoney import MonarchMoney

# Skip all tests in this module if credentials aren't available
CREDENTIALS_AVAILABLE = all([
    os.environ.get("MONARCH_EMAIL"),
    os.environ.get("MONARCH_PASSWORD"),
])

pytestmark = pytest.mark.skipif(
    not CREDENTIALS_AVAILABLE,
    reason="Monarch Money credentials not available (set MONARCH_EMAIL and MONARCH_PASSWORD)"
)


@pytest_asyncio.fixture
async def authenticated_client() -> MonarchMoney:
    """Create and authenticate a MonarchMoney client."""
    mm = MonarchMoney()
    await mm.login(
        os.environ["MONARCH_EMAIL"],
        os.environ["MONARCH_PASSWORD"],
        mfa_secret_key=os.environ.get("MONARCH_MFA_SECRET"),
    )
    return mm


class TestMonarchAPIConnectivity:
    """Integration tests for Monarch Money API connectivity."""

    @pytest.mark.asyncio
    async def test_authentication(self) -> None:
        """Test that we can authenticate with Monarch Money."""
        mm = MonarchMoney()
        await mm.login(
            os.environ["MONARCH_EMAIL"],
            os.environ["MONARCH_PASSWORD"],
            mfa_secret_key=os.environ.get("MONARCH_MFA_SECRET"),
        )
        # If we get here without exception, auth worked
        assert mm is not None

    @pytest.mark.asyncio
    async def test_get_accounts(self, authenticated_client: MonarchMoney) -> None:
        """Test that we can fetch accounts."""
        accounts = await authenticated_client.get_accounts()
        assert isinstance(accounts, dict)
        assert "accounts" in accounts
        assert isinstance(accounts["accounts"], list)

    @pytest.mark.asyncio
    async def test_get_transactions(self, authenticated_client: MonarchMoney) -> None:
        """Test that we can fetch transactions."""
        transactions = await authenticated_client.get_transactions(limit=5)
        assert transactions is not None

    @pytest.mark.asyncio
    async def test_get_budgets(self, authenticated_client: MonarchMoney) -> None:
        """Test that we can fetch budgets."""
        budgets = await authenticated_client.get_budgets()
        assert budgets is not None


class TestHealthCheck:
    """Quick health check to verify API is working."""

    @pytest.mark.asyncio
    async def test_api_health(self, authenticated_client: MonarchMoney) -> None:
        """Comprehensive health check - tests auth, accounts, transactions, budgets."""
        # Test accounts
        accounts = await authenticated_client.get_accounts()
        account_count = len(accounts.get("accounts", []))
        assert account_count > 0, "Expected at least one account"

        # Test transactions
        transactions = await authenticated_client.get_transactions(limit=5)
        assert transactions is not None, "Expected transactions response"

        # Test budgets
        budgets = await authenticated_client.get_budgets()
        assert budgets is not None, "Expected budgets response"

        print(f"\n✅ Health check passed: {account_count} accounts found")
