#!/usr/bin/env python3
"""Expose Monarch Money financial data and account updates through MCP."""

import asyncio
import contextlib
import functools
import io
import json
import logging
import os
import re
import signal
import stat
import sys
import time
import warnings
from collections.abc import Awaitable, Callable, Iterator
from datetime import date, datetime, timedelta
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Literal, ParamSpec, TypeVar

import aiohttp
import structlog
from anyio import AsyncFile
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
from gql import GraphQLRequest, gql
from gql.transport.exceptions import TransportError, TransportServerError
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.stdio import stdio_server
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
    ToolAnnotations,
)
from monarchmoney import LoginFailedException, MonarchMoney, RequestFailedException, RequireMFAException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, TypeAdapter, model_validator

JsonSerializable = str | int | float | bool | None | list["JsonSerializable"] | dict[str, "JsonSerializable"]

# Tools communicate only with the Monarch Money API.
READONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
WRITE_CREATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
WRITE_SIDE_EFFECT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
WRITE_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
WRITE_CREATE_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def _named_period(date_input: str, today: date) -> tuple[date, date] | None:
    """Return the first and last day of a named period, or None for other input."""
    if date_input in ("today", "now"):
        return today, today
    if date_input == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if date_input in ("this month", "current month"):
        start = today.replace(day=1)
        return start, start + relativedelta(months=1, days=-1)
    if date_input in ("last month", "previous month"):
        start = today.replace(day=1) - relativedelta(months=1)
        return start, start + relativedelta(months=1, days=-1)
    if date_input in ("this year", "current year"):
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if date_input in ("last year", "previous year"):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if date_input == "this week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if date_input == "last week":
        # Earlier releases treated this as the single day a week ago.
        day = today - timedelta(days=7)
        return day, day
    return None


def _parsed_period(date_input: str, today: date) -> tuple[date, date]:
    """Parse a date, widening year-only and month-only input to the whole period."""
    # Parsing with two defaults reveals omitted fields: "2024-02" fills day 1 and
    # day 29 (dateutil clamps to the month's end), while a full date is unaffected.
    first = date_parser.parse(date_input, default=datetime(today.year, 1, 1)).date()
    last = date_parser.parse(date_input, default=datetime(today.year, 12, 31)).date()
    whole_month = first.day == 1 and last == first + relativedelta(months=1, days=-1)
    whole_year = first == date(first.year, 1, 1) and last == date(first.year, 12, 31)
    if first == last or whole_month or whole_year:
        return first, last
    # Other omissions, such as "15" or "monday", resolve relative to today.
    day = date_parser.parse(date_input, default=datetime(today.year, today.month, today.day)).date()
    return day, day


def parse_date_period(date_input: str) -> tuple[date, date]:
    """Resolve a date phrase to the first and last day it covers.

    Named periods ("last month") and partial dates ("2024", "2024-02", "January 2024")
    cover several days; full dates, "today", and relative dates cover one.
    """
    if not date_input:
        raise ValueError("Date input cannot be empty")

    date_input = date_input.lower().strip()
    today = date.today()

    named = _named_period(date_input, today)
    if named is not None:
        return named

    relative_pattern = re.match(r"(\d+)\s+(days?|weeks?|months?|years?)\s+ago", date_input)
    if relative_pattern:
        amount = int(relative_pattern.group(1))
        unit = relative_pattern.group(2).rstrip("s")
        try:
            if unit == "day":
                day = today - timedelta(days=amount)
            elif unit == "week":
                day = today - timedelta(weeks=amount)
            elif unit == "month":
                day = today - relativedelta(months=amount)
            else:
                day = today - relativedelta(years=amount)
        except (ValueError, OverflowError) as e:
            log.warning("Invalid relative date calculation", input=date_input, amount=amount, unit=unit, error=str(e))
            raise ValueError(f"Invalid relative date: {date_input}") from e
        return day, day

    try:
        first, last = _parsed_period(date_input, today)
    except (ValueError, TypeError, OverflowError) as e:
        log.warning("Failed to parse date with dateutil", input=date_input, error=str(e))

        suggestions = [
            "Try formats like: 2024-01-15, Jan 15 2024, 01/15/2024 (month first), 2024-01, 2024",
            "Or natural language: today, yesterday, last month, this year",
            "Or relative: 30 days ago, 6 months ago, 1 year ago",
        ]
        suggestion_text = ". ".join(suggestions)
        raise ValueError(f"Could not parse date '{date_input}'. {suggestion_text}") from e

    min_date = date(1900, 1, 1)
    max_date = date(today.year + 50, 12, 31)
    if first < min_date or last > max_date:
        log.warning("Date outside reasonable range", input=date_input, parsed_date=first.isoformat())
        raise ValueError(f"Date {first.isoformat()} is outside reasonable range (1900-{today.year + 50})")

    return first, last


def parse_flexible_date(date_input: str) -> date:
    """Parse a start date: the first day of a named period or partial date."""
    return parse_date_period(date_input)[0]


def parse_flexible_end_date(date_input: str) -> date:
    """Parse an end date: the last day of a named period or partial date."""
    return parse_date_period(date_input)[1]


def build_date_filter(start_date: str | None, end_date: str | None) -> dict[str, str]:
    """Build ISO date filters; reject unparseable or reversed ranges.

    Periods such as "last month" or "2024" start on their first day and end on
    their last. Monarch requires both dates or neither. A missing end defaults
    to today; a missing start to the first day of the end period's first month.
    """
    if not start_date and not end_date:
        return {}

    today = date.today()
    end_first, end = parse_date_period(end_date) if end_date else (today, today)
    start = parse_flexible_date(start_date) if start_date else end_first.replace(day=1)

    if start > end:
        log.warning("Start date is after end date", start_date=start.isoformat(), end_date=end.isoformat())
        raise ValueError(f"Start date ({start.isoformat()}) cannot be after end date ({end.isoformat()})")

    log.info(
        "Parsed date range", start_input=start_date, end_input=end_date, start=start.isoformat(), end=end.isoformat()
    )
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def convert_dates_to_strings(obj: Any) -> Any:
    """Convert nested dates to ISO strings before MCP serializes the response."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {key: convert_dates_to_strings(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_dates_to_strings(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_dates_to_strings(item) for item in obj)
    else:
        return obj


def extract_transactions_list(response: Any) -> list[dict[str, Any]]:
    """Extract allTransactions.results, accepting a bare list as well."""
    if isinstance(response, list):
        return response
    elif isinstance(response, dict):
        if "allTransactions" in response:
            all_txns = response["allTransactions"]
            if isinstance(all_txns, dict) and "results" in all_txns:
                results = all_txns["results"]
                if isinstance(results, list):
                    return results
        log.warning("Unexpected transaction response structure", keys=list(response.keys()))
        return []
    else:
        log.error("Unexpected transaction response type", response_type=str(type(response)))
        return []


def extract_list(response: Any, key: str) -> list[Any]:
    """Unwrap a named API list, accept a bare list, or return [] for other shapes."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        value = response.get(key)
        if isinstance(value, list):
            return value
    return []


def format_transactions_compact(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep IDs, date, amount, merchant, statement name, category, account, and review
    status, plus pending, notes, and tag names when present. Public tools document these fields.
    """
    compact: list[dict[str, Any]] = []

    for txn in transactions:
        if not isinstance(txn, dict):
            continue

        category = txn.get("category")
        compact_txn: dict[str, Any] = {
            "id": txn.get("id"),
            "date": txn.get("date"),
            "amount": txn.get("amount"),
            "merchant": txn.get("merchant", {}).get("name") if isinstance(txn.get("merchant"), dict) else None,
            "plaidName": txn.get("plaidName"),
            "category": category.get("name") if isinstance(category, dict) else None,
            "categoryId": category.get("id") if isinstance(category, dict) else None,
            "account": txn.get("account", {}).get("displayName") if isinstance(txn.get("account"), dict) else None,
            "needsReview": txn.get("needsReview", False),
        }

        if txn.get("pending"):
            compact_txn["pending"] = True

        if txn.get("notes"):
            compact_txn["notes"] = txn.get("notes")

        tags = txn.get("tags")
        if isinstance(tags, list):
            tag_names = [tag.get("name") for tag in tags if isinstance(tag, dict) and tag.get("name")]
            if tag_names:
                compact_txn["tags"] = tag_names

        compact.append(compact_txn)

    return compact


def _build_transaction_filters(
    start_date: str | None,
    end_date: str | None,
    account_id: str | None = None,
    category_id: str | None = None,
    tag_ids: str | None = None,
    has_attachments: bool | None = None,
    has_notes: bool | None = None,
    hidden_from_reports: bool | None = None,
    is_split: bool | None = None,
    is_recurring: bool | None = None,
    is_pending: bool | None = None,
    needs_review: bool | None = None,
) -> dict[str, Any]:
    """Build shared filters for transaction retrieval and search."""
    filters: dict[str, Any] = build_date_filter(start_date, end_date)

    if account_id:
        filters["account_ids"] = [account_id]
    if category_id:
        filters["category_ids"] = [category_id]
    if tag_ids:
        filters["tag_ids"] = [t.strip() for t in tag_ids.split(",")]

    if has_attachments is not None:
        filters["has_attachments"] = has_attachments
    if has_notes is not None:
        filters["has_notes"] = has_notes
    if hidden_from_reports is not None:
        filters["hidden_from_reports"] = hidden_from_reports
    if is_split is not None:
        filters["is_split"] = is_split
    if is_recurring is not None:
        filters["is_recurring"] = is_recurring
    if is_pending is not None:
        filters["is_pending"] = is_pending
    if needs_review is not None:
        filters["needs_review"] = needs_review

    return filters


# Keep logs off stdout, which carries the MCP protocol.
class SafeStreamHandler(logging.StreamHandler):
    """Ignore broken pipes when the MCP client disconnects."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.handleError(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[SafeStreamHandler(sys.stderr)],
)

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

log = structlog.get_logger(__name__)

# Suppress third-party library logging to reduce noise
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("monarchmoney").setLevel(logging.ERROR)
logging.getLogger("gql").setLevel(logging.ERROR)
logging.getLogger("gql.transport").setLevel(logging.ERROR)

warnings.filterwarnings("ignore", category=UserWarning, module="gql.transport.aiohttp")

P = ParamSpec("P")
R = TypeVar("R")

# MCPServer shows the model only ToolError and ResourceError text. These failures carry
# messages meant for the caller: our validation (pydantic's ValidationError is a ValueError),
# Monarch rejections, auth, network errors, and session-file errors such as a private
# directory that can't be written. Anything else is a bug whose text may expose internals,
# so the client gets a generic error and the details stay in the server log.
ANTICIPATED_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    OSError,
    asyncio.TimeoutError,
    LoginFailedException,
    RequestFailedException,
    RequireMFAException,
    TransportError,
    aiohttp.ClientError,
)


def caller_message(error: Exception) -> str:
    """Return text for the client; timeouts and some network errors have an empty str()."""
    return str(error) or type(error).__name__


def track_usage(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Log tool arguments, timing, errors, and result sizes."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start_time = time.time()
        tool_name = func.__name__

        # Exclude credential keywords; financial inputs may still appear in logs.
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ["password", "mfa_secret"]}

        log.info("tool_call", tool=tool_name, args=safe_kwargs)

        try:
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time

            # Measure serialized character count, not transport bytes.
            if isinstance(result, BaseModel):
                payload = result.model_dump_json()
            elif isinstance(result, str):
                payload = result
            else:
                payload = str(result) if result else ""
            result_chars = len(payload)
            result_kb = result_chars / 1024

            log.info(
                "tool_success",
                tool=tool_name,
                time_s=round(execution_time, 3),
                result_chars=result_chars,
                result_kb=round(result_kb, 2),
            )

            return result

        except Exception as e:
            execution_time = time.time() - start_time

            log.error("tool_error", tool=tool_name, time_s=round(execution_time, 3), error=str(e))
            if isinstance(e, ANTICIPATED_ERRORS):
                raise ToolError(caller_message(e)) from e
            raise

    return wrapper


