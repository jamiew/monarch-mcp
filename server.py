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
import sys
import time
import uuid
import warnings
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import structlog
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
    ToolAnnotations,
)
from monarchmoney import MonarchMoney, RequireMFAException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue

JsonSerializable = str | int | float | bool | None | list["JsonSerializable"] | dict[str, "JsonSerializable"]

# Tools communicate only with the Monarch Money API.
READONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_SIDE_EFFECT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def parse_flexible_date(date_input: str) -> date:
    """Parse named periods, relative dates, and dateutil-supported date strings."""
    if not date_input:
        raise ValueError("Date input cannot be empty")

    date_input = date_input.lower().strip()
    today = date.today()

    if date_input in ["today", "now"]:
        return today
    elif date_input == "yesterday":
        return today - timedelta(days=1)
    elif date_input in ["this month", "current month"]:
        return date(today.year, today.month, 1)
    elif date_input in ["last month", "previous month"]:
        if today.month == 1:
            return date(today.year - 1, 12, 1)
        else:
            return date(today.year, today.month - 1, 1)
    elif date_input in ["this year", "current year"]:
        return date(today.year, 1, 1)
    elif date_input in ["last year", "previous year"]:
        return date(today.year - 1, 1, 1)
    elif date_input == "last week":
        return today - timedelta(days=7)
    elif date_input == "this week":
        # Start of this week (Monday)
        days_since_monday = today.weekday()
        return today - timedelta(days=days_since_monday)

    relative_pattern = re.match(r"(\d+)\s+(days?|weeks?|months?|years?)\s+ago", date_input)
    if relative_pattern:
        amount = int(relative_pattern.group(1))
        unit = relative_pattern.group(2).rstrip("s")

        try:
            if unit == "day":
                return today - timedelta(days=amount)
            elif unit == "week":
                return today - timedelta(weeks=amount)
            elif unit == "month":
                result = today - relativedelta(months=amount)
                return result.date() if hasattr(result, "date") else result
            elif unit == "year":
                result = today - relativedelta(years=amount)
                return result.date() if hasattr(result, "date") else result
        except (ValueError, OverflowError) as e:
            log.warning("Invalid relative date calculation", input=date_input, amount=amount, unit=unit, error=str(e))
            raise ValueError(f"Invalid relative date: {date_input}") from e

    try:
        parsed_datetime = date_parser.parse(date_input)
        parsed_date = parsed_datetime.date()

        min_date = date(1900, 1, 1)
        max_date = date(today.year + 50, 12, 31)

        if parsed_date < min_date or parsed_date > max_date:
            log.warning("Date outside reasonable range", input=date_input, parsed_date=parsed_date.isoformat())
            raise ValueError(f"Date {parsed_date.isoformat()} is outside reasonable range (1900-{today.year + 50})")

        return parsed_date

    except (ValueError, TypeError, OverflowError) as e:
        log.warning("Failed to parse date with dateutil", input=date_input, error=str(e))

        suggestions = [
            "Try formats like: 2024-01-15, Jan 15 2024, 15/01/2024",
            "Or natural language: today, yesterday, last month, this year",
            "Or relative: 30 days ago, 6 months ago, 1 year ago",
        ]
        suggestion_text = ". ".join(suggestions)
        raise ValueError(f"Could not parse date '{date_input}'. {suggestion_text}") from e


