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


# Rule-mutation contract. These tests write to the account, so they need a second opt-in.
# The throwaway rule requires a random merchant name and a $0.01 amount, so it cannot match
# real transactions. It is deleted in a finally block, and every other rule is checked unchanged.
RULE_VOLATILE_FIELDS = {"order", "recentApplicationCount", "lastAppliedAt"}


def _stable_rules(rules: list[dict[str, object]], exclude_id: str | None = None) -> dict[str, dict[str, object]]:
    return {
        str(rule["id"]): {k: v for k, v in rule.items() if k not in RULE_VOLATILE_FIELDS}
        for rule in rules
        if rule["id"] != exclude_id
    }


@pytest.mark.skipif(
    os.environ.get("MONARCH_RUN_RULE_WRITES") != "true",
    reason="Rule writes require MONARCH_RUN_RULE_WRITES=true",
)
async def test_rule_create_update_delete_round_trip(authenticated_client: MonarchMoney) -> None:
    import uuid

    import server

    # conftest resets these globals after each test.
    server.mm_client = authenticated_client
    server.auth_state = server.AuthState.AUTHENTICATED

    marker = f"zz-monarch-mcp-test-{uuid.uuid4().hex}"
    categories = (await authenticated_client.get_transaction_categories())["categories"]
    category_id = categories[0]["id"]
    before = (await authenticated_client.get_transaction_rules())["transactionRules"]

    rule_id: str | None = None
    try:
        created = await server.create_transaction_rule(
            merchant_criteria=[server.RuleTextCriterion(operator="eq", value=marker)],
            amount_criteria=server.RuleAmountCriterion(operator="eq", value=0.01),
            set_category_id=category_id,
            review_status="needs_review",
        )
        assert isinstance(created.rule, dict)
        rule_id = str(created.rule["id"])
        rule = await server._find_rule(rule_id)
        assert rule is not None
        assert rule["merchantNameCriteria"][0]["value"] == marker
        assert rule["setCategoryAction"]["id"] == category_id
        assert rule["reviewStatusAction"] == "needs_review"

        # Changing one field must keep the other actions (Monarch clears omitted ones).
        # Applying to existing transactions is safe here because nothing matches.
        await server.update_transaction_rule(
            rule_id=rule_id,
            amount_criteria=server.RuleAmountCriterion(operator="between", lower=0.01, upper=0.02),
            apply_to_existing_transactions=True,
        )
        rule = await server._find_rule(rule_id)
        assert rule is not None
        assert rule["amountCriteria"]["operator"] == "between"
        assert rule["setCategoryAction"]["id"] == category_id
        assert rule["reviewStatusAction"] == "needs_review"

        # Clearing one action leaves the rest.
        await server.update_transaction_rule(rule_id=rule_id, review_status="")
        rule = await server._find_rule(rule_id)
        assert rule is not None
        assert not rule["reviewStatusAction"]
        assert rule["setCategoryAction"]["id"] == category_id

        deleted = await server.delete_transaction_rule(rule_id=rule_id)
        assert deleted.deleted is True
        rule_id = None
    finally:
        if rule_id is not None:
            await server.delete_transaction_rule(rule_id=rule_id)

    after = (await authenticated_client.get_transaction_rules())["transactionRules"]
    assert _stable_rules(after) == _stable_rules(before)
