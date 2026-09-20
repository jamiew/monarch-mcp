"""Bounded reads preserve rule semantics and upstream priority without mutation."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

import server


async def test_rules_pages_preserve_priority_and_compact_without_mutation(mock_api: AsyncMock) -> None:
    rules: list[dict[str, JsonValue]] = [
        {
            "id": "rule_z",
            "__typename": "TransactionRule",
            "criteria": [
                {
                    "__typename": "RuleCriterion",
                    "field": "amount",
                    "operator": "equals",
                    "value": 0,
                    "unused": None,
                },
                {"field": "merchant", "value": "Corner Deli", "options": []},
            ],
            "actions": {
                "__typename": "RuleActions",
                "hideFromReports": False,
                "notes": "",
                "category": {"id": "cat_001", "__typename": "Category", "unused": {}},
                "tags": [],
                "unused": None,
            },
            "futureField": {"enabled": False, "threshold": 0, "label": "", "unused": {}},
            "emptyAfterCompaction": {"unused": None, "__typename": "Unused"},
        },
        {
            "id": "rule_a",
            "criteria": [{"field": "account", "value": "acc_001"}],
            "actions": [{"field": "reviewed", "value": False, "unused": None}],
            "futureValues": [None, [], {}, False, 0, ""],
        },
        {
            "id": "rule_m",
            "criteria": [{"field": "notes", "value": "", "unused": []}],
            "actions": [{"field": "merchant", "value": "Synthetic Store", "__typename": "RuleAction"}],
        },
    ]
    response = {"transactionRules": rules}
    original = deepcopy(response)
    mock_api.return_value = response

    first = await server.get_transaction_rules(limit=2)
    assert first.rules == [
        {
            "id": "rule_z",
            "criteria": [
                {"field": "amount", "operator": "equals", "value": 0},
                {"field": "merchant", "value": "Corner Deli"},
            ],
            "actions": {"hideFromReports": False, "notes": "", "category": {"id": "cat_001"}},
            "futureField": {"enabled": False, "threshold": 0, "label": ""},
        },
        {
            "id": "rule_a",
            "criteria": [{"field": "account", "value": "acc_001"}],
            "actions": [{"field": "reviewed", "value": False}],
            "futureValues": [None, [], {}, False, 0, ""],
        },
    ]
    assert (first.count, first.total_count, first.offset, first.next_offset) == (2, 3, 0, 2)
    assert first.verbose is False

    final = await server.get_transaction_rules(limit=2, offset=2)
    assert final.rules == [
        {
            "id": "rule_m",
            "criteria": [{"field": "notes", "value": ""}],
            "actions": [{"field": "merchant", "value": "Synthetic Store"}],
        }
    ]
    assert (final.count, final.total_count, final.offset, final.next_offset) == (1, 3, 2, None)

    empty = await server.get_transaction_rules(limit=2, offset=4)
    assert empty.rules == []
    assert (empty.count, empty.total_count, empty.offset, empty.next_offset) == (0, 3, 4, None)
    assert response == original

    verbose = await server.get_transaction_rules(limit=1, verbose=True)
    assert verbose.rules == original["transactionRules"][:1]
    assert (verbose.count, verbose.total_count, verbose.offset, verbose.next_offset) == (1, 3, 0, 1)
    assert verbose.verbose is True
    assert response == original


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_rules_reject_invalid_paging_before_api(mock_api: AsyncMock, arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        await server.get_transaction_rules(**arguments)
    mock_api.assert_not_awaited()


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
async def test_history_rejects_invalid_paging_before_api(mock_api: AsyncMock, arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        await server.get_account_history("acc_001", **arguments)
    mock_api.assert_not_awaited()