def build_date_filter(start_date: str | None, end_date: str | None) -> dict[str, str]:
    """Build ISO date filters; reject unparseable or reversed ranges.

    Monarch requires both dates or neither. A missing end defaults to today;
    a missing start defaults to the first day of the end date's month.
    """
    filters: dict[str, str] = {}

    if start_date and not end_date:
        end_date = "today"
        log.info("Auto-filling missing end_date with 'today'", start_date=start_date)
    elif end_date and not start_date:
        try:
            parsed_end = parse_flexible_date(end_date)
            today = date.today()

            if parsed_end < today or parsed_end.month != today.month or parsed_end.year != today.year:
                start_date = date(parsed_end.year, parsed_end.month, 1).isoformat()
                log.info(
                    "Auto-filling missing start_date with first of end_date's month",
                    end_date=end_date,
                    calculated_start=start_date,
                )
            else:
                start_date = "this month"
                log.info("Auto-filling missing start_date with 'this month'", end_date=end_date)
        except ValueError:
            # Let the parsing below report the invalid end date.
            start_date = "this month"
            log.info("Auto-filling missing start_date with 'this month' (end_date parse pending)", end_date=end_date)

    if start_date:
        parsed_date = parse_flexible_date(start_date)
        filters["start_date"] = parsed_date.isoformat()
        log.info("Parsed start_date", input=start_date, parsed=parsed_date.isoformat())

    if end_date:
        parsed_date = parse_flexible_date(end_date)
        filters["end_date"] = parsed_date.isoformat()
        log.info("Parsed end_date", input=end_date, parsed=parsed_date.isoformat())

    if "start_date" in filters and "end_date" in filters:
        start = date.fromisoformat(filters["start_date"])
        end = date.fromisoformat(filters["end_date"])

        if start > end:
            log.warning("Start date is after end date", start_date=filters["start_date"], end_date=filters["end_date"])
            raise ValueError(f"Start date ({filters['start_date']}) cannot be after end date ({filters['end_date']})")

    return filters


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
    status, plus pending and notes when present. Public tools document these fields.
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

current_session_id = str(uuid.uuid4())
usage_patterns: dict[str, list[dict[str, Any]]] = {}

P = ParamSpec("P")
R = TypeVar("R")


def track_usage(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Log tool arguments, timing, errors, and result sizes."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start_time = time.time()
        tool_name = func.__name__

        # Exclude credential keywords; financial inputs may still appear in logs.
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ["password", "mfa_secret"]}

        log.info("tool_call", tool=tool_name, args=safe_kwargs)

        call_info = {
            "session_id": current_session_id,
            "tool_name": tool_name,
            "timestamp": time.time(),
            "args": list(args),
            "kwargs": safe_kwargs,
        }

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

            call_info.update({"status": "success", "execution_time": execution_time, "result_size": result_chars})

            log.info(
                "tool_success",
                tool=tool_name,
                time_s=round(execution_time, 3),
                result_chars=result_chars,
                result_kb=round(result_kb, 2),
            )

            if tool_name not in usage_patterns:
                usage_patterns[tool_name] = []
            usage_patterns[tool_name].append(call_info)

            return result

        except Exception as e:
            execution_time = time.time() - start_time
            call_info.update({"status": "error", "execution_time": execution_time, "error": str(e)})

            log.error("tool_error", tool=tool_name, time_s=round(execution_time, 3), error=str(e))
            raise

    return wrapper


mcp = FastMCP("monarch-money")


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
    verbose: bool


class SearchMetadata(BaseModel):
    query: str
    result_count: int
    filters_applied: dict[str, JsonValue]


class SearchResult(MMModel):
    search_metadata: SearchMetadata
    transactions: list[JsonValue]


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


class AccountHistoryResult(MMModel):
    account_id: str
    history: JsonValue


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


class FinancialOverview(MMModel):
    period: str
    accounts: JsonValue = None
    budgets: JsonValue = None
    cashflow: JsonValue = None
    transactions: JsonValue = None
    categories: JsonValue = None
    transaction_summary: JsonValue = None
    batch_metadata: JsonValue = None


class SpendingPatterns(MMModel):
    analysis_period: JsonValue = None
    monthly_trends: JsonValue = None
    category_analysis: JsonValue = None
    account_usage: JsonValue = None
    budget_performance: JsonValue = None
    forecast: JsonValue = None
    metadata: JsonValue = None


# MCP resources


@mcp.resource("categories://list", title="Transaction Categories")
async def list_categories_resource() -> str:
    """Return Monarch's category JSON, including IDs for transaction updates."""
    await ensure_authenticated()
    categories = await api_call_with_retry("get_transaction_categories")
    return json.dumps(convert_dates_to_strings(categories), indent=2)


