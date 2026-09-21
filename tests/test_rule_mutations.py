"""Rule create/update/delete send merged input, verify results, and fail before mutating on bad input."""

from collections.abc import Callable
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue, ValidationError

import server


def existing_rule() -> dict[str, JsonValue]:
    """A rule shaped like upstream get_transaction_rules output."""
    return {
        "id": "rule_001",
        "order": 0,
        "__typename": "TransactionRuleV2",
        "merchantCriteriaUseOriginalStatement": False,
        "merchantCriteria": None,
        "merchantNameCriteria": [{"operator": "contains", "value": "Corner Deli", "__typename": "Criterion"}],
        "originalStatementCriteria": [],
        "amountCriteria": {
            "operator": "between",
            "isExpense": True,
            "value": None,
            "valueRange": {"lower": 5.0, "upper": 25.0, "__typename": "Range"},
        },
        "categoryIds": [],
        "accountIds": ["acc_001"],
        "criteriaOwnerIsJoint": False,
        "criteriaOwnerUserIds": None,
        "setMerchantAction": {"id": "merch_001", "name": "Corner Deli"},
        "setCategoryAction": {"id": "cat_001", "name": "Dining", "icon": "x"},
        "addTagsAction": [{"id": "tag_001", "name": "Lunch", "color": "#fff"}],
        "linkGoalAction": None,
        "needsReviewByUserAction": {"id": "user_001", "name": None},
        "unassignNeedsReviewByUserAction": False,
        "sendNotificationAction": False,
        "setHideFromReportsAction": False,
        "reviewStatusAction": "needs_review",
        "actionSetOwnerIsJoint": False,
        "actionSetOwner": {"id": "user_002", "displayName": "Member"},
        "splitTransactionsAction": None,
        "recentApplicationCount": 3,
    }