def resource_errors(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Surface anticipated resource failures to the client instead of a generic error."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except ANTICIPATED_ERRORS as e:
            raise ResourceError(caller_message(e)) from e

    return wrapper


def _server_version() -> str:
    # Running server.py from a checkout without installing the package has no metadata.
    try:
        return package_version("monarch-mcp-jamiew")
    except PackageNotFoundError:
        return ""


mcp = MCPServer("monarch-money", version=_server_version())


# Structured output models give MCP clients schemas and machine-readable content.
# JsonValue and extra fields preserve evolving upstream payloads.


class MMModel(BaseModel):
    """Preserve extra upstream response fields."""

    model_config = ConfigDict(extra="allow")


class AccountsResult(MMModel):
    accounts: list[JsonValue]
    count: int


class TransactionsResult(MMModel):
    transactions: list[JsonValue]
    count: int
    total_count: int | None = None
    next_offset: int | None = None
    verbose: bool


class SearchMetadata(BaseModel):
    query: str
    result_count: int
    filters_applied: dict[str, JsonValue]


class SearchResult(MMModel):
    search_metadata: SearchMetadata
    transactions: list[JsonValue]
    total_count: int | None = None
    next_offset: int | None = None


class BudgetsResult(MMModel):
    budgets: JsonValue
    message: str | None = None


class CashflowResult(MMModel):
    cashflow: JsonValue


class CategoriesResult(MMModel):
    categories: list[JsonValue]
    count: int
    verbose: bool


class TransactionResult(MMModel):
    transaction: JsonValue


class TransactionUpdatePayload(BaseModel):
    errors: list[JsonValue] | None = None
    transaction: dict[str, JsonValue] | None


class TransactionUpdateEnvelope(BaseModel):
    updateTransaction: TransactionUpdatePayload


def _require_nonzero_amount(amount: float | None) -> None:
    # The client sends an amount only when it is truthy, so 0 would be dropped
    # while the update still reported success.
    if amount == 0:
        raise ValueError("amount cannot be 0: Monarch's client ignores a zero amount")


def _check_transaction_update(result: object) -> None:
    payload = TransactionUpdateEnvelope.model_validate(result).updateTransaction
    if payload.errors:
        raise ValueError(f"Monarch rejected the transaction update: {payload.errors}")
    if payload.transaction is None:
        raise ValueError("Monarch returned no updated transaction")


class TransactionSplit(BaseModel):
    """One leg of a split transaction.

    Monarch requires split amounts to sum to the parent amount, using the parent's
    sign convention: negative expenses, positive income.
    """

    amount: float
    category_id: str | None = None
    merchant_name: str | None = None
    notes: str | None = None


class TransactionSplitsResult(MMModel):
    transaction_id: str
    has_split_transactions: bool
    splits: list[JsonValue]


class TransactionDetailsResult(MMModel):
    transaction_id: str
    amount: float | None
    merchant: str | None
    category_id: str | None
    has_split_transactions: bool
    is_split_transaction: bool
    parent_transaction_id: str | None
    split_transaction_ids: list[str]


class TransactionReference(BaseModel):
    id: str = Field(min_length=1)


class TransactionMerchant(BaseModel):
    name: str | None = None


class TransactionSplitsPayload(TransactionReference):
    splitTransactions: list[JsonValue]


class TransactionDetailsPayload(TransactionReference):
    splitTransactions: list[TransactionReference]
    amount: float | None
    merchant: TransactionMerchant | None
    category: TransactionReference | None
    hasSplitTransactions: bool
    isSplitTransaction: bool
    originalTransaction: TransactionReference | None


def _require_transaction(result: object) -> object:
    """Reject missing transactions before validating the requested fields."""
    if not isinstance(result, dict):
        raise ValueError("Monarch returned an invalid transaction response")
    transaction = result.get("getTransaction")
    if transaction is None:
        raise ValueError("Monarch returned no transaction")
    return transaction


class UpdateSplitsResult(MMModel):
    transaction_id: str
    has_split_transactions: bool
    splits: list[JsonValue]
    message: str


class BulkTransactionUpdate(BaseModel):
    """Validate a bulk item before mutating its transaction."""

    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    transaction_id: str = Field(min_length=1)
    amount: FiniteFloat | None = None
    merchant_name: str | None = None
    category_id: str | None = None
    date: str | None = None
    notes: str | None = None
    goal_id: str | None = None
    hide_from_reports: bool | None = None
    needs_review: bool | None = None
    owner_user_id: str | None = None


class BulkSummary(BaseModel):
    total: int
    succeeded: int
    failed: int


class BulkItemResult(BaseModel):
    transaction_id: str | None = None
    status: str
    error: str | None = None


class BulkUpdateResult(MMModel):
    summary: BulkSummary
    results: list[BulkItemResult]
    message: str | None = None


class HoldingsResult(MMModel):
    holdings: JsonValue


class BrokerageHoldingsResult(MMModel):
    accounts: list[JsonValue]


class TransactionRulesResult(MMModel):
    rules: list[JsonValue]
    count: int
    total_count: int
    offset: int
    next_offset: int | None
    verbose: bool


class RuleTextCriterion(BaseModel):
    """Match text that contains the value ("contains") or equals it exactly ("eq")."""

    model_config = ConfigDict(extra="forbid")

    operator: Literal["contains", "eq"] = "contains"
    value: str = Field(min_length=1)


class RuleAmountCriterion(BaseModel):
    """Match an amount, ignoring its sign.

    is_expense=True matches money going out. False matches money coming in.
    "between" uses lower and upper. Every other operator uses value.
    """

    model_config = ConfigDict(extra="forbid")

    operator: Literal["gt", "lt", "eq", "between"]
    is_expense: bool = True
    value: FiniteFloat | None = Field(default=None, ge=0)
    lower: FiniteFloat | None = Field(default=None, ge=0)
    upper: FiniteFloat | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> "RuleAmountCriterion":
        # If we dropped a missing limit, the rule would match more than asked, so reject it.
        if self.operator == "between":
            if self.lower is None or self.upper is None or self.value is not None:
                raise ValueError("between requires lower and upper, and no value")
            if self.lower > self.upper:
                raise ValueError("lower must not exceed upper")
        elif self.value is None or self.lower is not None or self.upper is not None:
            raise ValueError(f"{self.operator} requires value, and no lower or upper")
        return self

    def to_input(self) -> dict[str, JsonValue]:
        value_range: JsonValue = None
        if self.operator == "between":
            value_range = {"lower": self.lower, "upper": self.upper}
        return {"operator": self.operator, "isExpense": self.is_expense, "value": self.value, "valueRange": value_range}


# Shapes for reading an existing rule. Monarch's update deletes any action left out
# of the input, so an update must send back everything the rule already has.


class _RuleRef(BaseModel):
    id: str
    name: str | None = None


class _RuleCriterion(BaseModel):
    operator: str
    value: str


class _RuleValueRange(BaseModel):
    lower: float | None = None
    upper: float | None = None


class _RuleAmount(BaseModel):
    operator: str
    isExpense: bool
    value: float | None = None
    valueRange: _RuleValueRange | None = None


class _RuleSplits(BaseModel):
    amountType: str
    splitsInfo: list[dict[str, JsonValue]]


class ExistingRule(BaseModel):
    id: str
    merchantCriteriaUseOriginalStatement: bool | None = None
    merchantCriteria: list[_RuleCriterion] | None = None
    merchantNameCriteria: list[_RuleCriterion] | None = None
    originalStatementCriteria: list[_RuleCriterion] | None = None
    amountCriteria: _RuleAmount | None = None
    categoryIds: list[str] | None = None
    accountIds: list[str] | None = None
    criteriaOwnerIsJoint: bool | None = None
    criteriaOwnerUserIds: list[str] | None = None
    criteriaBusinessEntityIds: list[str] | None = None
    criteriaBusinessEntityIsUnassigned: bool | None = None
    setMerchantAction: _RuleRef | None = None
    setCategoryAction: _RuleRef | None = None
    addTagsAction: list[_RuleRef] | None = None
    linkGoalAction: _RuleRef | None = None
    linkSavingsGoalAction: _RuleRef | None = None
    needsReviewByUserAction: _RuleRef | None = None
    unassignNeedsReviewByUserAction: bool | None = None
    sendNotificationAction: bool | None = None
    setHideFromReportsAction: bool | None = None
    setLinkToPaydownBudgetAction: bool | None = None
    reviewStatusAction: str | None = None
    actionSetOwnerIsJoint: bool | None = None
    actionSetOwner: _RuleRef | None = None
    actionSetBusinessEntity: _RuleRef | None = None
    actionSetBusinessEntityIsUnassigned: bool | None = None
    splitTransactionsAction: _RuleSplits | None = None

    def to_input(self) -> dict[str, JsonValue]:
        """Build mutation input from this rule, skipping empty fields."""
        rule: dict[str, JsonValue] = {"id": self.id}
        if self.merchantCriteriaUseOriginalStatement is not None:
            rule["merchantCriteriaUseOriginalStatement"] = self.merchantCriteriaUseOriginalStatement
        for key, criteria in (
            ("merchantCriteria", self.merchantCriteria),
            ("merchantNameCriteria", self.merchantNameCriteria),
            ("originalStatementCriteria", self.originalStatementCriteria),
        ):
            if criteria:
                rule[key] = [{"operator": c.operator, "value": c.value} for c in criteria]
        if self.amountCriteria is not None:
            amount = self.amountCriteria
            value_range: JsonValue = None
            if amount.valueRange is not None:
                value_range = {"lower": amount.valueRange.lower, "upper": amount.valueRange.upper}
            rule["amountCriteria"] = {
                "operator": amount.operator,
                "isExpense": amount.isExpense,
                "value": amount.value,
                "valueRange": value_range,
            }
        for key, ids in (
            ("categoryIds", self.categoryIds),
            ("accountIds", self.accountIds),
            ("criteriaOwnerUserIds", self.criteriaOwnerUserIds),
            ("criteriaBusinessEntityIds", self.criteriaBusinessEntityIds),
        ):
            if ids:
                rule[key] = list(ids)
        for key, flag in (
            ("criteriaOwnerIsJoint", self.criteriaOwnerIsJoint),
            ("criteriaBusinessEntityIsUnassigned", self.criteriaBusinessEntityIsUnassigned),
            ("unassignNeedsReviewByUserAction", self.unassignNeedsReviewByUserAction),
            ("sendNotificationAction", self.sendNotificationAction),
            ("setHideFromReportsAction", self.setHideFromReportsAction),
            ("setLinkToPaydownBudgetAction", self.setLinkToPaydownBudgetAction),
            ("actionSetOwnerIsJoint", self.actionSetOwnerIsJoint),
            ("actionSetBusinessEntityIsUnassigned", self.actionSetBusinessEntityIsUnassigned),
        ):
            if flag:
                rule[key] = True
        # Monarch expects a merchant name here. An ID would create a merchant named after the ID.
        if self.setMerchantAction is not None and self.setMerchantAction.name:
            rule["setMerchantAction"] = self.setMerchantAction.name
        for key, ref in (
            ("setCategoryAction", self.setCategoryAction),
            ("linkGoalAction", self.linkGoalAction),
            ("linkSavingsGoalAction", self.linkSavingsGoalAction),
            ("actionSetOwner", self.actionSetOwner),
            ("actionSetBusinessEntity", self.actionSetBusinessEntity),
        ):
            if ref is not None:
                rule[key] = ref.id
        if self.addTagsAction:
            rule["addTagsAction"] = [tag.id for tag in self.addTagsAction]
        if self.reviewStatusAction:
            rule["reviewStatusAction"] = self.reviewStatusAction
            # Monarch rejects a reviewer unless a review status is also set.
            if self.needsReviewByUserAction is not None:
                rule["needsReviewByUserAction"] = self.needsReviewByUserAction.id
        if self.splitTransactionsAction is not None and self.splitTransactionsAction.splitsInfo:
            rule["splitTransactionsAction"] = {
                "amountType": self.splitTransactionsAction.amountType,
                "splitsInfo": [
                    {k: v for k, v in info.items() if k != "__typename"}
                    for info in self.splitTransactionsAction.splitsInfo
                ],
            }
        return rule


class _RuleFieldError(BaseModel):
    field: str | None = None
    messages: list[str] | None = None


class _RulePayloadError(BaseModel):
    message: str | None = None
    code: str | None = None
    fieldErrors: list[_RuleFieldError] | None = None

    def describe(self) -> str | None:
        parts = [self.message] if self.message else []
        parts += [f"{e.field}: {', '.join(e.messages or [])}" for e in self.fieldErrors or []]
        return "; ".join(parts) or None


class _RuleMutationPayload(BaseModel):
    errors: _RulePayloadError | list[_RulePayloadError] | None = None
    transactionRule: _RuleRef | None = None

    def check(self, action: str) -> None:
        """Raise if Monarch rejected the change, even when every error field is empty."""
        errors = self.errors if isinstance(self.errors, list) else [self.errors] if self.errors else []
        if not errors:
            return
        reasons = [reason for e in errors if (reason := e.describe())]
        raise ValueError(f"Monarch rejected the rule {action}: {'; '.join(reasons) or 'no reason given'}")


# Monarch ignores rule updates without one of these. Create requires one too, so every
# rule this server makes can be edited later.
RULE_MATCH_KEYS = ("merchantCriteria", "merchantNameCriteria", "originalStatementCriteria", "amountCriteria")
RULE_ACTION_KEYS = (
    "setMerchantAction",
    "setCategoryAction",
    "addTagsAction",
    "linkGoalAction",
    "linkSavingsGoalAction",
    "needsReviewByUserAction",
    "unassignNeedsReviewByUserAction",
    "sendNotificationAction",
    "setHideFromReportsAction",
    "setLinkToPaydownBudgetAction",
    "reviewStatusAction",
    "actionSetOwner",
    "actionSetOwnerIsJoint",
    "actionSetBusinessEntity",
    "actionSetBusinessEntityIsUnassigned",
    "splitTransactionsAction",
)


class TransactionRuleResult(MMModel):
    rule: JsonValue
    applied_to_existing_transactions: bool


class DeleteTransactionRuleResult(MMModel):
    rule_id: str
    deleted: bool


class HouseholdMembersResult(MMModel):
    members: list[JsonValue]
    count: int


class AccountHistorySnapshot(MMModel):
    date: date


class AccountHistoryResult(MMModel):
    account_id: str
    history: JsonValue
    count: int
    total_count: int
    offset: int
    next_offset: int | None


class InstitutionsResult(MMModel):
    # The upstream payload groups institution credentials, accounts, and subscription.
    institutions: JsonValue


class RecurringResult(MMModel):
    recurring: JsonValue


class RecurringMerchant(BaseModel):
    id: str
    name: str
    recurringTransactionStream: dict[str, JsonValue] | None


class UpdateRecurringResult(MMModel):
    merchant: RecurringMerchant


class SetBudgetResult(MMModel):
    category_id: str
    amount: float
    month: str
    apply_to_future: bool
    result: JsonValue


class CreateAccountResult(MMModel):
    account: JsonValue


class RefreshResult(MMModel):
    result: JsonValue


class Totals(BaseModel):
    income: float
    expenses: float
    net: float


class GroupSummary(BaseModel):
    income: float
    expenses: float
    net: float
    count: int


class Period(BaseModel):
    start: str | None = None
    end: str | None = None


class SpendingSummaryResult(MMModel):
    period: Period
    group_by: str
    groups: dict[str, GroupSummary]
    totals: Totals
    transfers_excluded: int = 0


class FinancialOverview(MMModel):
    period: str
    verbose: bool = False
    accounts: JsonValue = None
    budgets: JsonValue = None
    cashflow: JsonValue = None
    transactions: JsonValue = None
    categories: JsonValue = None
    transaction_summary: JsonValue = None
    batch_metadata: JsonValue = None


class SpendingPatterns(MMModel):
    verbose: bool = False
    analysis_period: JsonValue = None
    monthly_trends: JsonValue = None
    category_analysis: JsonValue = None
    account_usage: JsonValue = None
    budget_performance: JsonValue = None
    forecast: JsonValue = None
    metadata: JsonValue = None
    errors: dict[str, str] = Field(default_factory=dict)


def _compact_payload(value: JsonValue) -> JsonValue:
    """Drop unused object fields without losing false, zero, or empty-string values."""
    if isinstance(value, dict):
        compact: dict[str, JsonValue] = {}
        for key, item in value.items():
            if key == "__typename":
                continue
            reduced = _compact_payload(item)
            if reduced is not None and reduced != [] and reduced != {}:
                compact[key] = reduced
        return compact
    if isinstance(value, list):
        return [_compact_payload(item) for item in value]
    return value


def _budget_totals(response: object, verbose: bool = False) -> dict[str, JsonValue]:
    payload = TypeAdapter(dict[str, JsonValue]).validate_python(response)
    data = payload.get("budgetData")
    if not isinstance(data, dict) or not isinstance(data.get("totalsByMonth"), list):
        raise ValueError("Budget response is missing totalsByMonth")
    return payload if verbose else {"totalsByMonth": _compact_payload(data["totalsByMonth"])}


def _cashflow_summary(response: object, verbose: bool = False) -> JsonValue:
    payload = TypeAdapter(dict[str, JsonValue]).validate_python(response)
    aggregates = payload.get("summary")
    if not isinstance(aggregates, list) or len(aggregates) != 1:
        raise ValueError("Cashflow response must contain one summary aggregate")
    aggregate = aggregates[0]
    if not isinstance(aggregate, dict) or not isinstance(aggregate.get("summary"), dict):
        raise ValueError("Cashflow response is missing summary")
    return payload if verbose else _compact_payload(aggregate["summary"])


def _transactions_truncated(response: object, returned: int) -> bool | None:
    if isinstance(response, dict):
        transactions = response.get("allTransactions")
        if isinstance(transactions, dict):
            total = transactions.get("totalCount")
            if isinstance(total, int) and not isinstance(total, bool):
                return total > returned
    return None


class _CategoryGroupRef(BaseModel):
    type: str | None = None


class _CategoryWithGroup(BaseModel):
    id: str | None = None
    group: _CategoryGroupRef | None = None


def _transfer_category_ids(categories_response: object) -> set[str]:
    """Return IDs of categories whose group type is "transfer", such as card payments.

    Transactions from get_transactions carry only the category ID and name, so
    the group type comes from get_transaction_categories.
    """
    categories = TypeAdapter(list[_CategoryWithGroup]).validate_python(extract_list(categories_response, "categories"))
    return {c.id for c in categories if c.id and c.group is not None and c.group.type == "transfer"}


def _is_transfer(txn: dict[str, Any], transfer_ids: set[str]) -> bool:
    # Moving money between accounts is neither income nor spending; counting a
    # card payment as an expense double-counts the purchases it pays for.
    category = txn.get("category")
    if not isinstance(category, dict):
        return False
    group = category.get("group")
    if isinstance(group, dict) and group.get("type") == "transfer":
        return True
    return category.get("id") in transfer_ids


# MCP resources


@mcp.resource("categories://list", title="Transaction Categories")
@resource_errors
async def list_categories_resource() -> str:
    """Return Monarch's category JSON, including IDs for transaction updates."""
    await ensure_authenticated()
    categories = await api_call_with_retry("get_transaction_categories")
    return json.dumps(convert_dates_to_strings(categories), indent=2)


@mcp.resource("accounts://list", title="Linked Accounts")
@resource_errors
async def list_accounts_resource() -> str:
    """Return Monarch's linked-account JSON with balances and institution details."""
    await ensure_authenticated()
    accounts = await api_call_with_retry("get_accounts")
    return json.dumps(convert_dates_to_strings(accounts), indent=2)


@mcp.resource("institutions://list", title="Linked Institutions")
@resource_errors
async def list_institutions_resource() -> str:
    """Return Monarch's connected-institution JSON."""
    await ensure_authenticated()
    institutions = await api_call_with_retry("get_institutions")
    return json.dumps(convert_dates_to_strings(institutions), indent=2)


def _require_account_id(account_id: str) -> None:
    # RFC 6570 templates match an empty {account_id}, as in accounts:///holdings.
    if not account_id.strip():
        raise ValueError("account_id must not be blank")


@mcp.resource("accounts://{account_id}/holdings", title="Account Holdings")
@resource_errors
async def account_holdings_resource(account_id: str) -> str:
    """Return investment holdings for the account_id in the resource path."""
    _require_account_id(account_id)
    await ensure_authenticated()
    holdings = await api_call_with_retry("get_account_holdings", account_id=account_id)
    return json.dumps(convert_dates_to_strings(holdings), indent=2)


@mcp.resource("accounts://{account_id}/history", title="Account Balance History")
@resource_errors
async def account_history_resource(account_id: str) -> str:
    """Return balance history for the account_id in the resource path."""
    _require_account_id(account_id)
    await ensure_authenticated()
    history = await api_call_with_retry("get_account_history", account_id=account_id)
    return json.dumps(convert_dates_to_strings(history), indent=2)


# MCP prompts


@mcp.prompt(title="Analyze Spending")
def analyze_spending(period: str = "this month", category: str | None = None) -> str:
    """Request a spending analysis.

    Args:
        period: Time period to analyze (e.g., "this month", "last 3 months", "2024")
        category: Optional category to focus on (e.g., "Food & Dining", "Shopping")
    """
    category_focus = f" specifically for {category}" if category else ""
    return f"""Please analyze my spending{category_focus} for {period}.

Use get_transactions to fetch the period's transactions, then summarize total
expenses, top categories, notable patterns, and practical spending recommendations."""


@mcp.prompt(title="Budget Review")
def budget_review(month: str = "current") -> str:
    """Request a budget review.

    Args:
        month: Which month to review ("current", "last", or "YYYY-MM" format)
    """
    return f"""Please review my budget performance for {month}.

Use get_budgets and get_transactions to show each category's budget, actual spending,
and variance. Highlight over- and under-budget categories, assess progress for the
month, and suggest adjustments based on spending patterns."""


@mcp.prompt(title="Financial Health Check")
def financial_health_check() -> str:
    """Request a review of accounts, cash flow, spending, and budgets."""
    return """Please review my financial health using the available tools.

1. **Accounts**: Summarize balances, assets, liabilities, and net worth.
2. **Cash Flow**: Compare monthly income and expenses, calculate savings rate,
   and review recurring transactions.
3. **Spending**: Show top categories and unusual or large transactions for the
   last 30 days; compare with the previous month.
4. **Budgets**: Identify categories on and off track and project month-end status.
5. **Actions**: Recommend changes and highlight positive trends to maintain.

Lead with the most important findings."""


@mcp.prompt(title="Categorize a Transaction")
def transaction_categorization_help(description: str) -> str:
    """Request a category recommendation.

    Args:
        description: The transaction description or merchant name
    """
    return f"""Help me categorize this transaction: "{description}"

Use categories://list to suggest the best category, alternatives, and your reasoning.
If I use this merchant frequently, note whether the recommendation also fits future
transactions."""


# MCP argument completions


async def _category_name_completions(partial: str) -> list[str]:
    """Suggest live category names; return [] if the lookup fails."""
    try:
        await ensure_authenticated()
        categories = extract_list(await api_call_with_retry("get_transaction_categories"), "categories")
    except Exception as e:
        log.warning("completion_categories_failed", error=str(e))
        return []
    names = [c.get("name", "") for c in categories if isinstance(c, dict) and c.get("name")]
    needle = partial.lower()
    return [n for n in names if needle in n.lower()][:100]


async def _account_id_completions(partial: str) -> list[str]:
    """Suggest live account IDs; return [] if the lookup fails."""
    try:
        await ensure_authenticated()
        accounts = extract_list(await api_call_with_retry("get_accounts"), "accounts")
    except Exception as e:
        log.warning("completion_accounts_failed", error=str(e))
        return []
    ids = [a.get("id", "") for a in accounts if isinstance(a, dict) and a.get("id")]
    needle = partial.lower()
    return [i for i in ids if needle in i.lower()][:100]


@mcp.completion()
async def handle_completion(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,
) -> Completion | None:
    """Autocomplete prompt/resource-template arguments from live Monarch data.

    - prompt ``category`` argument -> transaction category names
    - resource-template ``account_id`` argument -> account IDs
    """
    if isinstance(ref, PromptReference) and argument.name == "category":
        return Completion(values=await _category_name_completions(argument.value), has_more=False)

    if isinstance(ref, ResourceTemplateReference) and argument.name == "account_id":
        return Completion(values=await _account_id_completions(argument.value), has_more=False)

    return None


class AuthState(Enum):
    """Track authentication state to prevent duplicate initialization attempts."""

    NOT_INITIALIZED = "not_initialized"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


mm_client: MonarchMoney | None = None
auth_state: AuthState = AuthState.NOT_INITIALIZED
auth_lock: asyncio.Lock | None = None  # Created in async context
auth_error: str | None = None
auth_failed_at: float | None = None
AUTH_RETRY_COOLDOWN_SECONDS = 60

# The library's 10-second default is too short for year-long reads and merchant lists.
CLIENT_TIMEOUT_SECONDS = 30
# Bounds a whole write, including a re-login and retry after an expired session,
# so it must outlast a single request that the client's own timeout already limits.
WRITE_TIMEOUT_SECONDS = 60

# Default to the home directory because MCP clients may launch from read-only "/".
# MONARCH_SESSION_DIR overrides this location.
_session_dir_env = os.getenv("MONARCH_SESSION_DIR")
session_dir = Path(_session_dir_env).expanduser() if _session_dir_env else Path.home() / ".monarch-mcp"
session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
session_file = session_dir / "session.pickle"

AUTH_STATUS_CODES = frozenset({401, 403})
# Fallback for errors without a status, such as upstream's "HTTP Code 401: Unauthorized".
# Bare numbers are not enough: "Transaction 1984015 not found" or "amount 403.20" are not auth errors.
_AUTH_ERROR_TEXT = re.compile(
    r"unauthori[sz]ed|forbidden|invalid credentials|bad credentials|authentication failed|auth failed"
    r"|not authenticated|invalid token|token expired|session (?:has )?expired"
    r"|\b(?:http|status)(?: code)?\s*[:=]?\s*40[13]\b"
)


def _error_chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_auth_error(error: Exception) -> bool:
    """Detect a rejected session, preferring the HTTP status over message text."""
    for err in _error_chain(error):
        if isinstance(err, TransportServerError) and err.code is not None:
            return err.code in AUTH_STATUS_CODES
        if isinstance(err, aiohttp.ClientResponseError):
            return err.status in AUTH_STATUS_CODES
        if isinstance(err, (TimeoutError, asyncio.TimeoutError, aiohttp.ClientConnectionError)):
            return False
    return _AUTH_ERROR_TEXT.search(str(error).lower()) is not None


def clear_session(reason: str = "unknown") -> None:
    """Reset the client and auth state, and remove session files.

    Use after auth failures or forced login, while holding auth_lock. Log the supplied reason.
    """
    global mm_client, auth_state, auth_error, auth_failed_at

    log.info("auth_reset", reason=reason, previous_state=auth_state.value)

    if mm_client is not None:
        mm_client = None

    auth_state = AuthState.NOT_INITIALIZED
    auth_error = None
    auth_failed_at = None

    for path in [session_file, session_dir / "mm_session.pickle"]:
        if path.exists():
            try:
                path.unlink()
                log.info("session_file_cleared", path=str(path))
            except Exception as e:
                log.warning("session_file_clear_failed", path=str(path), error=str(e))


def _get_auth_lock() -> asyncio.Lock:
    # Created lazily because the lock must belong to the running event loop.
    global auth_lock
    if auth_lock is None:
        auth_lock = asyncio.Lock()
    return auth_lock


async def _reauthenticate(failed_client: MonarchMoney, reason: str) -> None:
    """Replace the client whose session was rejected, unless another call already has.

    Calls in flight with an expired session all fail together. Only the first one
    to take the lock clears the session; the rest reuse the new login, because
    Monarch rejects a second login with the same TOTP code.
    """
    async with _get_auth_lock():
        if mm_client is failed_client:
            clear_session(reason=reason)
        else:
            log.info("auth_already_refreshed")
    await ensure_authenticated()


async def api_call_with_retry(method_name: str, *args: Any, max_retries: int = 3, **kwargs: Any) -> Any:
    """Call a client method, retrying recognized auth failures after reauthentication.

    max_retries excludes the initial attempt. Other errors propagate immediately.
    """
    for attempt in range(max_retries + 1):
        client = mm_client
        if client is None:
            # Another call may have cleared an expired session and be logging in again.
            await ensure_authenticated()
            client = mm_client
            if client is None:
                raise ValueError("mm_client is not initialized")

        try:
            return await getattr(client, method_name)(*args, **kwargs)
        except Exception as e:
            if not is_auth_error(e):
                raise
            if attempt >= max_retries:
                log.error("api_auth_retries_exhausted", max_retries=max_retries, error=str(e))
                raise
            log.warning("api_auth_error", attempt=attempt + 1, max_attempts=max_retries + 1, error=str(e))
            await _reauthenticate(client, reason=f"authentication failure during API call (attempt {attempt + 1})")
            log.info("api_retry_after_reauth", attempt=attempt + 2, max_attempts=max_retries + 1)

    raise RuntimeError("api_call_with_retry completed without result or error")


def _new_client() -> MonarchMoney:
    return MonarchMoney(timeout=CLIENT_TIMEOUT_SECONDS)


async def initialize_client() -> None:
    """Load a cached session or log in. The first API call detects expired sessions.

    The client is published only once it holds a session, so concurrent calls never
    use a half-initialized client.
    """
    global mm_client, auth_state, auth_error, auth_failed_at

    email = os.getenv("MONARCH_EMAIL")
    password = os.getenv("MONARCH_PASSWORD")
    mfa_secret = os.getenv("MONARCH_MFA_SECRET")

    if not email or not password:
        error_msg = "MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required"
        log.error("auth_missing_credentials")
        auth_state = AuthState.FAILED
        auth_error = error_msg
        raise ValueError(error_msg)

    log.info("auth_init")

    force_login = os.getenv("MONARCH_FORCE_LOGIN") == "true"
    if session_file.exists() and not force_login:
        client = _new_client()
        try:
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                client.load_session(str(session_file))
        except Exception as e:
            log.warning("auth_session_load_failed", error=str(e))
            if is_auth_error(e):
                clear_session(reason="invalid session file")
        else:
            mm_client = client
            auth_state = AuthState.AUTHENTICATED
            log.info("auth_session_loaded", session_file=str(session_file))
            return

    elif force_login:
        log.info("auth_force_login")
        clear_session(reason="forced login requested")

    max_retries = 2
    retry_delay = 3

    for attempt in range(max_retries):
        client = _new_client()
        try:
            log.info("auth_login_attempt", attempt=attempt + 1, max_retries=max_retries, mfa=bool(mfa_secret))
            # Disable the library's default .mm session storage; save to our path only.
            if mfa_secret:
                await client.login(
                    email, password, mfa_secret_key=mfa_secret, use_saved_session=False, save_session=False
                )
            else:
                await client.login(email, password, use_saved_session=False, save_session=False)

            # Suppress library output so it cannot corrupt the stdio protocol.
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                client.save_session(str(session_file))

            if session_file.exists():
                session_file.chmod(0o600)

        except RequireMFAException as e:
            # Monarch answers a rejected TOTP code the same way as a missing one.
            if mfa_secret:
                error_msg = (
                    "Monarch rejected the multi-factor code. Check that MONARCH_MFA_SECRET is the "
                    "account's TOTP secret and that the system clock is accurate."
                )
            else:
                error_msg = "Monarch requires multi-factor authentication but MONARCH_MFA_SECRET is not set"
            log.error("auth_mfa_required", mfa_secret_set=bool(mfa_secret))
            auth_state = AuthState.FAILED
            auth_error = error_msg
            auth_failed_at = time.time()
            raise ValueError(error_msg) from e

        except Exception as e:
            if attempt < max_retries - 1:
                log.warning("auth_attempt_failed", attempt=attempt + 1, error=str(e), is_auth=is_auth_error(e))
                await asyncio.sleep(retry_delay)
            else:
                log.error("auth_failed", error=str(e), max_retries=max_retries)
                auth_state = AuthState.FAILED
                auth_error = str(e)
                auth_failed_at = time.time()
                raise

        else:
            mm_client = client
            auth_state = AuthState.AUTHENTICATED
            auth_error = None
            log.info("auth_success")
            return


async def ensure_authenticated() -> None:
    """Initialize once under a lock, respecting the cooldown after timed failures.

    Cancelling a call mid-login leaves the state unauthenticated, so the next call retries.
    """
    global auth_state, auth_error, auth_failed_at

    if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
        return

    log.info("auth_needed", state=auth_state.value)

    async with _get_auth_lock():
        if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
            return

        if auth_state == AuthState.FAILED:
            if auth_failed_at is not None:
                elapsed = time.time() - auth_failed_at
                if elapsed < AUTH_RETRY_COOLDOWN_SECONDS:
                    remaining = AUTH_RETRY_COOLDOWN_SECONDS - elapsed
                    error_msg = (
                        f"Authentication previously failed: {auth_error or 'unknown error'}. "
                        f"Cooldown active: retry available in {remaining:.0f} seconds. "
                        f"To retry immediately, restart the server or set MONARCH_FORCE_LOGIN=true."
                    )
                    raise ValueError(error_msg)
                else:
                    log.info("auth_cooldown_elapsed", cooldown_s=AUTH_RETRY_COOLDOWN_SECONDS)
                    auth_state = AuthState.NOT_INITIALIZED
                    auth_error = None
                    auth_failed_at = None
            else:
                error_msg = f"Authentication previously failed: {auth_error or 'unknown error'}"
                raise ValueError(error_msg)

        try:
            await initialize_client()
            log.info("auth_lazy_init_success")
        except Exception as e:
            auth_state = AuthState.FAILED
            auth_error = str(e)
            log.error("auth_init_failed", error=str(e))
            raise


# MCP tools


@mcp.tool(annotations=READONLY, title="Get Accounts")
@track_usage
async def get_accounts() -> AccountsResult:
    """Retrieve all linked financial accounts."""
    await ensure_authenticated()

    try:
        accounts = await api_call_with_retry("get_accounts")
        accounts = convert_dates_to_strings(accounts)
        account_list = extract_list(accounts, "accounts")
        return AccountsResult(accounts=account_list, count=len(account_list))
    except Exception as e:
        log.error("Failed to fetch accounts", error=str(e))
        raise


@mcp.tool(annotations=READONLY, title="Get Transactions")
@track_usage
async def get_transactions(
    limit: int = 100,
    offset: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    tag_ids: str | None = None,
    has_attachments: bool | None = None,
    has_notes: bool | None = None,
    hidden_from_reports: bool | None = None,
    is_split: bool | None = None,
    is_recurring: bool | None = None,
    verbose: bool = False,
    is_pending: bool | None = None,
    needs_review: bool | None = None,
) -> TransactionsResult:
    """Fetch one page of transactions.

    Args:
        limit: Maximum transactions to request (default: 100).
        offset: Transactions to skip for pagination.
        start_date: Inclusive start; accepts ISO dates, 'last month', or '30 days ago'.
            Without end_date, the end defaults to today.
        end_date: Inclusive end, with the same date formats. Without start_date,
            the start defaults to the first day of the end date's month.
        account_id: Account ID from get_accounts.
        category_id: Category ID from get_transaction_categories.
        tag_ids: Comma-separated tag IDs.
        has_attachments: True for transactions with attachments, False for those without.
        has_notes: True for transactions with notes, False for those without.
        hidden_from_reports: True for hidden transactions, False for visible ones.
        is_split: True for split transactions, False for non-split transactions.
        is_recurring: True for recurring transactions, False for non-recurring ones.
        is_pending: True for pending transactions, False for posted transactions.
        needs_review: True for transactions flagged for review, False for reviewed ones.
        verbose: False returns compact fields; True preserves the full API objects.

    Boolean filters default to None (no restriction). Results contain transactions,
    count (this page), total_count (all matches; null if Monarch omits it), and
    next_offset (null when no more pages). Pass next_offset as offset for the next page.

    Compact fields: id, date, amount, merchant, plaidName, category, categoryId,
    account, needsReview; pending appears only when true, notes only when nonempty,
    tags (tag names) only when the transaction has tags.
    Amounts are negative for expenses and positive for income. Use id for updates.
    merchant is the display name; plaidName preserves the original statement text.
    category and account are names, not nested objects.

    Verbose results retain nested merchant/category/account objects and upstream
    fields such as hideFromReports, reviewStatus, isSplitTransaction, isRecurring,
    attachments, tags, and timestamps.
    """
    await ensure_authenticated()

    try:
        filters = _build_transaction_filters(
            start_date,
            end_date,
            account_id,
            category_id,
            tag_ids,
            has_attachments,
            has_notes,
            hidden_from_reports,
            is_split,
            is_recurring,
            is_pending,
            needs_review,
        )

        response = await api_call_with_retry("get_transactions", limit=limit, offset=offset, **filters)
        transactions = extract_transactions_list(response)
        transactions = convert_dates_to_strings(transactions)

        if not verbose and isinstance(transactions, list):
            transactions = format_transactions_compact(transactions)

        total_count, next_offset = _transaction_paging(response, offset, limit, len(transactions))
        log.info("Transactions retrieved", count=len(transactions))
        # model_validate (not the constructor) sidesteps mypy's list invariance:
        # list[dict[str, Any]] is not assignable to the model's list[JsonValue].
        return TransactionsResult.model_validate(
            {
                "transactions": transactions,
                "count": len(transactions),
                "total_count": total_count,
                "next_offset": next_offset,
                "verbose": verbose,
            }
        )
    except Exception as e:
        log.error("Failed to fetch transactions", error=str(e), limit=limit, start_date=start_date)
        raise


@mcp.tool(annotations=READONLY, title="Search Transactions")
@track_usage
async def search_transactions(
    query: str,
    limit: int = 500,
    offset: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    tag_ids: str | None = None,
    has_attachments: bool | None = None,
    has_notes: bool | None = None,
    hidden_from_reports: bool | None = None,
    is_split: bool | None = None,
    is_recurring: bool | None = None,
    verbose: bool = False,
    is_pending: bool | None = None,
    needs_review: bool | None = None,
) -> SearchResult:
    """Search one page of transactions using Monarch's text search.

    Args:
        query: Nonempty search text for Monarch's transaction search.
        limit: Maximum transactions to request (default: 500).
        offset: Transactions to skip for pagination.
        start_date: Inclusive start, accepting ISO dates or natural language.
            Without end_date, the end defaults to today.
        end_date: Inclusive end. Without start_date, the start defaults to the
            first day of the end date's month.
        account_id: Account ID from get_accounts.
        category_id: Category ID from get_transaction_categories.
        tag_ids: Comma-separated tag IDs.
        has_attachments: Filter by attachment presence.
        has_notes: Filter by notes presence.
        hidden_from_reports: True for hidden transactions, False for visible ones.
        is_split: Filter by split status.
        is_recurring: Filter by recurring status.
        is_pending: True for pending transactions, False for posted transactions.
        needs_review: True for transactions flagged for review, False for reviewed ones.
        verbose: False uses get_transactions' compact fields; True keeps full API objects.

    Boolean filters default to None (no restriction). Returns matching transactions,
    search_metadata with the query, this page's result_count, and applied filters,
    plus total_count (all matches; null if Monarch omits it) and next_offset (null
    when no more pages). Pass next_offset as offset for the next page.
    """
    await ensure_authenticated()

    if not query or not query.strip():
        raise ValueError("Query parameter cannot be empty")

    try:
        query_str = query.strip()
        filters = _build_transaction_filters(
            start_date,
            end_date,
            account_id,
            category_id,
            tag_ids,
            has_attachments,
            has_notes,
            hidden_from_reports,
            is_split,
            is_recurring,
            is_pending,
            needs_review,
        )
        filters["search"] = query_str

        response = await api_call_with_retry("get_transactions", limit=limit, offset=offset, **filters)
        transactions = extract_transactions_list(response)
        transactions = convert_dates_to_strings(transactions)

        if not verbose:
            transactions = format_transactions_compact(transactions)

        metadata = SearchMetadata(
            query=query_str,
            result_count=len(transactions),
            filters_applied={k: v for k, v in filters.items() if k != "search"},
        )

        total_count, next_offset = _transaction_paging(response, offset, limit, len(transactions))
        log.info("Search complete", query=query_str, result_count=len(transactions))
        # model_validate (not the constructor) sidesteps mypy's list invariance.
        return SearchResult.model_validate(
            {
                "search_metadata": metadata,
                "transactions": transactions,
                "total_count": total_count,
                "next_offset": next_offset,
            }
        )

    except Exception as e:
        log.error("Failed to search transactions", error=str(e), query=query)
        raise


@mcp.tool(annotations=READONLY, title="Get Budgets")
@track_usage
async def get_budgets(start_date: str | None = None, end_date: str | None = None) -> BudgetsResult:
    """Retrieve budgets for optional start_date/end_date filters.

    Dates accept ISO strings or natural language such as 'last month'. A missing
    end defaults to today; a missing start to the first day of the end date's month.
    """
    await ensure_authenticated()

    kwargs = build_date_filter(start_date, end_date)

    try:
        budgets = await api_call_with_retry("get_budgets", **kwargs)  # type: ignore[arg-type]
        budgets = convert_dates_to_strings(budgets)
        return BudgetsResult(budgets=budgets)
    except Exception as e:
        # Handle the case where no budgets exist
        if "Something went wrong while processing: None" in str(e):
            return BudgetsResult(budgets=[], message="No budgets configured in your Monarch Money account")
        else:
            raise


@mcp.tool(annotations=READONLY, title="Get Cashflow")
@track_usage
async def get_cashflow(start_date: str | None = None, end_date: str | None = None) -> CashflowResult:
    """Retrieve cash flow for optional start_date/end_date filters.

    Dates accept ISO strings or natural language such as 'last month'. A missing
    end defaults to today; a missing start to the first day of the end date's month.
    """
    await ensure_authenticated()

    kwargs = build_date_filter(start_date, end_date)

    cashflow = await api_call_with_retry("get_cashflow", **kwargs)  # type: ignore[arg-type]
    cashflow = convert_dates_to_strings(cashflow)
    return CashflowResult(cashflow=cashflow)


@mcp.tool(annotations=READONLY, title="Get Transaction Categories")
@track_usage
async def get_transaction_categories(verbose: bool = False) -> CategoriesResult:
    """List category IDs and names for lookups and transaction updates.

    verbose=True preserves full API details, including groups and system flags.
    Returns categories, count, and verbose.
    """
    await ensure_authenticated()

    categories = await api_call_with_retry("get_transaction_categories")
    categories = convert_dates_to_strings(categories)
    category_list = extract_list(categories, "categories")

    if not verbose:
        category_list = [
            {"id": cat.get("id"), "name": cat.get("name")} for cat in category_list if isinstance(cat, dict)
        ]

    return CategoriesResult(categories=category_list, count=len(category_list), verbose=verbose)


@mcp.tool(annotations=WRITE_CREATE, title="Create Transaction")
@track_usage
async def create_transaction(
    amount: float,
    merchant_name: str,
    account_id: str,
    date: str,
    category_id: str,
    notes: str | None = None,
    update_balance: bool = False,
) -> TransactionResult:
    """Create a manual transaction.

    Args:
        amount: Positive for income, negative for an expense.
        merchant_name: Merchant/payee display name.
        account_id: Account ID from get_accounts.
        date: Transaction date in YYYY-MM-DD format.
        category_id: Required category ID from get_transaction_categories.
        notes: Optional memo.
        update_balance: False records the transaction without changing the account
            balance. True also adjusts the balance, useful for manual accounts.

    Returns the created transaction details.
    """
    await ensure_authenticated()

    try:
        if not merchant_name or merchant_name.strip() == "":
            raise ValueError("merchant_name cannot be empty")
        if not category_id:
            raise ValueError("category_id is required when creating transactions")

        try:
            transaction_date = datetime.strptime(date, "%Y-%m-%d").date()
            date_str = transaction_date.isoformat()
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD (e.g., 2024-01-15). Error: {e}") from e

        log.info("creating_transaction", merchant=merchant_name, amount=amount, date=date_str)

        result = await asyncio.wait_for(
            api_call_with_retry(
                "create_transaction",
                amount=amount,
                merchant_name=merchant_name,
                category_id=category_id,
                account_id=account_id,
                date=date_str,
                notes=notes or "",
                update_balance=update_balance,
            ),
            timeout=WRITE_TIMEOUT_SECONDS,
        )
        result = convert_dates_to_strings(result)
        return TransactionResult(transaction=result)
    except asyncio.TimeoutError as e:
        log.error("create_transaction_timeout")
        raise ValueError(
            "Transaction creation timed out, so its outcome is unknown: Monarch may have created it. "
            "Check get_transactions before retrying to avoid a duplicate."
        ) from e
    except ValueError:
        raise
    except Exception as e:
        log.error("create_transaction_failed", error=str(e))
        raise


@mcp.tool(annotations=WRITE_IDEMPOTENT, title="Update Transaction")
@track_usage
async def update_transaction(
    transaction_id: str,
    amount: float | None = None,
    merchant_name: str | None = None,
    category_id: str | None = None,
    date: str | None = None,
    notes: str | None = None,
    goal_id: str | None = None,
    hide_from_reports: bool | None = None,
    needs_review: bool | None = None,
    owner_user_id: str | None = None,
) -> TransactionResult:
    """Update a transaction, leaving omitted fields unchanged.

    Args:
        transaction_id: Transaction ID from get_transactions or search_transactions.
        amount: New nonzero amount. Monarch's client cannot set an amount of 0.
        merchant_name: New display name, not the read-only plaidName statement text.
            The API ignores empty names.
        category_id: New category ID from get_transaction_categories.
        date: New date in YYYY-MM-DD format.
        notes: Memo, separate from the merchant name; "" clears it.
        goal_id: Savings goal ID; "" clears the association.
        hide_from_reports: Whether to hide the transaction from reports.
        needs_review: Whether to flag the transaction for review.
        owner_user_id: Member ID from get_household_members; "" makes it Shared.
            Omitted/null leaves ownership unchanged. Read verbose transactions to
            verify ownership; the update response does not include the owner.

    Returns updated transaction details. This tool cannot change the transaction
    ID, account, pending status, attachments, or timestamps. Use
    update_transaction_splits to change splits and update_recurring_transaction
    to change a merchant's recurring schedule.
    """
    parsed_date = datetime.strptime(date, "%Y-%m-%d").date() if date is not None else None
    _require_nonzero_amount(amount)
    await ensure_authenticated()

    try:
        if merchant_name is not None and merchant_name.strip() == "":
            log.warning("empty_merchant_name_ignored")

        updates: dict[str, Any] = {"transaction_id": transaction_id}
        if amount is not None:
            updates["amount"] = amount
        if merchant_name is not None:
            updates["merchant_name"] = merchant_name
        if category_id is not None:
            updates["category_id"] = category_id
        if parsed_date is not None:
            updates["date"] = parsed_date.isoformat()
        if notes is not None:
            updates["notes"] = notes
        if goal_id is not None:
            updates["goal_id"] = goal_id
        if hide_from_reports is not None:
            updates["hide_from_reports"] = hide_from_reports
        if needs_review is not None:
            updates["needs_review"] = needs_review
        if owner_user_id is not None:
            updates["owner_user_id"] = owner_user_id

        update_fields = [k for k in updates if k != "transaction_id"]
        log.info("updating_transaction", transaction_id=transaction_id, fields=update_fields)

        result = await asyncio.wait_for(
            api_call_with_retry("update_transaction", **updates),
            timeout=WRITE_TIMEOUT_SECONDS,
        )
        _check_transaction_update(result)
        result = convert_dates_to_strings(result)
        return TransactionResult(transaction=result)
    except asyncio.TimeoutError as e:
        log.error("update_transaction_timeout", transaction_id=transaction_id)
        raise ValueError("Transaction update timed out. Please try again.") from e
    except Exception as e:
        log.error("update_transaction_failed", transaction_id=transaction_id, error=str(e))
        raise


@mcp.tool(annotations=WRITE_IDEMPOTENT, title="Bulk Update Transactions")
@track_usage
async def update_transactions_bulk(updates: str) -> BulkUpdateResult:
    """Update transactions concurrently, returning per-item results and counts.

    Args:
        updates: JSON array encoded as a string. Each item requires a nonempty
            transaction_id and accepts amount, merchant_name, category_id, date
            (YYYY-MM-DD), notes, goal_id, hide_from_reports, needs_review, and
            owner_user_id. Fields have the same meaning as update_transaction;
            omitted/null fields stay unchanged. Empty notes or goal_id clears that
            value; empty owner_user_id makes the transaction Shared.

    Items reject unknown fields and wrong types without coercion; amounts must
    be finite nonzero numbers and flags must be booleans. Invalid items fail individually;
    valid items still run. The batch is not atomic and does not roll back successes.

    Example:
        [{"transaction_id": "txn_123", "category_id": "cat_456", "notes": ""},
         {"transaction_id": "txn_789", "needs_review": false}]
    """
    await ensure_authenticated()

    try:
        try:
            updates_list = json.loads(updates)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in updates parameter: {e}") from e

        if not isinstance(updates_list, list):
            raise ValueError("updates parameter must be a JSON array of transaction updates")

        if len(updates_list) == 0:
            return BulkUpdateResult(
                summary=BulkSummary(total=0, succeeded=0, failed=0), results=[], message="No updates provided"
            )

        log.info("bulk_update_start", count=len(updates_list))

        async def update_single(update_data: object) -> BulkItemResult:
            """Validate and update one item, returning errors without aborting the batch."""
            txn_id: str | None = None
            if isinstance(update_data, dict) and isinstance(update_data.get("transaction_id"), str):
                txn_id = update_data["transaction_id"]
            try:
                update = BulkTransactionUpdate.model_validate(update_data)
                update_params = update.model_dump(exclude_none=True)
                _require_nonzero_amount(update.amount)
                if update.date is not None:
                    update_params["date"] = datetime.strptime(update.date, "%Y-%m-%d").date().isoformat()

                result = await asyncio.wait_for(
                    api_call_with_retry("update_transaction", **update_params), timeout=WRITE_TIMEOUT_SECONDS
                )
                _check_transaction_update(result)

                return BulkItemResult(transaction_id=txn_id, status="success")

            except asyncio.TimeoutError:
                return BulkItemResult(
                    transaction_id=txn_id,
                    status="error",
                    error="Update timed out",
                )
            except Exception as e:
                return BulkItemResult(transaction_id=txn_id, status="error", error=str(e))

        results = await asyncio.gather(
            *[update_single(update_data) for update_data in updates_list],
            return_exceptions=False,
        )

        success_count = sum(1 for r in results if r.status == "success")
        failure_count = len(results) - success_count

        log.info("bulk_update_complete", succeeded=success_count, failed=failure_count)

        return BulkUpdateResult(
            summary=BulkSummary(total=len(results), succeeded=success_count, failed=failure_count),
            results=list(results),
        )

    except Exception as e:
        log.error("bulk_update_failed", error=str(e))
        raise


@mcp.tool(annotations=READONLY, title="Get Transaction Splits")
@track_usage
async def get_transaction_splits(transaction_id: str) -> TransactionSplitsResult:
    """Get split legs for transaction_id from get_transactions or search_transactions.

    Returns transaction_id, has_split_transactions, and splits with each leg's
    amount, category, merchant, and notes. An unsplit transaction has an empty list.
    """
    await ensure_authenticated()
    result = await api_call_with_retry("get_transaction_splits", transaction_id=transaction_id)
    transaction = TransactionSplitsPayload.model_validate(_require_transaction(convert_dates_to_strings(result)))
    return TransactionSplitsResult(
        transaction_id=transaction.id,
        has_split_transactions=bool(transaction.splitTransactions),
        splits=transaction.splitTransactions,
    )


@mcp.tool(annotations=READONLY, title="Get Transaction Details")
@track_usage
async def get_transaction_details(transaction_id: str) -> TransactionDetailsResult:
    """Get one transaction's split linkage, amount, merchant, and category_id.

    Compact get_transactions/search_transactions results omit split flags.
    Verbose results identify split legs with isSplitTransaction but do not
    expose their parent IDs. This tool returns a leg's parent_transaction_id
    or a parent's has_split_transactions flag and split_transaction_ids. Use
    these IDs to fetch details for individual legs; an unsplit transaction
    or a split leg returns an empty list.

    Use this before get_transaction_splits or update_transaction_splits when
    you need the parent ID. transaction_id is the resolved ID returned by
    Monarch; it may differ from the requested ID when a pending transaction
    redirects to its posted replacement. Missing transactions raise an error.
    """
    await ensure_authenticated()
    result = await api_call_with_retry("get_transaction_details", transaction_id=transaction_id)
    txn = TransactionDetailsPayload.model_validate(_require_transaction(convert_dates_to_strings(result)))
    return TransactionDetailsResult(
        transaction_id=txn.id,
        amount=txn.amount,
        merchant=txn.merchant.name if txn.merchant else None,
        category_id=txn.category.id if txn.category else None,
        has_split_transactions=txn.hasSplitTransactions,
        is_split_transaction=txn.isSplitTransaction,
        parent_transaction_id=txn.originalTransaction.id if txn.originalTransaction else None,
        split_transaction_ids=[split.id for split in txn.splitTransactions],
    )


@mcp.tool(annotations=WRITE_IDEMPOTENT, title="Update Transaction Splits")
@track_usage
async def update_transaction_splits(transaction_id: str, splits: list[TransactionSplit]) -> UpdateSplitsResult:
    """Replace a transaction's entire set of splits, or remove all splits with [].

    Args:
        transaction_id: Parent transaction ID. For a split leg from list/search,
            resolve parent_transaction_id with get_transaction_details first.
        splits: Complete replacement list. Each leg accepts:
            - amount (required): Negative for expenses, positive for income.
              Amounts must sum to the parent's amount or Monarch rejects the update.
            - category_id: Category ID for the leg.
            - merchant_name: Display name; defaults to the parent merchant.
            - notes: Per-leg memo.

    Example for a -100.00 parent transaction:
        [{"amount": -70.00, "category_id": "cat_groceries", "notes": "Food"},
         {"amount": -30.00, "category_id": "cat_household"}]

    Returns transaction_id, has_split_transactions, resulting splits, and a summary.
    """
    await ensure_authenticated()

    try:
        split_data: list[dict[str, Any]] = []
        for split in splits:
            entry: dict[str, Any] = {"amount": split.amount}
            # An empty merchantName inherits the parent merchant.
            entry["merchantName"] = split.merchant_name if split.merchant_name is not None else ""
            if split.category_id is not None:
                entry["categoryId"] = split.category_id
            if split.notes is not None:
                entry["notes"] = split.notes
            split_data.append(entry)

        log.info("updating_transaction_splits", transaction_id=transaction_id, split_count=len(split_data))

        result = await asyncio.wait_for(
            api_call_with_retry("update_transaction_splits", transaction_id=transaction_id, split_data=split_data),
            timeout=WRITE_TIMEOUT_SECONDS,
        )
        result = convert_dates_to_strings(result)

        payload = result.get("updateTransactionSplit") or {} if isinstance(result, dict) else {}
        errors = payload.get("errors")
        if errors:
            raise ValueError(f"Monarch rejected the split update: {errors}")

        transaction = payload.get("transaction")
        if not isinstance(transaction, dict):
            raise ValueError("Monarch returned no updated transaction")
        result_splits = transaction.get("splitTransactions") or []
        message = (
            f"Removed all splits from transaction {transaction_id}"
            if not split_data
            else f"Set {len(result_splits)} split(s) on transaction {transaction_id}"
        )
        return UpdateSplitsResult(
            transaction_id=transaction_id,
            has_split_transactions=bool(transaction.get("hasSplitTransactions")),
            splits=result_splits,
            message=message,
        )
    except asyncio.TimeoutError as e:
        log.error("update_transaction_splits_timeout", transaction_id=transaction_id)
        raise ValueError("Split update timed out. Please try again.") from e
    except Exception as e:
        log.error("update_transaction_splits_failed", transaction_id=transaction_id, error=str(e))
        raise


@mcp.tool(annotations=READONLY, title="Get All Brokerage Holdings")
@track_usage
async def get_all_holdings() -> BrokerageHoldingsResult:
    """Get holdings grouped by brokerage account, not all asset classes.

    Fetches accounts, then holdings for each brokerage account concurrently.
    A failed account request fails the entire call; this is not a single API request.
    """
    await ensure_authenticated()
    return BrokerageHoldingsResult.model_validate(await api_call_with_retry("get_all_holdings"))


@mcp.tool(annotations=READONLY, title="Get Transaction Rules")
@track_usage
async def get_transaction_rules(limit: int = 25, offset: int = 0, verbose: bool = False) -> TransactionRulesResult:
    """Read a page of automation rules in priority order, without modifying them.

    limit: 1–100 (default 25); offset: zero-based position. Follow next_offset
    until null; count is this page and total_count is all rules. Paging is local.
    Compact results omit null/empty object fields and GraphQL type labels, but
    preserve false, zero, and active criteria/actions. verbose=True returns the
    full upstream objects for the requested page.
    """
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1–100 and offset must be nonnegative")
    await ensure_authenticated()
    response = await api_call_with_retry("get_transaction_rules")
    rules = TypeAdapter(list[JsonValue]).validate_python(extract_list(response, "transactionRules"))
    page = rules[offset : offset + limit]
    return TransactionRulesResult(
        rules=page if verbose else [_compact_payload(rule) for rule in page],
        count=len(page),
        total_count=len(rules),
        offset=offset,
        next_offset=offset + len(page) if offset + len(page) < len(rules) else None,
        verbose=verbose,
    )


_RULE_ERROR_FIELDS = "errors { message code fieldErrors { field messages } }"
CREATE_RULE_MUTATION = gql(
    "mutation Common_CreateTransactionRuleMutationV2($input: CreateTransactionRuleInput!) {"
    f" createTransactionRuleV2(input: $input) {{ transactionRule {{ id }} {_RULE_ERROR_FIELDS} }} }}"
)
UPDATE_RULE_MUTATION = gql(
    "mutation Common_UpdateTransactionRuleMutationV2($input: UpdateTransactionRuleInput!) {"
    f" updateTransactionRuleV2(input: $input) {{ transactionRule {{ id }} {_RULE_ERROR_FIELDS} }} }}"
)
DELETE_RULE_MUTATION = gql(
    "mutation Common_DeleteTransactionRule($id: ID!) {"
    f" deleteTransactionRule(id: $id) {{ deleted {_RULE_ERROR_FIELDS} }} }}"
)


async def _find_rule(rule_id: str) -> dict[str, JsonValue] | None:
    response = await api_call_with_retry("get_transaction_rules")
    rules = TypeAdapter(list[JsonValue]).validate_python(extract_list(response, "transactionRules"))
    return next((rule for rule in rules if isinstance(rule, dict) and rule.get("id") == rule_id), None)


async def _run_rule_mutation(
    action: str, operation: str, query: GraphQLRequest, variables: dict[str, JsonValue], key: str
) -> _RuleMutationPayload:
    result = await api_call_with_retry("gql_call", operation=operation, graphql_query=query, variables=variables)
    payload = result.get(key) if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        raise ValueError(f"Monarch returned an invalid rule {action} response")
    mutation = _RuleMutationPayload.model_validate(payload)
    mutation.check(action)
    return mutation


def _set_rule_fields(
    rule: dict[str, JsonValue],
    *,
    merchant_criteria: list[RuleTextCriterion] | None,
    original_statement_criteria: list[RuleTextCriterion] | None,
    amount_criteria: RuleAmountCriterion | None,
    account_ids: list[str] | None,
    category_ids: list[str] | None,
    set_category_id: str | None,
    set_merchant_name: str | None,
    add_tag_ids: list[str] | None,
    hide_from_reports: bool | None,
    review_status: Literal["needs_review", "reviewed", ""] | None,
) -> None:
    """Apply the requested changes. None keeps a field. An empty value clears it."""
    if merchant_criteria is not None:
        rule["merchantNameCriteria"] = [c.model_dump() for c in merchant_criteria]
        # Clear the older merchant field too. Monarch keeps it if we leave it out.
        if "merchantCriteria" in rule:
            rule["merchantCriteria"] = []
    if original_statement_criteria is not None:
        rule["originalStatementCriteria"] = [c.model_dump() for c in original_statement_criteria]
    if amount_criteria is not None:
        rule["amountCriteria"] = amount_criteria.to_input()
    if account_ids is not None:
        rule["accountIds"] = list(account_ids)
    if category_ids is not None:
        rule["categoryIds"] = list(category_ids)
    # Monarch deletes actions left out of the input, so removing a key clears that action.
    for key, text in (("setCategoryAction", set_category_id), ("setMerchantAction", set_merchant_name)):
        if text is not None:
            if text.strip():
                rule[key] = text.strip()
            else:
                rule.pop(key, None)
    if add_tag_ids is not None:
        if add_tag_ids:
            rule["addTagsAction"] = list(add_tag_ids)
        else:
            rule.pop("addTagsAction", None)
    if hide_from_reports is not None:
        rule["setHideFromReportsAction"] = hide_from_reports
    if review_status is not None:
        if review_status:
            rule["reviewStatusAction"] = review_status
        else:
            rule.pop("reviewStatusAction", None)
            rule.pop("needsReviewByUserAction", None)


def _require_criteria_and_action(rule: dict[str, JsonValue]) -> None:
    if not any(rule.get(key) for key in RULE_MATCH_KEYS):
        raise ValueError("A rule needs a merchant, statement, or amount criterion")
    if not any(rule.get(key) for key in RULE_ACTION_KEYS):
        raise ValueError("A rule needs at least one action: category, merchant name, tags, hide, or review status")


@mcp.tool(annotations=WRITE_CREATE_DESTRUCTIVE, title="Create Transaction Rule")
@track_usage
async def create_transaction_rule(
    merchant_criteria: list[RuleTextCriterion] | None = None,
    original_statement_criteria: list[RuleTextCriterion] | None = None,
    amount_criteria: RuleAmountCriterion | None = None,
    account_ids: list[str] | None = None,
    category_ids: list[str] | None = None,
    set_category_id: str | None = None,
    set_merchant_name: str | None = None,
    add_tag_ids: list[str] | None = None,
    hide_from_reports: bool | None = None,
    review_status: Literal["needs_review", "reviewed"] | None = None,
    apply_to_existing_transactions: bool = False,
) -> TransactionRuleResult:
    """Create an automation rule.

    A rule needs at least one action and a merchant, statement, or amount
    criterion. Without one of those three, Monarch ignores later edits to the rule.

    Criteria (a transaction must match all of them; values in one list are "or"):
        merchant_criteria: Merchant name text, e.g. [{"operator": "contains", "value": "Corner Deli"}].
        original_statement_criteria: Bank statement text, same shape.
        amount_criteria: {"operator": "gt"|"lt"|"eq", "value": 20} or
            {"operator": "between", "lower": 10, "upper": 50}. Amounts have no sign.
            is_expense=True (the default) matches money going out; False matches money coming in.
        account_ids / category_ids: Only match these accounts or current categories.

    Actions:
        set_category_id: Category ID from get_transaction_categories.
        set_merchant_name: New merchant name (a name, not an ID).
        add_tag_ids: Tag IDs to add.
        hide_from_reports: Hide matching transactions from reports.
        review_status: "needs_review" or "reviewed".

    apply_to_existing_transactions=True also changes matching past transactions
    right away. That is hard to undo, so check the criteria with
    search_transactions first. This tool does not change rule priority.
    """
    rule: dict[str, JsonValue] = {"applyToExistingTransactions": apply_to_existing_transactions}
    _set_rule_fields(
        rule,
        merchant_criteria=merchant_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        account_ids=account_ids,
        category_ids=category_ids,
        set_category_id=set_category_id,
        set_merchant_name=set_merchant_name,
        add_tag_ids=add_tag_ids,
        hide_from_reports=hide_from_reports,
        review_status=review_status,
    )
    _require_criteria_and_action(rule)
    await ensure_authenticated()
    payload = await _run_rule_mutation(
        "creation",
        "Common_CreateTransactionRuleMutationV2",
        CREATE_RULE_MUTATION,
        {"input": rule},
        "createTransactionRuleV2",
    )
    if payload.transactionRule is None:
        raise ValueError("Monarch returned no created rule")
    rule_id = payload.transactionRule.id
    log.info("transaction_rule_created", rule_id=rule_id, applied_to_existing=apply_to_existing_transactions)
    created = await _find_rule(rule_id)
    return TransactionRuleResult(
        rule=_compact_payload(created) if created is not None else {"id": rule_id},
        applied_to_existing_transactions=apply_to_existing_transactions,
    )


@mcp.tool(annotations=WRITE_DESTRUCTIVE, title="Update Transaction Rule")
@track_usage
async def update_transaction_rule(
    rule_id: str,
    merchant_criteria: list[RuleTextCriterion] | None = None,
    original_statement_criteria: list[RuleTextCriterion] | None = None,
    amount_criteria: RuleAmountCriterion | None = None,
    clear_amount_criteria: bool = False,
    account_ids: list[str] | None = None,
    category_ids: list[str] | None = None,
    set_category_id: str | None = None,
    set_merchant_name: str | None = None,
    add_tag_ids: list[str] | None = None,
    hide_from_reports: bool | None = None,
    review_status: Literal["needs_review", "reviewed", ""] | None = None,
    apply_to_existing_transactions: bool = False,
) -> TransactionRuleResult:
    """Edit a rule, and optionally run it on existing transactions.

    Get rule_id from get_transaction_rules. Arguments you leave out keep their
    current value. To clear a value, pass [] for a list or "" for
    set_category_id, set_merchant_name, or review_status. clear_amount_criteria
    removes the amount criterion. A list replaces the whole old list. Shapes
    match create_transaction_rule.

    Monarch replaces the whole rule on update. So this reads the rule first and
    sends it back with your changes, which keeps settings this tool can't edit,
    such as owners, goals, and splits. The edited rule must keep at least one
    action and a merchant, statement, or amount criterion.

    apply_to_existing_transactions=True runs the rule on matching past
    transactions, even with no other changes. That is hard to undo.
    """
    if amount_criteria is not None and clear_amount_criteria:
        raise ValueError("Pass amount_criteria or clear_amount_criteria, not both")
    changes = (
        merchant_criteria,
        original_statement_criteria,
        amount_criteria,
        account_ids,
        category_ids,
        set_category_id,
        set_merchant_name,
        add_tag_ids,
        hide_from_reports,
        review_status,
    )
    if all(value is None for value in changes) and not clear_amount_criteria and not apply_to_existing_transactions:
        raise ValueError("Provide at least one change, or apply_to_existing_transactions=True")
    await ensure_authenticated()
    existing = await _find_rule(rule_id)
    if existing is None:
        raise ValueError(f"No transaction rule found with ID {rule_id}")
    rule = ExistingRule.model_validate(existing).to_input()
    rule["applyToExistingTransactions"] = apply_to_existing_transactions
    if clear_amount_criteria:
        rule["amountCriteria"] = None
    _set_rule_fields(
        rule,
        merchant_criteria=merchant_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        account_ids=account_ids,
        category_ids=category_ids,
        set_category_id=set_category_id,
        set_merchant_name=set_merchant_name,
        add_tag_ids=add_tag_ids,
        hide_from_reports=hide_from_reports,
        review_status=review_status,
    )
    _require_criteria_and_action(rule)
    await _run_rule_mutation(
        "update",
        "Common_UpdateTransactionRuleMutationV2",
        UPDATE_RULE_MUTATION,
        {"input": rule},
        "updateTransactionRuleV2",
    )
    log.info("transaction_rule_updated", rule_id=rule_id, applied_to_existing=apply_to_existing_transactions)
    updated = await _find_rule(rule_id)
    if updated is None:
        raise ValueError(f"Rule {rule_id} is missing after the update")
    return TransactionRuleResult(
        rule=_compact_payload(updated), applied_to_existing_transactions=apply_to_existing_transactions
    )


@mcp.tool(annotations=WRITE_DESTRUCTIVE, title="Delete Transaction Rule")
@track_usage
async def delete_transaction_rule(rule_id: str) -> DeleteTransactionRuleResult:
    """Delete an automation rule by ID from get_transaction_rules.

    Transactions the rule already changed keep their current values.
    Deletion is confirmed by reading the rule list again.
    """
    if not rule_id.strip():
        raise ValueError("rule_id must not be blank")
    await ensure_authenticated()
    # Monarch returns deleted=false even on success, so check errors and read the list again.
    await _run_rule_mutation(
        "deletion", "Common_DeleteTransactionRule", DELETE_RULE_MUTATION, {"id": rule_id}, "deleteTransactionRule"
    )
    if await _find_rule(rule_id) is not None:
        raise ValueError(f"Rule {rule_id} still exists after deletion")
    log.info("transaction_rule_deleted", rule_id=rule_id)
    return DeleteTransactionRuleResult(rule_id=rule_id, deleted=True)


@mcp.tool(annotations=READONLY, title="Get Household Members")
@track_usage
async def get_household_members() -> HouseholdMembersResult:
    """List current household members and IDs for transaction ownership.

    Pending invitations are excluded. Use an ID as owner_user_id in transaction
    updates, or "" for Shared; a missing/null owner_user_id leaves it unchanged.
    """
    await ensure_authenticated()
    response = await api_call_with_retry("get_household_members")
    household = response.get("myHousehold") if isinstance(response, dict) else None
    members = extract_list(household, "users")
    return HouseholdMembersResult(members=members, count=len(members))


@mcp.tool(annotations=READONLY, title="Get Account Holdings")
@track_usage
async def get_account_holdings(account_id: str) -> HoldingsResult:
    """Get investment holdings for an account_id from get_accounts."""
    await ensure_authenticated()

    try:
        holdings = await api_call_with_retry("get_account_holdings", account_id=account_id)
        holdings = convert_dates_to_strings(holdings)
        return HoldingsResult(holdings=holdings)
    except Exception as e:
        log.error("Failed to fetch account holdings", error=str(e), account_id=account_id)
        raise


@mcp.tool(annotations=READONLY, title="Get Account History")
@track_usage
async def get_account_history(
    account_id: str, start_date: str | None = None, end_date: str | None = None, limit: int = 100, offset: int = 0
) -> AccountHistoryResult:
    """Get a page of balance history for account_id from get_accounts.

    Optional YYYY-MM-DD bounds are inclusive; an omitted bound is unbounded.
    Filtering precedes local paging, preserving Monarch's snapshot order.
    limit: 1–1000 (default 100); offset: zero-based position after filtering.
    Follow next_offset until null. total_count counts all matching snapshots.
    Monarch still returns the full history upstream; the MCP response is bounded.
    """
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("limit must be 1–1000 and offset must be nonnegative")
    start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date is not None else None
    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date is not None else None
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    await ensure_authenticated()

    try:
        history = await api_call_with_retry("get_account_history", account_id=account_id)
        if start is not None or end is not None:
            snapshots = TypeAdapter(list[AccountHistorySnapshot]).validate_python(history)
            history = [
                snapshot.model_dump(mode="json")
                for snapshot in snapshots
                if (start is None or snapshot.date >= start) and (end is None or snapshot.date <= end)
            ]
        matching = TypeAdapter(list[JsonValue]).validate_python(convert_dates_to_strings(history))
        page = matching[offset : offset + limit]
        return AccountHistoryResult(
            account_id=account_id,
            history=page,
            count=len(page),
            total_count=len(matching),
            offset=offset,
            next_offset=offset + len(page) if offset + len(page) < len(matching) else None,
        )
    except Exception as e:
        log.error("Failed to fetch account history", error=str(e), account_id=account_id)
        raise


@mcp.tool(annotations=READONLY, title="Get Institutions")
@track_usage
async def get_institutions() -> InstitutionsResult:
    """Get linked financial institutions."""
    await ensure_authenticated()

    try:
        institutions = await api_call_with_retry("get_institutions")
        institutions = convert_dates_to_strings(institutions)
        return InstitutionsResult(institutions=institutions)
    except Exception as e:
        log.error("Failed to fetch institutions", error=str(e))
        raise


@mcp.tool(annotations=READONLY, title="Get Recurring Transactions")
@track_usage
async def get_recurring_transactions(start_date: str | None = None, end_date: str | None = None) -> RecurringResult:
    """Get scheduled recurring occurrences, not the posted transaction history.

    Dates accept ISO dates or natural language such as "today" or "last month".
    With neither date, fetch the current calendar month. With only a start, end on
    that month's last day. With only an end, start on the first day of that period.

    The recurring.recurringTransactionItems list includes each occurrence's date,
    amount, account, category, transactionId (when matched), and stream containing
    the merchant ID, frequency, and expected amount. isPast describes the date,
    not whether a bill was paid. Use get_transactions(is_recurring=True) for
    recorded transactions instead.
    """
    start = parse_flexible_date(start_date) if start_date is not None else None
    end = parse_flexible_end_date(end_date) if end_date is not None else None
    if start is None:
        # A period end such as "2024" starts at the period's first month, not its last.
        start = (parse_date_period(end_date)[0] if end_date is not None else date.today()).replace(day=1)
    if end is None:
        end = start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
    filters = build_date_filter(start.isoformat(), end.isoformat())
    await ensure_authenticated()
    recurring = await api_call_with_retry(
        "get_recurring_transactions", start_date=filters["start_date"], end_date=filters["end_date"]
    )
    return RecurringResult(recurring=convert_dates_to_strings(recurring))


@mcp.tool(annotations=WRITE_IDEMPOTENT, title="Update Recurring Transaction")
@track_usage
async def update_recurring_transaction(
    merchant_id: str,
    merchant_name: str,
    is_recurring: bool | None = None,
    frequency: str | None = None,
    base_date: str | None = None,
    amount: FiniteFloat | None = None,
    is_active: bool | None = None,
) -> UpdateRecurringResult:
    """Change a merchant's recurring schedule, not an individual transaction.

    This affects the merchant-wide recurrence. Get merchant_id from an
    occurrence's stream.merchant.id, not stream.id or transactionId.
    Pass the current merchant_name to avoid renaming the merchant.

    Omitted settings stay unchanged; provide at least one. is_recurring enables or
    removes recurrence; is_active pauses or resumes a schedule.
    frequency is Monarch's string (for example, "monthly"). base_date is the schedule's anchor date and
    accepts the same date formats as get_recurring_transactions.
    amount uses Monarch's signed amount, as returned by the existing stream.
    This does not create posted transactions or move money.
    """
    if not merchant_id.strip() or not merchant_name.strip():
        raise ValueError("merchant_id and merchant_name must not be blank")
    if all(value is None for value in (is_recurring, frequency, base_date, amount, is_active)):
        raise ValueError("Provide at least one recurring setting to change")
    if frequency is not None and not frequency.strip():
        raise ValueError("frequency must not be blank")
    normalized_date = parse_flexible_date(base_date).isoformat() if base_date is not None else None
    await ensure_authenticated()
    result = await api_call_with_retry(
        "update_reoccuring",
        merchant_id=merchant_id,
        name=merchant_name,
        is_recurring=is_recurring,
        frequency=frequency,
        base_date=normalized_date,
        amount=amount,
        is_active=is_active,
    )
    payload = result.get("updateMerchant") if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("Monarch returned an invalid recurring update response")
    if payload.get("errors"):
        raise ValueError(f"Monarch rejected the recurring update: {payload['errors']}")
    return UpdateRecurringResult(merchant=RecurringMerchant.model_validate(payload.get("merchant")))


@mcp.tool(annotations=WRITE_IDEMPOTENT, title="Set Budget Amount")
@track_usage
async def set_budget_amount(
    category_id: str, amount: float, month: str | None = None, apply_to_future: bool = False
) -> SetBudgetResult:
    """Set a category's monthly budget amount.

    Args:
        category_id: Category ID from get_transaction_categories.
        amount: Budget amount; 0 clears the budget for that month.
        month: Any date in the target month; ISO or natural language. Defaults to the current month.
        apply_to_future: True also applies the amount to later months.
    """
    month_start = _first_of_month(month)
    await ensure_authenticated()

    try:
        result = await api_call_with_retry(
            "set_budget_amount",
            category_id=category_id,
            amount=amount,
            start_date=month_start,
            apply_to_future=apply_to_future,
        )
        result = convert_dates_to_strings(result)
        payload = result.get("updateOrCreateBudgetItem") if isinstance(result, dict) else None
        if not isinstance(payload, dict) or payload.get("budgetItem") is None:
            raise ValueError("Monarch did not confirm the budget update")
        log.info("Budget amount updated", category_id=category_id, amount=amount, month=month_start)
        return SetBudgetResult(
            category_id=category_id, amount=amount, month=month_start, apply_to_future=apply_to_future, result=result
        )
    except Exception as e:
        log.error("Failed to set budget amount", error=str(e), category_id=category_id)
        raise


@mcp.tool(annotations=WRITE_CREATE, title="Create Manual Account")
@track_usage
async def create_manual_account(
    account_name: str,
    account_type: str,
    account_sub_type: str,
    balance: float,
    include_in_net_worth: bool = True,
) -> CreateAccountResult:
    """Create a manually tracked account and return its new ID.

    Args:
        account_name: Display name for the account.
        account_type: Monarch account type name, such as loan, other_asset, or other_liability.
        account_sub_type: Subtype name within that type, such as mortgage, auto, or line_of_credit.
        balance: Starting balance.
        include_in_net_worth: Whether the account counts toward net worth.
    """
    await ensure_authenticated()

    try:
        result = await api_call_with_retry(
            "create_manual_account",
            account_type=account_type,
            account_sub_type=account_sub_type,
            is_in_net_worth=include_in_net_worth,
            account_name=account_name,
            account_balance=balance,
        )
        payload = result.get("createManualAccount") if isinstance(result, dict) else None
        account = payload.get("account") if isinstance(payload, dict) else None
        if not isinstance(account, dict) or not account.get("id"):
            errors = payload.get("errors") if isinstance(payload, dict) else None
            raise ValueError(f"Monarch rejected the manual account: {errors}")
        log.info("Manual account created", type=account_type, sub_type=account_sub_type)
        return CreateAccountResult(account=convert_dates_to_strings(account))
    except Exception as e:
        log.error("Failed to create manual account", error=str(e), type=account_type)
        raise


@mcp.tool(annotations=READONLY, title="Get Spending Summary")
@track_usage
async def get_spending_summary(
    start_date: str | None = None, end_date: str | None = None, group_by: str = "category"
) -> SpendingSummaryResult:
    """Summarize income, expenses, and net by category, account, or month.

    Args:
        start_date: Inclusive start; accepts ISO dates or natural language.
        end_date: Inclusive end; accepts the same formats. A missing end defaults
            to today; a missing start to the first day of the end date's month.
        group_by: 'category', 'account', or 'month'; other values produce one group.

    Fetches all matching pages. Expenses are positive magnitudes. Transfers
    (categories in Monarch's transfer group, such as credit card payments) are
    left out and counted in transfers_excluded. period holds the resolved ISO dates.
    """
    await ensure_authenticated()

    try:
        log.info("Generating spending summary", start_date=start_date, end_date=end_date, group_by=group_by)

        filters = build_date_filter(start_date, end_date)
        transfer_ids = _transfer_category_ids(await api_call_with_retry("get_transaction_categories"))
        page_size = 1000
        offset = 0
        transactions: list[dict[str, Any]] = []
        while True:
            response = await api_call_with_retry(
                "get_transactions",
                limit=page_size,
                offset=offset,
                **filters,  # type: ignore[arg-type]
            )
            page = extract_transactions_list(response)
            if not page:
                break
            transactions.extend(page)
            offset += len(page)
            total_count: int | None = None
            if isinstance(response, dict):
                total_count = (response.get("allTransactions") or {}).get("totalCount")
            if total_count is not None:
                if len(transactions) >= total_count:
                    break
            elif len(page) < page_size:
                # No totalCount available (e.g. a flat-list response) — a short
                # page is the only signal that this was the last page.
                break

        summary: dict[str, Any] = {
            "groups": {},
            "totals": {"income": 0, "expenses": 0, "net": 0},
        }

        transfers_excluded = 0
        for txn in transactions:
            if _is_transfer(txn, transfer_ids):
                transfers_excluded += 1
                continue
            amount = float(txn.get("amount", 0))

            totals: dict[str, float] = summary["totals"]
            if amount > 0:
                totals["income"] += amount
            else:
                totals["expenses"] += abs(amount)

            if group_by == "category":
                key = (
                    txn.get("category", {}).get("name", "Uncategorized")
                    if isinstance(txn.get("category"), dict)
                    else "Uncategorized"
                )
            elif group_by == "account":
                key = (
                    (txn["account"].get("displayName") or "Unknown")
                    if isinstance(txn.get("account"), dict)
                    else "Unknown"
                )
            elif group_by == "month":
                txn_date = txn.get("date", "")
                key = txn_date[:7] if len(txn_date) >= 7 else "Unknown"  # YYYY-MM format
            else:
                key = "All"

            groups: dict[str, dict[str, float]] = summary["groups"]
            if key not in groups:
                groups[key] = {"income": 0, "expenses": 0, "net": 0, "count": 0}

            group = groups[key]
            if amount > 0:
                group["income"] += amount
            else:
                group["expenses"] += abs(amount)

            group["net"] += amount
            group["count"] += 1

        summary["totals"]["net"] = summary["totals"]["income"] - summary["totals"]["expenses"]

        sorted_groups = dict(sorted(summary["groups"].items(), key=lambda x: x[1]["expenses"], reverse=True))

        totals_data: dict[str, float] = summary["totals"]
        result = SpendingSummaryResult(
            period=Period(start=filters.get("start_date"), end=filters.get("end_date")),
            group_by=group_by,
            groups={
                key: GroupSummary(income=g["income"], expenses=g["expenses"], net=g["net"], count=int(g["count"]))
                for key, g in sorted_groups.items()
            },
            totals=Totals(income=totals_data["income"], expenses=totals_data["expenses"], net=totals_data["net"]),
            transfers_excluded=transfers_excluded,
        )

        log.info(
            "Spending summary generated",
            total_transactions=len(transactions),
            groups_count=len(result.groups),
            net_amount=result.totals.net,
        )

        return result

    except Exception as e:
        log.error("Failed to generate spending summary", error=str(e))
        raise


@mcp.tool(annotations=WRITE_SIDE_EFFECT, title="Refresh Accounts")
@track_usage
async def refresh_accounts() -> RefreshResult:
    """Request an institution refresh for all accounts; do not wait for completion."""
    await ensure_authenticated()

    try:
        # The library requires the ids; this is the same lookup its
        # request_accounts_refresh_and_wait does, without the wait.
        accounts = extract_list(await api_call_with_retry("get_accounts"), "accounts")
        result = await api_call_with_retry("request_accounts_refresh", [a["id"] for a in accounts])
        result = convert_dates_to_strings(result)
        log.info("Account refresh requested")
        return RefreshResult(result=result)
    except Exception as e:
        log.error("Failed to refresh accounts", error=str(e))
        raise


@mcp.tool(annotations=READONLY, title="Complete Financial Overview")
@track_usage
async def get_complete_financial_overview(
    period: str = "this month", ctx: Context | None = None, verbose: bool = False
) -> FinancialOverview:
    """Fetch a compact financial overview; verbose=True includes full API details.

    period is a start date or phrase such as 'this month', 'last month', or 'this year';
    the end is always today. 'last month' therefore includes the current month too.
    Transactions and their summary use one page of up to 500 entries, not the full
    history. Failed API sections contain errors while successful sections remain.
    Default: account IDs/names/balances/types, monthly budget totals, cashflow totals,
    and a transaction summary. Raw transactions/categories are null unless verbose.
    Use their dedicated tools for details. batch_metadata.transactions_truncated
    is true if more transactions exist, false for a complete page, null if unknown.
    Summary income and expenses leave out transfers such as card payments;
    transfers_excluded counts them, or is null when categories could not be read.
    """
    await ensure_authenticated()

    try:
        if ctx is not None:
            await ctx.report_progress(0, 5, "Fetching accounts, budgets, cashflow, transactions, categories…")

        filters = build_date_filter(period, None)

        accounts_task = api_call_with_retry("get_accounts")
        budgets_task = api_call_with_retry("get_budgets", **filters)  # type: ignore[arg-type]
        cashflow_task = api_call_with_retry("get_cashflow", **filters)  # type: ignore[arg-type]
        transactions_task = api_call_with_retry("get_transactions", limit=500, **filters)  # type: ignore[arg-type]
        categories_task = api_call_with_retry("get_transaction_categories")

        api_results = await asyncio.gather(
            accounts_task, budgets_task, cashflow_task, transactions_task, categories_task, return_exceptions=True
        )
        accounts, budgets, cashflow, transactions, categories = api_results

        results: dict[str, Any] = {}

        if not isinstance(accounts, Exception):
            if verbose:
                results["accounts"] = convert_dates_to_strings(accounts)
            else:
                account_list = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
                    extract_list(accounts, "accounts")
                )
                results["accounts"] = [
                    {key: account.get(key) for key in ("id", "displayName", "currentBalance", "type")}
                    for account in account_list
                ]
        else:
            results["accounts"] = {"error": str(accounts)}

        if not isinstance(budgets, Exception):
            try:
                results["budgets"] = _budget_totals(convert_dates_to_strings(budgets), verbose)
            except ValueError as error:
                results["budgets"] = {"error": str(error)}
        else:
            results["budgets"] = {"error": str(budgets)}

        if not isinstance(cashflow, Exception):
            try:
                results["cashflow"] = _cashflow_summary(convert_dates_to_strings(cashflow), verbose)
            except ValueError as error:
                results["cashflow"] = {"error": str(error)}
        else:
            results["cashflow"] = {"error": str(cashflow)}

        transfer_ids: set[str] | None = None
        if not isinstance(categories, Exception):
            try:
                transfer_ids = _transfer_category_ids(categories)
            except ValueError as error:
                log.warning("overview_transfer_categories_unreadable", error=str(error))

        if not isinstance(transactions, Exception):
            transactions_list = extract_transactions_list(transactions)
            results["transactions"] = convert_dates_to_strings(transactions_list) if verbose else None
            amounts = [
                float(t.get("amount", 0))
                for t in transactions_list
                if transfer_ids is None or not _is_transfer(t, transfer_ids)
            ]
            results["transaction_summary"] = {
                "total_count": len(transactions_list),
                "total_income": sum(amount for amount in amounts if amount > 0),
                "total_expenses": sum(abs(amount) for amount in amounts if amount < 0),
                "transfers_excluded": len(transactions_list) - len(amounts) if transfer_ids is not None else None,
                "unique_categories": len(
                    {
                        t.get("category", {}).get("name", "Unknown")
                        for t in transactions_list
                        if isinstance(t.get("category"), dict)
                    }
                ),
                "unique_accounts": len(
                    {
                        t["account"]["id"]
                        for t in transactions_list
                        if isinstance(t.get("account"), dict) and t["account"].get("id")
                    }
                ),
            }
        else:
            results["transactions"] = {"error": str(transactions)}

        if not isinstance(categories, Exception):
            results["categories"] = convert_dates_to_strings(categories) if verbose else None
        else:
            results["categories"] = {"error": str(categories)}

        if ctx is not None:
            await ctx.report_progress(5, 5, "Assembled financial overview")

        results["batch_metadata"] = {
            "period": period,
            "filters_applied": convert_dates_to_strings(filters),
            "api_calls_made": 5,
            "timestamp": datetime.now().isoformat(),
            "transactions_truncated": (
                _transactions_truncated(transactions, len(transactions_list))
                if not isinstance(transactions, Exception)
                else None
            ),
        }
        results["period"] = period
        results["verbose"] = verbose

        accounts_val = results.get("accounts", [])
        summary_val = results.get("transaction_summary")
        log.info(
            "Complete financial overview generated",
            period=period,
            accounts_count=len(accounts_val) if isinstance(accounts_val, list) else 0,
            transactions_count=summary_val.get("total_count", 0) if isinstance(summary_val, dict) else 0,
        )

        return FinancialOverview.model_validate(results)

    except Exception as e:
        log.error("Failed to generate financial overview", error=str(e), period=period)
        raise