@mcp.resource("accounts://list", title="Linked Accounts")
async def list_accounts_resource() -> str:
    """Return Monarch's linked-account JSON with balances and institution details."""
    await ensure_authenticated()
    accounts = await api_call_with_retry("get_accounts")
    return json.dumps(convert_dates_to_strings(accounts), indent=2)


@mcp.resource("institutions://list", title="Linked Institutions")
async def list_institutions_resource() -> str:
    """Return Monarch's connected-institution JSON."""
    await ensure_authenticated()
    institutions = await api_call_with_retry("get_institutions")
    return json.dumps(convert_dates_to_strings(institutions), indent=2)


@mcp.resource("accounts://{account_id}/holdings", title="Account Holdings")
async def account_holdings_resource(account_id: str) -> str:
    """Return investment holdings for the account_id in the resource path."""
    await ensure_authenticated()
    holdings = await api_call_with_retry("get_account_holdings", account_id=account_id)
    return json.dumps(convert_dates_to_strings(holdings), indent=2)


@mcp.resource("accounts://{account_id}/history", title="Account Balance History")
async def account_history_resource(account_id: str) -> str:
    """Return balance history for the account_id in the resource path."""
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
        return Completion(values=await _category_name_completions(argument.value), hasMore=False)

    if isinstance(ref, ResourceTemplateReference) and argument.name == "account_id":
        return Completion(values=await _account_id_completions(argument.value), hasMore=False)

    return None


class AuthState(Enum):
    """Track authentication state to prevent duplicate initialization attempts."""

    NOT_INITIALIZED = "not_initialized"
    INITIALIZING = "initializing"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


mm_client: MonarchMoney | None = None
auth_state: AuthState = AuthState.NOT_INITIALIZED
auth_lock: asyncio.Lock | None = None  # Created in async context
auth_error: str | None = None
auth_failed_at: float | None = None
AUTH_RETRY_COOLDOWN_SECONDS = 60

# Default to the home directory because MCP clients may launch from read-only "/".
# MONARCH_SESSION_DIR overrides this location.
_session_dir_env = os.getenv("MONARCH_SESSION_DIR")
session_dir = Path(_session_dir_env).expanduser() if _session_dir_env else Path.home() / ".monarch-mcp"
session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
session_file = session_dir / "session.pickle"


def is_auth_error(error: Exception) -> bool:
    """Match auth-error text, excluding known network and library error patterns."""
    error_str = str(error).lower()

    false_positives = [
        "connector",
        "aiohttp",
        "transport",
        "connection refused",
        "connection reset",
        "timeout",
    ]

    if any(fp in error_str for fp in false_positives):
        return False

    auth_indicators = [
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "invalid credentials",
        "bad credentials",
        "authentication failed",
        "auth failed",
        "not authenticated",
        "invalid token",
        "token expired",
        "session expired",
        "session has expired",
    ]

    return any(indicator in error_str for indicator in auth_indicators)