class FakeRulesApi:
    """Dispatch api_call_with_retry by method, recording mutation variables."""

    def __init__(self, rules: list[dict[str, JsonValue]], mutation_payload: dict[str, JsonValue]) -> None:
        self.rules = rules
        self.mutation_payload = mutation_payload
        self.mutations: list[tuple[str, dict[str, Any]]] = []
        self.after_mutation: Callable[[], None] | None = None

    async def __call__(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if method_name == "get_transaction_rules":
            return {"transactionRules": deepcopy(self.rules)}
        assert method_name == "gql_call"
        self.mutations.append((kwargs["operation"], kwargs["variables"]))
        if self.after_mutation is not None:
            self.after_mutation()
        return self.mutation_payload


def install(mock_api: AsyncMock, fake: FakeRulesApi) -> FakeRulesApi:
    mock_api.side_effect = fake.__call__
    return fake


async def test_create_sends_criteria_and_actions_and_returns_created_rule(mock_api: AsyncMock) -> None:
    created = {
        "id": "rule_new",
        "setCategoryAction": {"id": "cat_001", "__typename": "Category"},
        "linkGoalAction": None,
    }
    fake = install(
        mock_api,
        FakeRulesApi([created], {"createTransactionRuleV2": {"transactionRule": {"id": "rule_new"}, "errors": None}}),
    )

    result = await server.create_transaction_rule(
        merchant_criteria=[server.RuleTextCriterion(value="Corner Deli")],
        amount_criteria=server.RuleAmountCriterion(operator="gt", value=20),
        set_category_id="cat_001",
        set_merchant_name=" Corner Deli ",
        add_tag_ids=["tag_001"],
        apply_to_existing_transactions=True,
    )

    [(operation, variables)] = fake.mutations
    assert operation == "Common_CreateTransactionRuleMutationV2"
    assert variables["input"] == {
        "applyToExistingTransactions": True,
        "merchantNameCriteria": [{"operator": "contains", "value": "Corner Deli"}],
        "amountCriteria": {"operator": "gt", "isExpense": True, "value": 20.0, "valueRange": None},
        "setCategoryAction": "cat_001",
        "setMerchantAction": "Corner Deli",
        "addTagsAction": ["tag_001"],
    }
    assert result.rule == {"id": "rule_new", "setCategoryAction": {"id": "cat_001"}}
    assert result.applied_to_existing_transactions is True


@pytest.mark.parametrize(
    "arguments",
    [
        {"set_category_id": "cat_001"},
        {"merchant_criteria": [server.RuleTextCriterion(value="Corner Deli")]},
        {"merchant_criteria": [], "set_category_id": "cat_001"},
        {"account_ids": ["acc_001"], "set_category_id": "  ", "hide_from_reports": False},
    ],
)
async def test_create_requires_a_criterion_and_an_action_before_api(
    mock_api: AsyncMock, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="at least one"):
        await server.create_transaction_rule(**arguments)
    mock_api.assert_not_awaited()


@pytest.mark.parametrize(
    "arguments",
    [
        {"operator": "between", "lower": 10},
        {"operator": "between", "lower": 30, "upper": 10},
        {"operator": "between", "lower": 1, "upper": 2, "value": 1},
        {"operator": "gt"},
        {"operator": "lt", "value": 5, "upper": 9},
        {"operator": "eq", "value": -5},
        {"operator": "gte", "value": 5},
    ],
)
def test_amount_criterion_rejects_partial_or_invalid_bounds(arguments: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        server.RuleAmountCriterion.model_validate(arguments)


@pytest.mark.parametrize(
    "errors",
    [
        {"message": "Transaction rule must have one action", "code": None, "fieldErrors": None},
        [{"message": None, "fieldErrors": [{"field": "setCategoryAction", "messages": ["Invalid"]}]}],
        {"message": None, "code": None, "fieldErrors": None},
    ],
)
async def test_create_fails_on_payload_errors(mock_api: AsyncMock, errors: JsonValue) -> None:
    fake = install(mock_api, FakeRulesApi([], {"createTransactionRuleV2": {"transactionRule": None, "errors": errors}}))
    with pytest.raises(ValueError, match="rejected the rule creation"):
        await server.create_transaction_rule(account_ids=["acc_001"], set_category_id="cat_001")
    assert len(fake.mutations) == 1


async def test_create_fails_without_returned_rule(mock_api: AsyncMock) -> None:
    install(mock_api, FakeRulesApi([], {"createTransactionRuleV2": {"transactionRule": None, "errors": None}}))
    with pytest.raises(ValueError, match="no created rule"):
        await server.create_transaction_rule(account_ids=["acc_001"], set_category_id="cat_001")


async def test_update_resends_existing_state_with_changes_merged(mock_api: AsyncMock) -> None:
    fake = install(
        mock_api,
        FakeRulesApi([existing_rule()], {"updateTransactionRuleV2": {"transactionRule": {"id": "rule_001"}}}),
    )

    result = await server.update_transaction_rule(rule_id="rule_001", set_category_id="cat_002", add_tag_ids=[])

    [(operation, variables)] = fake.mutations
    assert operation == "Common_UpdateTransactionRuleMutationV2"
    assert variables["input"] == {
        "id": "rule_001",
        "applyToExistingTransactions": False,
        "merchantCriteriaUseOriginalStatement": False,
        "merchantNameCriteria": [{"operator": "contains", "value": "Corner Deli"}],
        "amountCriteria": {
            "operator": "between",
            "isExpense": True,
            "value": None,
            "valueRange": {"lower": 5.0, "upper": 25.0},
        },
        "accountIds": ["acc_001"],
        # Merchant actions are written by name, not merchant ID.
        "setMerchantAction": "Corner Deli",
        "setCategoryAction": "cat_002",
        "actionSetOwner": "user_002",
        "reviewStatusAction": "needs_review",
        "needsReviewByUserAction": "user_001",
    }
    assert result.applied_to_existing_transactions is False
    assert isinstance(result.rule, dict) and result.rule["id"] == "rule_001"


async def test_update_clears_values_and_replaces_legacy_merchant_criteria(mock_api: AsyncMock) -> None:
    rule = existing_rule()
    rule["merchantCriteria"] = [{"operator": "eq", "value": "Old Deli"}]
    fake = install(mock_api, FakeRulesApi([rule], {"updateTransactionRuleV2": {"errors": None}}))

    await server.update_transaction_rule(
        rule_id="rule_001",
        merchant_criteria=[server.RuleTextCriterion(operator="eq", value="New Deli")],
        clear_amount_criteria=True,
        account_ids=[],
        set_merchant_name="",
        review_status="",
    )

    sent = fake.mutations[0][1]["input"]
    assert sent["merchantCriteria"] == []
    assert sent["merchantNameCriteria"] == [{"operator": "eq", "value": "New Deli"}]
    assert sent["amountCriteria"] is None
    assert sent["accountIds"] == []
    for cleared in ("setMerchantAction", "reviewStatusAction", "needsReviewByUserAction"):
        assert cleared not in sent
    assert sent["setCategoryAction"] == "cat_001"


async def test_update_can_only_apply_rule_to_existing_transactions(mock_api: AsyncMock) -> None:
    fake = install(mock_api, FakeRulesApi([existing_rule()], {"updateTransactionRuleV2": {"errors": None}}))
    result = await server.update_transaction_rule(rule_id="rule_001", apply_to_existing_transactions=True)
    sent = fake.mutations[0][1]["input"]
    assert sent["applyToExistingTransactions"] is True
    assert sent["setCategoryAction"] == "cat_001"
    assert result.applied_to_existing_transactions is True


async def test_update_preserves_split_action_without_type_labels(mock_api: AsyncMock) -> None:
    rule = existing_rule()
    rule["splitTransactionsAction"] = {
        "amountType": "percentage",
        "splitsInfo": [{"categoryId": "cat_001", "amount": 50, "__typename": "SplitInfo"}],
        "__typename": "SplitAction",
    }
    fake = install(mock_api, FakeRulesApi([rule], {"updateTransactionRuleV2": {"errors": None}}))
    await server.update_transaction_rule(rule_id="rule_001", hide_from_reports=True)
    sent = fake.mutations[0][1]["input"]
    assert sent["splitTransactionsAction"] == {
        "amountType": "percentage",
        "splitsInfo": [{"categoryId": "cat_001", "amount": 50}],
    }
    assert sent["setHideFromReportsAction"] is True


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "at least one change"),
        (
            {"amount_criteria": server.RuleAmountCriterion(operator="eq", value=1), "clear_amount_criteria": True},
            "not both",
        ),
    ],
)
async def test_update_rejects_empty_or_conflicting_requests_before_api(
    mock_api: AsyncMock, arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        await server.update_transaction_rule(rule_id="rule_001", **arguments)
    mock_api.assert_not_awaited()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"set_category_id": "", "set_merchant_name": "", "add_tag_ids": [], "review_status": ""}, "one action"),
        ({"merchant_criteria": [], "clear_amount_criteria": True}, "merchant, statement, or amount"),
    ],
)
async def test_update_rejects_rules_left_without_required_parts(
    mock_api: AsyncMock, arguments: dict[str, Any], message: str
) -> None:
    rule = existing_rule()
    rule["actionSetOwner"] = None
    fake = install(mock_api, FakeRulesApi([rule], {"updateTransactionRuleV2": {"errors": None}}))
    with pytest.raises(ValueError, match=message):
        await server.update_transaction_rule(rule_id="rule_001", **arguments)
    assert fake.mutations == []