@mcp.tool(annotations=READONLY, title="Analyze Spending Patterns")
@track_usage
async def analyze_spending_patterns(
    lookback_months: int = 6, include_forecasting: bool = True, ctx: Context | None = None, verbose: bool = False
) -> SpendingPatterns:
    """Summarize monthly trends, category expenses, account usage, and budget data.

    Args:
        lookback_months: Months before today to include (default: 6).
        include_forecasting: Include average-based income and expense estimates.
        verbose: Include full budget details instead of monthly budget totals.

    Requests one page of up to 2000 transactions, without pagination; analysis can
    be incomplete for larger periods. Transfers such as card payments are left out
    of every section and counted in metadata.transfers_excluded (null when categories
    could not be read). Forecasts average the latest three complete calendar months
    with transactions, excluding the current month. The confidence label is fixed,
    not a statistical measure. Failed transaction, category, or budget requests
    appear in errors; successful analysis sections remain available.
    metadata.transactions_truncated reports whether more transactions exist
    (null when unknown). Fetch narrower date ranges with get_transactions for detail.
    """
    await ensure_authenticated()

    try:
        if ctx is not None:
            await ctx.report_progress(0, 2, "Fetching transactions, categories, and budgets…")

        end_date = datetime.now().date()
        start_date = end_date - relativedelta(months=lookback_months)

        transactions_task = api_call_with_retry(
            "get_transactions", limit=2000, start_date=start_date.isoformat(), end_date=end_date.isoformat()
        )
        budgets_task = api_call_with_retry(
            "get_budgets", start_date=start_date.isoformat(), end_date=end_date.isoformat()
        )
        categories_task = api_call_with_retry("get_transaction_categories")
        api_results: list[object] = await asyncio.gather(
            transactions_task, budgets_task, categories_task, return_exceptions=True
        )
        transactions, budgets, categories = api_results
        errors: dict[str, str] = {}
        if isinstance(transactions, Exception):
            errors["transactions"] = str(transactions)
        if isinstance(budgets, Exception):
            errors["budgets"] = str(budgets)

        transfer_ids: set[str] | None = None
        if isinstance(categories, Exception):
            errors["categories"] = str(categories)
        else:
            try:
                transfer_ids = _transfer_category_ids(categories)
            except ValueError as error:
                errors["categories"] = str(error)
        transfers_excluded = 0

        if ctx is not None:
            await ctx.report_progress(1, 2, "Computing trends, category and account analysis…")

        analysis: dict[str, object] = {
            "analysis_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "months_analyzed": lookback_months,
            },
            "monthly_trends": {},
            "category_analysis": {},
            "account_usage": {},
            "budget_performance": {},
        }

        if not isinstance(transactions, Exception):
            transactions_list = extract_transactions_list(transactions)

            monthly_data: dict[str, dict[str, float]] = {}
            category_totals: dict[str, dict[str, float]] = {}
            category_expense_counts: dict[str, int] = {}
            account_usage: dict[str, dict[str, float]] = {}

            for txn in transactions_list:
                if transfer_ids is not None and _is_transfer(txn, transfer_ids):
                    transfers_excluded += 1
                    continue
                txn_date = txn.get("date", "")
                amount = float(txn.get("amount", 0))
                category_name = (
                    txn.get("category", {}).get("name", "Uncategorized")
                    if isinstance(txn.get("category"), dict)
                    else "Uncategorized"
                )
                account_name = (
                    (txn["account"].get("displayName") or "Unknown")
                    if isinstance(txn.get("account"), dict)
                    else "Unknown"
                )

                month_key = txn_date[:7] if len(txn_date) >= 7 else "Unknown"
                if month_key not in monthly_data:
                    monthly_data[month_key] = {"income": 0.0, "expenses": 0.0, "net": 0.0, "transaction_count": 0.0}

                if amount > 0:
                    monthly_data[month_key]["income"] += amount
                else:
                    monthly_data[month_key]["expenses"] += abs(amount)
                monthly_data[month_key]["net"] += amount
                monthly_data[month_key]["transaction_count"] += 1

                if category_name not in category_totals:
                    category_totals[category_name] = {"total": 0.0, "transactions": 0.0, "avg_amount": 0.0}
                    category_expense_counts[category_name] = 0
                if amount < 0:
                    category_totals[category_name]["total"] += abs(amount)
                    category_expense_counts[category_name] += 1
                category_totals[category_name]["transactions"] += 1

                if account_name not in account_usage:
                    account_usage[account_name] = {"total_volume": 0.0, "transactions": 0.0}
                account_usage[account_name]["total_volume"] += abs(amount)
                account_usage[account_name]["transactions"] += 1

            # Average expense size; income and refunds would otherwise dilute it.
            for category, expense_count in category_expense_counts.items():
                if expense_count > 0:
                    category_totals[category]["avg_amount"] = category_totals[category]["total"] / expense_count

            analysis["monthly_trends"] = dict(sorted(monthly_data.items()))
            analysis["category_analysis"] = dict(
                sorted(category_totals.items(), key=lambda x: x[1]["total"], reverse=True)
            )
            analysis["account_usage"] = dict(
                sorted(account_usage.items(), key=lambda x: x[1]["total_volume"], reverse=True)
            )

            # Upstream returns newest first, so pick months by key, not response order.
            # Leave out partial months: the current one, the first one when the window
            # starts mid-month, and the oldest one when the 2,000-row page was truncated.
            current_month = end_date.strftime("%Y-%m")
            first_month = start_date.strftime("%Y-%m")
            complete_months = sorted(
                key
                for key in monthly_data
                if key != "Unknown" and key < current_month and (key > first_month or start_date.day == 1)
            )
            if _transactions_truncated(transactions, len(transactions_list)) and complete_months:
                complete_months = complete_months[1:]
            if include_forecasting and complete_months:
                forecast_months = complete_months[-3:]
                recent_months = [monthly_data[key] for key in forecast_months]
                avg_monthly_expenses = sum(m["expenses"] for m in recent_months) / len(recent_months)
                avg_monthly_income = sum(m["income"] for m in recent_months) / len(recent_months)

                next_month = (end_date + relativedelta(months=1)).strftime("%Y-%m")
                analysis["forecast"] = {
                    "next_month": next_month,
                    "predicted_expenses": round(avg_monthly_expenses, 2),
                    "predicted_income": round(avg_monthly_income, 2),
                    "predicted_net": round(avg_monthly_income - avg_monthly_expenses, 2),
                    "confidence": "medium",
                    "note": f"Forecast averages complete months {', '.join(forecast_months)}",
                }

        if not isinstance(budgets, Exception):
            try:
                analysis["budget_performance"] = _budget_totals(convert_dates_to_strings(budgets), verbose)
            except ValueError as error:
                errors["budgets"] = str(error)

        txn_count = len(transactions_list) if not isinstance(transactions, Exception) else 0
        analysis["metadata"] = {
            "api_calls_made": 3,
            "transfers_excluded": transfers_excluded if transfer_ids is not None else None,
            "total_transactions_analyzed": txn_count,
            "analysis_timestamp": datetime.now().isoformat(),
            "transactions_truncated": (
                _transactions_truncated(transactions, txn_count) if not isinstance(transactions, Exception) else None
            ),
        }

        if ctx is not None:
            await ctx.report_progress(2, 2, "Spending pattern analysis complete")

        log.info(
            "Spending pattern analysis completed",
            lookback_months=lookback_months,
            transactions_analyzed=txn_count,
            include_forecasting=include_forecasting,
        )

        return SpendingPatterns.model_validate({**analysis, "errors": errors, "verbose": verbose})

    except Exception as e:
        log.error("Failed to analyze spending patterns", error=str(e), lookback_months=lookback_months)
        raise