def clear_session(reason: str = "unknown") -> None:
    """Reset the client and auth state, and remove session files.

    Use after auth failures or forced login. Log the supplied reason.
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


async def api_call_with_retry(method_name: str, *args: Any, max_retries: int = 3, **kwargs: Any) -> Any:
    """Call a client method, retrying recognized auth failures after reauthentication.

    max_retries excludes the initial attempt. Other errors propagate immediately.
    """
    global auth_state, mm_client

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            if mm_client is None:
                raise ValueError("mm_client is not initialized")

            method = getattr(mm_client, method_name)
            return await method(*args, **kwargs)

        except Exception as e:
            last_error = e

            if is_auth_error(e):
                if attempt < max_retries:
                    backoff_delay = 2**attempt
                    log.warning(
                        "api_auth_error",
                        attempt=attempt + 1,
                        max_attempts=max_retries + 1,
                        error=str(e),
                        backoff_s=backoff_delay,
                    )

                    clear_session(reason=f"authentication failure during API call (attempt {attempt + 1})")

                    if backoff_delay > 0:
                        await asyncio.sleep(backoff_delay)

                    await ensure_authenticated()
                    log.info("api_retry_after_reauth", attempt=attempt + 2, max_attempts=max_retries + 1)

                    continue
                else:
                    log.error("api_auth_retries_exhausted", max_retries=max_retries, error=str(e))
                    raise
            else:
                raise

    if last_error:
        raise last_error
    raise RuntimeError("api_call_with_retry completed without result or error")


async def initialize_client() -> None:
    """Load a cached session or log in. The first API call detects expired sessions."""
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
    mm_client = MonarchMoney()

    force_login = os.getenv("MONARCH_FORCE_LOGIN") == "true"
    if session_file.exists() and not force_login:
        try:
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                mm_client.load_session(str(session_file))

            log.info("auth_session_loaded", session_file=str(session_file))
            auth_state = AuthState.AUTHENTICATED
            return

        except Exception as e:
            log.warning("auth_session_load_failed", error=str(e))
            if is_auth_error(e):
                clear_session(reason="invalid session file")

    else:
        if force_login:
            log.info("auth_force_login")
            clear_session(reason="forced login requested")

    max_retries = 2
    retry_delay = 3

    for attempt in range(max_retries):
        try:
            # clear_session() discards the client, including between login attempts.
            if mm_client is None:
                mm_client = MonarchMoney()
            log.info("auth_login_attempt", attempt=attempt + 1, max_retries=max_retries, mfa=bool(mfa_secret))
            # Disable the library's default .mm session storage; save to our path only.
            if mfa_secret:
                await mm_client.login(
                    email, password, mfa_secret_key=mfa_secret, use_saved_session=False, save_session=False
                )
            else:
                await mm_client.login(email, password, use_saved_session=False, save_session=False)

            # Suppress library output so it cannot corrupt the stdio protocol.
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                mm_client.save_session(str(session_file))

            if session_file.exists():
                session_file.chmod(0o600)

            auth_state = AuthState.AUTHENTICATED
            auth_error = None
            log.info("auth_success")
            return

        except RequireMFAException as e:
            error_msg = "Multi-factor authentication required but MONARCH_MFA_SECRET not set"
            log.error("auth_mfa_required")
            auth_state = AuthState.FAILED
            auth_error = error_msg
            raise ValueError(error_msg) from e

        except Exception as e:
            if attempt < max_retries - 1:
                log.warning("auth_attempt_failed", attempt=attempt + 1, error=str(e), is_auth=is_auth_error(e))
                if is_auth_error(e):
                    clear_session(reason=f"auth failure on attempt {attempt + 1}")
                await asyncio.sleep(retry_delay)
            else:
                error_msg = f"Authentication failed after {max_retries} attempts: {e}"
                log.error("auth_failed", error=str(e), max_retries=max_retries)
                auth_state = AuthState.FAILED
                auth_error = str(e)
                auth_failed_at = time.time()
                raise


async def ensure_authenticated() -> None:
    """Initialize once under a lock, respecting the cooldown after timed failures."""
    global mm_client, auth_state, auth_lock, auth_error, auth_failed_at

    # Initialize lock on first call (must be done in async context)
    if auth_lock is None:
        auth_lock = asyncio.Lock()

    if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
        return

    log.info("auth_needed", state=auth_state.value)

    async with auth_lock:
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

        if auth_state == AuthState.INITIALIZING:
            log.warning("auth_already_initializing")
            await asyncio.sleep(1)
            if auth_state == AuthState.AUTHENTICATED:
                return
            raise ValueError("Authentication is taking too long")

        auth_state = AuthState.INITIALIZING
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
        verbose: False returns compact fields; True preserves the full API objects.

    Boolean filters default to None (no restriction). Results contain transactions,
    count (this page only), and verbose. Use offset to fetch further pages.

    Compact fields: id, date, amount, merchant, plaidName, category, categoryId,
    account, needsReview; pending appears only when true, notes only when nonempty.
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
        )

        response = await api_call_with_retry("get_transactions", limit=limit, offset=offset, **filters)
        transactions = extract_transactions_list(response)
        transactions = convert_dates_to_strings(transactions)

        if not verbose and isinstance(transactions, list):
            transactions = format_transactions_compact(transactions)

        log.info("Transactions retrieved", count=len(transactions))
        # model_validate (not the constructor) sidesteps mypy's list invariance:
        # list[dict[str, Any]] is not assignable to the model's list[JsonValue].
        return TransactionsResult.model_validate(
            {"transactions": transactions, "count": len(transactions), "verbose": verbose}
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
        verbose: False uses get_transactions' compact fields; True keeps full API objects.

    Boolean filters default to None (no restriction). Returns matching transactions
    and search_metadata with the query, this page's result_count, and applied filters.
    Use offset for further pages; result_count is not the total number of matches.
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

        log.info("Search complete", query=query_str, result_count=len(transactions))
        # model_validate (not the constructor) sidesteps mypy's list invariance.
        return SearchResult.model_validate({"search_metadata": metadata, "transactions": transactions})

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
            timeout=30.0,
        )
        result = convert_dates_to_strings(result)
        return TransactionResult(transaction=result)
    except asyncio.TimeoutError as e:
        log.error("create_transaction_timeout")
        raise ValueError("Transaction creation timed out after 30 seconds. Please try again.") from e
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
) -> TransactionResult:
    """Update a transaction, leaving omitted fields unchanged.

    Args:
        transaction_id: Transaction ID from get_transactions or search_transactions.
        amount: New amount.
        merchant_name: New display name, not the read-only plaidName statement text.
            The API ignores empty names.
        category_id: New category ID from get_transaction_categories.
        date: New date in YYYY-MM-DD format.
        notes: Memo, separate from the merchant name; "" clears it.
        goal_id: Savings goal ID; "" clears the association.
        hide_from_reports: Whether to hide the transaction from reports.
        needs_review: Whether to flag the transaction for review.

    Returns updated transaction details. This tool cannot change the transaction
    ID, account, pending status, attachments, or timestamps. Use
    update_transaction_splits to change splits and update_recurring_transaction
    to change a merchant's recurring schedule.
    """
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
        if date is not None:
            updates["date"] = datetime.strptime(date, "%Y-%m-%d").date()
        if notes is not None:
            updates["notes"] = notes
        if goal_id is not None:
            updates["goal_id"] = goal_id
        if hide_from_reports is not None:
            updates["hide_from_reports"] = hide_from_reports
        if needs_review is not None:
            updates["needs_review"] = needs_review

        update_fields = [k for k in updates if k != "transaction_id"]
        log.info("updating_transaction", transaction_id=transaction_id, fields=update_fields)

        result = await asyncio.wait_for(
            api_call_with_retry("update_transaction", **updates),
            timeout=30.0,
        )
        result = convert_dates_to_strings(result)
        return TransactionResult(transaction=result)
    except asyncio.TimeoutError as e:
        log.error("update_transaction_timeout", transaction_id=transaction_id)
        raise ValueError("Transaction update timed out after 30 seconds. Please try again.") from e
    except ValueError as e:
        error_msg = str(e)
        if "date" in error_msg.lower():
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD (e.g., 2024-01-15). Error: {e}") from e
        raise
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
            (YYYY-MM-DD), notes, goal_id, hide_from_reports, and needs_review.
            Fields have the same meaning as update_transaction; omitted/null fields
            stay unchanged. Empty notes or goal_id clears that value.

    Items reject unknown fields and wrong types without coercion; amounts must
    be finite numbers and flags must be booleans. Invalid items fail individually;
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
                if update.date is not None:
                    update_params["date"] = datetime.strptime(update.date, "%Y-%m-%d").date()

                await asyncio.wait_for(api_call_with_retry("update_transaction", **update_params), timeout=30.0)

                return BulkItemResult(transaction_id=txn_id, status="success")

            except asyncio.TimeoutError:
                return BulkItemResult(
                    transaction_id=txn_id,
                    status="error",
                    error="Update timed out after 30 seconds",
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

    try:
        result = await api_call_with_retry("get_transaction_splits", transaction_id=transaction_id)
        result = convert_dates_to_strings(result)
        transaction = result.get("getTransaction") or {} if isinstance(result, dict) else {}
        splits = transaction.get("splitTransactions") or []
        return TransactionSplitsResult(
            transaction_id=transaction_id,
            has_split_transactions=bool(splits),
            splits=splits,
        )
    except Exception as e:
        log.error("Failed to get transaction splits", error=str(e), transaction_id=transaction_id)
        raise


@mcp.tool(annotations=WRITE_IDEMPOTENT, title="Update Transaction Splits")
@track_usage
async def update_transaction_splits(transaction_id: str, splits: list[TransactionSplit]) -> UpdateSplitsResult:
    """Replace a transaction's entire set of splits, or remove all splits with [].

    Args:
        transaction_id: Parent transaction ID from get_transactions or search_transactions.
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
            timeout=30.0,
        )
        result = convert_dates_to_strings(result)

        payload = result.get("updateTransactionSplit") or {} if isinstance(result, dict) else {}
        errors = payload.get("errors")
        if errors:
            raise ValueError(f"Monarch rejected the split update: {errors}")

        transaction = payload.get("transaction") or {}
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
        raise ValueError("Split update timed out after 30 seconds. Please try again.") from e
    except Exception as e:
        log.error("update_transaction_splits_failed", transaction_id=transaction_id, error=str(e))
        raise


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
    account_id: str, start_date: str | None = None, end_date: str | None = None
) -> AccountHistoryResult:
    """Get balance history for account_id from get_accounts.

    Optional start_date and end_date must use YYYY-MM-DD, not natural language.
    """
    await ensure_authenticated()

    kwargs: dict[str, Any] = {"account_id": account_id}
    if start_date:
        kwargs["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        kwargs["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()

    try:
        history = await api_call_with_retry("get_account_history", **kwargs)
        history = convert_dates_to_strings(history)
        return AccountHistoryResult(account_id=account_id, history=history)
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
    With neither date, fetch the current calendar month. With only one date,
    use the beginning or end of that date's month for the missing bound.

    The recurring.recurringTransactionItems list includes each occurrence's date,
    amount, account, category, transactionId (when matched), and stream containing
    the merchant ID, frequency, and expected amount. isPast describes the date,
    not whether a bill was paid. Use get_transactions(is_recurring=True) for
    recorded transactions instead.
    """
    start = parse_flexible_date(start_date) if start_date is not None else None
    end = parse_flexible_date(end_date) if end_date is not None else None
    if start is None:
        start = (end or date.today()).replace(day=1)
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
async def set_budget_amount(category_id: str, amount: float) -> SetBudgetResult:
    """Set amount for a category_id from get_transaction_categories."""
    await ensure_authenticated()

    try:
        result = await api_call_with_retry("set_budget_amount", category_id=category_id, amount=amount)
        result = convert_dates_to_strings(result)
        log.info("Budget amount updated", category_id=category_id, amount=amount)
        return SetBudgetResult(category_id=category_id, amount=amount, result=result)
    except Exception as e:
        log.error("Failed to set budget amount", error=str(e), category_id=category_id)
        raise


@mcp.tool(annotations=WRITE_CREATE, title="Create Manual Account")
@track_usage
async def create_manual_account(account_name: str, account_type: str, balance: float) -> CreateAccountResult:
    """Create a manual account with account_name, Monarch account_type, and balance."""
    await ensure_authenticated()

    try:
        result = await api_call_with_retry(
            "create_manual_account", account_name=account_name, account_type=account_type, balance=balance
        )
        result = convert_dates_to_strings(result)
        log.info("Manual account created", name=account_name, type=account_type)
        return CreateAccountResult(account=result)
    except Exception as e:
        log.error("Failed to create manual account", error=str(e), name=account_name)
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

    Fetches all matching pages. Expenses are positive magnitudes.
    """
    await ensure_authenticated()

    try:
        log.info("Generating spending summary", start_date=start_date, end_date=end_date, group_by=group_by)

        filters = build_date_filter(start_date, end_date)
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

        for txn in transactions:
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
                    txn.get("account", {}).get("name", "Unknown") if isinstance(txn.get("account"), dict) else "Unknown"
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
            period=Period(start=start_date, end=end_date),
            group_by=group_by,
            groups={
                key: GroupSummary(income=g["income"], expenses=g["expenses"], net=g["net"], count=int(g["count"]))
                for key, g in sorted_groups.items()
            },
            totals=Totals(income=totals_data["income"], expenses=totals_data["expenses"], net=totals_data["net"]),
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
        result = await api_call_with_retry("request_accounts_refresh")
        result = convert_dates_to_strings(result)
        log.info("Account refresh requested")
        return RefreshResult(result=result)
    except Exception as e:
        log.error("Failed to refresh accounts", error=str(e))
        raise


@mcp.tool(annotations=READONLY, title="Complete Financial Overview")
@track_usage
async def get_complete_financial_overview(period: str = "this month", ctx: Context | None = None) -> FinancialOverview:
    """Fetch accounts, budgets, cash flow, transactions, and categories together.

    period is a start date or phrase such as 'this month', 'last month', or 'this year';
    the end is always today. 'last month' therefore includes the current month too.
    Transactions and their summary use one page of up to 500 entries, not the full
    history. Failed API sections contain errors while successful sections remain.
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
            results["accounts"] = convert_dates_to_strings(accounts)
        else:
            results["accounts"] = {"error": str(accounts)}

        if not isinstance(budgets, Exception):
            results["budgets"] = convert_dates_to_strings(budgets)
        else:
            results["budgets"] = {"error": str(budgets)}

        if not isinstance(cashflow, Exception):
            results["cashflow"] = convert_dates_to_strings(cashflow)
        else:
            results["cashflow"] = {"error": str(cashflow)}

        if not isinstance(transactions, Exception):
            transactions_list = extract_transactions_list(transactions)
            results["transactions"] = convert_dates_to_strings(transactions_list)
            if isinstance(transactions_list, list):
                results["transaction_summary"] = {
                    "total_count": len(transactions_list),
                    "total_income": sum(
                        float(t.get("amount", 0)) for t in transactions_list if float(t.get("amount", 0)) > 0
                    ),
                    "total_expenses": sum(
                        abs(float(t.get("amount", 0))) for t in transactions_list if float(t.get("amount", 0)) < 0
                    ),
                    "unique_categories": len(
                        {
                            t.get("category", {}).get("name", "Unknown")
                            for t in transactions_list
                            if isinstance(t.get("category"), dict)
                        }
                    ),
                    "unique_accounts": len(
                        {
                            t.get("account", {}).get("name", "Unknown")
                            for t in transactions_list
                            if isinstance(t.get("account"), dict)
                        }
                    ),
                }
        else:
            results["transactions"] = {"error": str(transactions)}

        if not isinstance(categories, Exception):
            results["categories"] = convert_dates_to_strings(categories)
        else:
            results["categories"] = {"error": str(categories)}

        if ctx is not None:
            await ctx.report_progress(5, 5, "Assembled financial overview")

        results["batch_metadata"] = {
            "period": period,
            "filters_applied": convert_dates_to_strings(filters),
            "api_calls_made": 5,
            "timestamp": datetime.now().isoformat(),
        }
        results["period"] = period

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
    lookback_months: int = 6, include_forecasting: bool = True, ctx: Context | None = None
) -> SpendingPatterns:
    """Summarize monthly trends, category expenses, account usage, and budget data.

    Args:
        lookback_months: Months before today to include (default: 6).
        include_forecasting: Include average-based income and expense estimates.

    Requests one page of up to 2000 transactions, without pagination; analysis can
    be incomplete for larger periods. Forecasts average up to three month buckets
    in response order, not necessarily the latest three calendar months. The
    confidence label is fixed, not a statistical measure. Failed transaction or
    budget requests leave the corresponding analysis sections empty.
    """
    await ensure_authenticated()

    try:
        if ctx is not None:
            await ctx.report_progress(0, 2, "Fetching transactions, budgets, accounts, categories…")

        end_date = datetime.now().date()
        start_date = end_date - relativedelta(months=lookback_months)

        transactions_task = api_call_with_retry(
            "get_transactions", limit=2000, start_date=start_date, end_date=end_date
        )
        budgets_task = api_call_with_retry("get_budgets", start_date=start_date, end_date=end_date)
        accounts_task = api_call_with_retry("get_accounts")
        categories_task = api_call_with_retry("get_transaction_categories")

        api_results = await asyncio.gather(
            transactions_task, budgets_task, accounts_task, categories_task, return_exceptions=True
        )
        transactions, budgets, accounts, categories = api_results

        if ctx is not None:
            await ctx.report_progress(1, 2, "Computing trends, category and account analysis…")

        analysis = {
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
            account_usage: dict[str, dict[str, float]] = {}

            for txn in transactions_list:
                txn_date = txn.get("date", "")
                amount = float(txn.get("amount", 0))
                category_name = (
                    txn.get("category", {}).get("name", "Uncategorized")
                    if isinstance(txn.get("category"), dict)
                    else "Uncategorized"
                )
                account_name = (
                    txn.get("account", {}).get("name", "Unknown") if isinstance(txn.get("account"), dict) else "Unknown"
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
                category_totals[category_name]["total"] += abs(amount) if amount < 0 else 0.0
                category_totals[category_name]["transactions"] += 1

                if account_name not in account_usage:
                    account_usage[account_name] = {"total_volume": 0.0, "transactions": 0.0}
                account_usage[account_name]["total_volume"] += abs(amount)
                account_usage[account_name]["transactions"] += 1

            for category in category_totals:
                if category_totals[category]["transactions"] > 0:
                    category_totals[category]["avg_amount"] = (
                        category_totals[category]["total"] / category_totals[category]["transactions"]
                    )

            analysis["monthly_trends"] = dict(sorted(monthly_data.items()))
            analysis["category_analysis"] = dict(
                sorted(category_totals.items(), key=lambda x: x[1]["total"], reverse=True)  # type: ignore[index]
            )
            analysis["account_usage"] = dict(
                sorted(account_usage.items(), key=lambda x: x[1]["total_volume"], reverse=True)  # type: ignore[index]
            )

            if include_forecasting and monthly_data:
                recent_months = list(monthly_data.values())[-3:]
                if recent_months:
                    avg_monthly_expenses = sum(m["expenses"] for m in recent_months) / len(recent_months)
                    avg_monthly_income = sum(m["income"] for m in recent_months) / len(recent_months)

                    next_month = (end_date + relativedelta(months=1)).strftime("%Y-%m")
                    analysis["forecast"] = {
                        "next_month": next_month,
                        "predicted_expenses": round(avg_monthly_expenses, 2),
                        "predicted_income": round(avg_monthly_income, 2),
                        "predicted_net": round(avg_monthly_income - avg_monthly_expenses, 2),
                        "confidence": "medium",
                        "note": "Forecast based on 3-month spending average",
                    }

        if not isinstance(budgets, Exception):
            analysis["budget_performance"] = convert_dates_to_strings(budgets)

        txn_count = len(transactions_list) if not isinstance(transactions, Exception) else 0
        analysis["metadata"] = {
            "api_calls_made": 4,
            "total_transactions_analyzed": txn_count,
            "analysis_timestamp": datetime.now().isoformat(),
        }

        if ctx is not None:
            await ctx.report_progress(2, 2, "Spending pattern analysis complete")

        log.info(
            "Spending pattern analysis completed",
            lookback_months=lookback_months,
            transactions_analyzed=txn_count,
            include_forecasting=include_forecasting,
        )

        return SpendingPatterns.model_validate(analysis)

    except Exception as e:
        log.error("Failed to analyze spending patterns", error=str(e), lookback_months=lookback_months)
        raise


async def main() -> None:
    """Start stdio immediately; authenticate on the first request that needs Monarch."""
    log.info("server_starting", session_file=str(session_file), auth_state=auth_state.value)

    try:
        await mcp.run_stdio_async()
    except (BrokenPipeError, ConnectionResetError):
        log.info("client_disconnected")
    except KeyboardInterrupt:
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