async def test_update_unknown_rule_fails_without_mutation(mock_api: AsyncMock) -> None:
    fake = install(mock_api, FakeRulesApi([existing_rule()], {}))
    with pytest.raises(ValueError, match="No transaction rule"):
        await server.update_transaction_rule(rule_id="rule_missing", hide_from_reports=True)
    assert fake.mutations == []


async def test_update_fails_on_payload_errors(mock_api: AsyncMock) -> None:
    install(mock_api, FakeRulesApi([existing_rule()], {"updateTransactionRuleV2": {"errors": {"message": "Bad"}}}))
    with pytest.raises(ValueError, match="rejected the rule update: Bad"):
        await server.update_transaction_rule(rule_id="rule_001", hide_from_reports=True)


async def test_delete_trusts_reread_over_deleted_flag(mock_api: AsyncMock) -> None:
    fake = install(
        mock_api, FakeRulesApi([existing_rule()], {"deleteTransactionRule": {"deleted": False, "errors": None}})
    )
    fake.after_mutation = fake.rules.clear

    result = await server.delete_transaction_rule(rule_id="rule_001")

    assert fake.mutations == [("Common_DeleteTransactionRule", {"id": "rule_001"})]
    assert result.deleted is True
    assert result.rule_id == "rule_001"


async def test_delete_fails_when_rule_remains(mock_api: AsyncMock) -> None:
    install(mock_api, FakeRulesApi([existing_rule()], {"deleteTransactionRule": {"deleted": True, "errors": None}}))
    with pytest.raises(ValueError, match="still exists"):
        await server.delete_transaction_rule(rule_id="rule_001")


@pytest.mark.parametrize(
    "response",
    [
        {"deleteTransactionRule": {"deleted": False, "errors": {"message": "Not allowed"}}},
        {"deleteTransactionRule": None},
    ],
)
async def test_delete_fails_on_errors_or_missing_payload(mock_api: AsyncMock, response: dict[str, JsonValue]) -> None:
    install(mock_api, FakeRulesApi([], response))
    with pytest.raises(ValueError, match="rule deletion"):
        await server.delete_transaction_rule(rule_id="rule_001")


async def test_rule_tools_advertise_write_annotations() -> None:
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert tools["create_transaction_rule"].annotations.readOnlyHint is False
    assert tools["update_transaction_rule"].annotations.destructiveHint is False
    assert tools["delete_transaction_rule"].annotations.destructiveHint is True