# Community client 1.7.x reads and mutations.

_JSON_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
_JSON_LIST: TypeAdapter[list[JsonValue]] = TypeAdapter(list[JsonValue])


def _json_object(value: object) -> dict[str, JsonValue]:
    return _JSON_OBJECT.validate_python(convert_dates_to_strings(value))


def _obj(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _require_text(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


def _first_of_month(month: str | None) -> str:
    return (parse_flexible_date(month) if month is not None else date.today()).replace(day=1).isoformat()


def _has_errors(errors: JsonValue) -> bool:
    """Monarch payload errors: a nonempty list, or an object with any set field."""
    if isinstance(errors, list):
        return bool(errors)
    if isinstance(errors, dict):
        return any(value not in (None, []) for key, value in errors.items() if key != "__typename")
    return bool(errors)


def _mutation_payload(result: object, key: str, action: str) -> dict[str, JsonValue]:
    """Return the named mutation payload, raising on a missing payload or errors."""
    payload = _json_object(result).get(key) if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        raise ValueError(f"Monarch returned an invalid {action} response")
    if _has_errors(payload.get("errors")):
        raise ValueError(f"Monarch rejected the {action}: {payload['errors']}")
    return payload


def _transaction_paging(response: object, offset: int, limit: int, returned: int) -> tuple[int | None, int | None]:
    """Return (total_count, next_offset) for one transaction page."""
    total: int | None = None
    if isinstance(response, dict):
        transactions = response.get("allTransactions")
        if isinstance(transactions, dict):
            count = transactions.get("totalCount")
            if isinstance(count, int) and not isinstance(count, bool):
                total = count
    # Without a total, a full page is the only hint that more rows exist.
    more = offset + returned < total if total is not None else returned > 0 and returned == limit
    return total, offset + returned if more else None


class _CancellableStdin(AsyncFile[str]):
    """Keep SDK framing without its cancellation-shielded blocking pipe read.

    mcp 2.3's stdio_server still reads stdin in a worker thread that cancellation waits
    on, so SIGINT hangs until the client closes stdin. Passing stdin also leaves fd 0 as
    is; the SDK still diverts fd 1 to stderr while serving.
    """

    def __init__(self, reader: asyncio.StreamReader) -> None:
        super().__init__(sys.stdin)
        self._reader = reader

    async def readline(self) -> str:
        parts: list[bytes] = []
        while True:
            try:
                parts.append(await self._reader.readuntil(b"\n"))
                break
            except asyncio.LimitOverrunError as exc:
                # MCP messages can exceed StreamReader's default 64 KiB limit.
                parts.append(await self._reader.readexactly(exc.consumed))
            except asyncio.IncompleteReadError as exc:
                parts.append(exc.partial)
                break
        return b"".join(parts).decode("utf-8", errors="replace")


async def main() -> None:
    """Start stdio immediately; authenticate on the first request that needs Monarch."""
    log.info("server_starting", session_file=str(session_file), auth_state=auth_state.value)

    # Python 3.10 raises KeyboardInterrupt inside whatever loop callback is running. If that
    # is a future wakeup, the waiting task never resumes and shutdown hangs. Cancelling the
    # main task instead lets every await unwind normally, like asyncio.run does in 3.11+.
    interrupted = False
    main_task = asyncio.current_task()

    def interrupt() -> None:
        nonlocal interrupted
        interrupted = True
        if main_task is not None:
            main_task.cancel()

    try:
        if sys.platform != "win32":
            asyncio.get_running_loop().add_signal_handler(signal.SIGINT, interrupt)
        if sys.platform == "win32" or stat.S_ISREG(os.fstat(sys.stdin.fileno()).st_mode):
            # Windows pipe handles and regular files need the SDK's file transport.
            await mcp.run_stdio_async()
        else:
            fd = sys.stdin.fileno()
            was_blocking = os.get_blocking(fd)
            reader = asyncio.StreamReader()
            transport: asyncio.BaseTransport | None = None
            with os.fdopen(os.dup(fd), "rb", buffering=0) as pipe:
                try:
                    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                        lambda: asyncio.StreamReaderProtocol(reader), pipe
                    )
                    # MCPServer has no public way to serve custom streams.
                    server = mcp._lowlevel_server
                    async with stdio_server(stdin=_CancellableStdin(reader)) as (read_stream, write_stream):
                        await server.run(read_stream, write_stream, server.create_initialization_options())
                finally:
                    if transport is not None:
                        transport.close()
                    os.set_blocking(fd, was_blocking)
    except (BrokenPipeError, ConnectionResetError):
        log.info("client_disconnected")
    except KeyboardInterrupt:
        log.info("interrupted")
    except asyncio.CancelledError:
        if not interrupted:
            raise
        log.info("interrupted")
    except Exception as e:
        log.error("server_error", error=str(e))
        raise


def run() -> None:
    """Await main() from the synchronous console-script entry point."""

    try:
        asyncio.run(main())
    except (BrokenPipeError, ConnectionResetError):
        pass  # Expected during client disconnect
    except KeyboardInterrupt:
        pass
    except Exception as eg:
        # Handle ExceptionGroups from anyio TaskGroups
        if hasattr(eg, "exceptions"):
            remaining = [
                exc
                for exc in eg.exceptions
                if not isinstance(exc, (BrokenPipeError, ConnectionResetError, OSError, EOFError))
                and not any(s in str(exc).lower() for s in ["broken pipe", "connection reset", "[errno 32]", "eof"])
            ]
            if remaining:
                log.error("fatal_error", error=str(eg))
                raise
        else:
            log.error("fatal_error", error=str(eg))
            raise


if __name__ == "__main__":
    run()
