#!/usr/bin/env python3
"""MonarchMoney MCP Server - Provides access to Monarch Money financial data via MCP protocol."""

import asyncio
import contextlib
import functools
import io
import json
import logging
import os
import re
import signal
import sys
import time
import uuid
import warnings
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from monarchmoney import MonarchMoney, RequireMFAException

# Type definitions for Monarch Money API responses
JsonSerializable = str | int | float | bool | None | list["JsonSerializable"] | dict[str, "JsonSerializable"]

# Reusable tool annotations — all tools are closed-world (only talk to Monarch Money API)
READONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_SIDE_EFFECT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def parse_flexible_date(date_input: str) -> date:
    """
    Parse flexible date inputs including natural language with comprehensive error handling.

    Supports:
    - "today", "now"
    - "yesterday"
    - "this month", "current month"
    - "last month", "previous month"
    - "this year", "current year"
    - "last year", "previous year"
    - "last week", "this week"
    - "30 days ago", "6 months ago"
    - Any date format supported by dateutil.parser
    """
    if not date_input:
        raise ValueError("Date input cannot be empty")

    # Handle common natural language patterns
    date_input = date_input.lower().strip()
    today = date.today()

    if date_input in ["today", "now"]:
        return today
    elif date_input == "yesterday":
        return today - timedelta(days=1)
    elif date_input in ["this month", "current month"]:
        return date(today.year, today.month, 1)
    elif date_input in ["last month", "previous month"]:
        # Handle month rollover correctly
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

    # Handle relative patterns like "30 days ago", "6 months ago"
    relative_pattern = re.match(r"(\d+)\s+(days?|weeks?|months?|years?)\s+ago", date_input)
    if relative_pattern:
        amount = int(relative_pattern.group(1))
        unit = relative_pattern.group(2).rstrip("s")  # Remove plural 's'

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

    # Try parsing with dateutil for standard date formats
    try:
        parsed_datetime = date_parser.parse(date_input)
        parsed_date = parsed_datetime.date()

        # Validate reasonable date range (1900 to 50 years in future)
        min_date = date(1900, 1, 1)
        max_date = date(today.year + 50, 12, 31)

        if parsed_date < min_date or parsed_date > max_date:
            log.warning("Date outside reasonable range", input=date_input, parsed_date=parsed_date.isoformat())
            raise ValueError(f"Date {parsed_date.isoformat()} is outside reasonable range (1900-{today.year + 50})")

        return parsed_date

    except (ValueError, TypeError, OverflowError) as e:
        log.warning("Failed to parse date with dateutil", input=date_input, error=str(e))

        # Provide helpful error message with suggestions
        suggestions = [
            "Try formats like: 2024-01-15, Jan 15 2024, 15/01/2024",
            "Or natural language: today, yesterday, last month, this year",
            "Or relative: 30 days ago, 6 months ago, 1 year ago",
        ]
        suggestion_text = ". ".join(suggestions)
        raise ValueError(f"Could not parse date '{date_input}'. {suggestion_text}") from e


def build_date_filter(start_date: str | None, end_date: str | None) -> dict[str, str]:
    """
    Build date filter dictionary with flexible parsing and comprehensive error recovery.

    Args:
        start_date: Start date string (flexible format supported)
        end_date: End date string (flexible format supported)

    Returns:
        Dictionary with ISO format date strings

    Raises:
        ValueError: If date parsing fails completely after all fallback attempts

    Note:
        Monarch Money API requires BOTH start_date AND end_date when filtering by date.
        If only one is provided, the other will be auto-filled with a sensible default:
        - Missing end_date: defaults to today
        - Missing start_date: defaults to start of current month
    """
    filters: dict[str, str] = {}

    # Auto-fill missing dates for better UX (Monarch API requires both or neither)
    if start_date and not end_date:
        # User provided start but not end - default end to today
        end_date = "today"
        log.info("Auto-filling missing end_date with 'today'", start_date=start_date)
    elif end_date and not start_date:
        # User provided end but not start - need to parse end_date first to choose smart default
        # If end_date is in the past, use beginning of that month; otherwise use this month
        try:
            parsed_end = parse_flexible_date(end_date)
            today = date.today()

            # If end date is in the past or in a different month, use first of that month
            if parsed_end < today or parsed_end.month != today.month or parsed_end.year != today.year:
                # Use first day of the end_date's month
                start_date = date(parsed_end.year, parsed_end.month, 1).isoformat()
                log.info(
                    "Auto-filling missing start_date with first of end_date's month",
                    end_date=end_date,
                    calculated_start=start_date,
                )
            else:
                # End date is this month, use "this month"
                start_date = "this month"
                log.info("Auto-filling missing start_date with 'this month'", end_date=end_date)
        except ValueError:
            # If we can't parse end_date yet, just use "this month" and let validation catch issues later
            start_date = "this month"
            log.info("Auto-filling missing start_date with 'this month' (end_date parse pending)", end_date=end_date)

    # parse_flexible_date already handles all formats (natural language, ISO, dateutil)
    if start_date:
        parsed_date = parse_flexible_date(start_date)
        filters["start_date"] = parsed_date.isoformat()
        log.info("Parsed start_date", input=start_date, parsed=parsed_date.isoformat())

    if end_date:
        parsed_date = parse_flexible_date(end_date)
        filters["end_date"] = parsed_date.isoformat()
        log.info("Parsed end_date", input=end_date, parsed=parsed_date.isoformat())

    # Validate date range logic
    if "start_date" in filters and "end_date" in filters:
        start = date.fromisoformat(filters["start_date"])
        end = date.fromisoformat(filters["end_date"])

        if start > end:
            log.warning("Start date is after end date", start_date=filters["start_date"], end_date=filters["end_date"])
            raise ValueError(f"Start date ({filters['start_date']}) cannot be after end date ({filters['end_date']})")

    return filters


def convert_dates_to_strings(obj: Any) -> Any:
    """
    Recursively convert all date/datetime objects to ISO format strings.

    This ensures that the data can be serialized by any JSON encoder,
    not just our custom one. This is necessary because the MCP framework
    may attempt to serialize the response before we can use our custom encoder.
    """
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
    """
    Extract the transactions list from monarchmoney API response.

    The monarchmoney library returns:
    {
        "allTransactions": {
            "totalCount": 123,
            "results": [...]  # <-- actual transactions
        },
        "transactionRules": ...
    }

    This function extracts the results list from the nested structure.
    """
    if isinstance(response, list):
        # Already a list (shouldn't happen with current API)
        return response
    elif isinstance(response, dict):
        # Check for the nested structure
        if "allTransactions" in response:
            all_txns = response["allTransactions"]
            if isinstance(all_txns, dict) and "results" in all_txns:
                results = all_txns["results"]
                if isinstance(results, list):
                    return results
        # Fallback: maybe it's a different structure
        log.warning("Unexpected transaction response structure", keys=list(response.keys()))
        return []
    else:
        log.error("Unexpected transaction response type", response_type=str(type(response)))
        return []


def format_transactions_compact(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Format transactions in a compact format with only essential fields.

    Returns simplified transaction objects with only:
    - id, date, amount
    - merchant name, plaidName (original statement name)
    - category id + name (id needed for updates)
    - account display name
    - needsReview flag
    - pending flag (only if True)
    - notes (only if present)

    Use verbose=True to get full transaction details when needed.
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

        # Only include pending if actually pending (saves bytes on the common case)
        if txn.get("pending"):
            compact_txn["pending"] = True

        # Include notes if present
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
    """Build filters dict for get_transactions API calls.

    Shared by get_transactions and search_transactions to avoid duplication.
    """
    filters: dict[str, Any] = build_date_filter(start_date, end_date)

    # monarchmoney expects account_ids and category_ids as LISTS
    if account_id:
        filters["account_ids"] = [account_id]
    if category_id:
        filters["category_ids"] = [category_id]
    if tag_ids:
        filters["tag_ids"] = [t.strip() for t in tag_ids.split(",")]

    # Boolean filters (only include if explicitly set)
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


# Configure logger to output to stderr only with error handling
class SafeStreamHandler(logging.StreamHandler[Any]):
    """Stream handler that gracefully handles broken pipes."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except (BrokenPipeError, ConnectionResetError):
            # Silently ignore broken pipe errors during logging
            pass
        except Exception:
            # Let other logging errors bubble up
            self.handleError(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[SafeStreamHandler(sys.stderr)],
)

# Configure structured logging
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

# Get structured logger for this module
log = structlog.get_logger(__name__)

# Suppress third-party library logging to reduce noise
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("monarchmoney").setLevel(logging.ERROR)
logging.getLogger("gql").setLevel(logging.ERROR)
logging.getLogger("gql.transport").setLevel(logging.ERROR)

warnings.filterwarnings("ignore", category=UserWarning, module="gql.transport.aiohttp")

# Session tracking for usage analytics
current_session_id = str(uuid.uuid4())
usage_patterns: dict[str, list[dict[str, Any]]] = {}


def track_usage(func: Any) -> Any:
    """Decorator to track tool usage patterns for analytics with detailed debugging."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.time()
        tool_name = func.__name__

        # Format args for logging (exclude sensitive data)
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ["password", "mfa_secret"]}

        log.info("tool_call", tool=tool_name, args=safe_kwargs)

        # Track this call
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

            # Calculate result size and stats
            result_chars = len(str(result)) if result else 0
            result_kb = result_chars / 1024

            # Try to extract additional stats from JSON results
            extra_stats = ""
            try:
                if isinstance(result, str) and result.strip().startswith("{"):
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        # Look for common list fields to count items
                        for key in ["transactions", "accounts", "budgets", "categories", "results"]:
                            if key in parsed and isinstance(parsed[key], list):
                                extra_stats += f" | {key}: {len(parsed[key])} items"
                        # Check for batch summaries
                        if "batch_summary" in parsed:
                            summary = parsed["batch_summary"]
                            if isinstance(summary, dict):
                                extra_stats += f" | batch: {summary}"
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

            call_info.update({"status": "success", "execution_time": execution_time, "result_size": result_chars})

            log.info(
                "tool_success",
                tool=tool_name,
                time_s=round(execution_time, 3),
                result_chars=result_chars,
                result_kb=round(result_kb, 2),
            )

            # Track usage patterns in memory for batching analysis
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


# Initialize the FastMCP server
mcp = FastMCP("monarch-money")


# =============================================================================
# MCP Resources - Read-only data endpoints for reference data
# =============================================================================


@mcp.resource("categories://list")
async def list_categories_resource() -> str:
    """
    List all transaction categories available in Monarch Money.

    Returns a JSON array of category objects with id, name, group, and icon.
    This is read-only reference data useful for understanding available categories
    before creating or updating transactions.
    """
    await ensure_authenticated()
    categories = await api_call_with_retry("get_transaction_categories")
    return json.dumps(convert_dates_to_strings(categories), indent=2)


@mcp.resource("accounts://list")
async def list_accounts_resource() -> str:
    """
    List all linked financial accounts in Monarch Money.

    Returns a JSON array of account objects including checking, savings,
    credit cards, investments, and other account types with their balances
    and institution information.
    """
    await ensure_authenticated()
    accounts = await api_call_with_retry("get_accounts")
    return json.dumps(convert_dates_to_strings(accounts), indent=2)


@mcp.resource("institutions://list")
async def list_institutions_resource() -> str:
    """
    List all connected financial institutions in Monarch Money.

    Returns a JSON array of institution objects showing which banks,
    brokerages, and other financial institutions are connected to the account.
    """
    await ensure_authenticated()
    institutions = await api_call_with_retry("get_institutions")
    return json.dumps(convert_dates_to_strings(institutions), indent=2)


# =============================================================================
# MCP Prompts - Reusable prompt templates for common financial analyses
# =============================================================================


@mcp.prompt()
def analyze_spending(period: str = "this month", category: str | None = None) -> str:
    """
    Generate a prompt template for analyzing spending patterns.

    Args:
        period: Time period to analyze (e.g., "this month", "last 3 months", "2024")
        category: Optional category to focus on (e.g., "Food & Dining", "Shopping")
    """
    category_focus = f" specifically for {category}" if category else ""
    return f"""Please analyze my spending{category_focus} for {period}.

Use the get_transactions tool to fetch transaction data for the specified period, then provide:

1. **Total Spending**: Sum of all expenses
2. **Top Categories**: Which categories had the most spending
3. **Trends**: Any notable patterns or changes
4. **Insights**: Specific observations about spending habits
5. **Recommendations**: Actionable suggestions to optimize spending

Focus on practical insights rather than just listing numbers."""


@mcp.prompt()
def budget_review(month: str = "current") -> str:
    """
    Generate a prompt template for reviewing budget performance.

    Args:
        month: Which month to review ("current", "last", or "YYYY-MM" format)
    """
    return f"""Please review my budget performance for {month}.

Use get_budgets and get_transactions tools to compare budgeted amounts vs actual spending:

1. **Budget vs Actual**: For each category, show budgeted amount, actual spending, and variance
2. **Over Budget**: Highlight categories where spending exceeded budget
3. **Under Budget**: Show categories with unused budget
4. **Overall Status**: Am I on track for the month?
5. **Adjustments**: Suggest any budget adjustments based on actual patterns

Present the data in a clear, easy-to-scan format."""


@mcp.prompt()
def financial_health_check() -> str:
    """
    Generate a comprehensive financial health assessment prompt.

    This prompt guides a thorough review of accounts, spending, and budgets.
    """
    return """Please perform a comprehensive financial health check.

Use the available tools to gather data and provide:

1. **Account Overview**:
   - Total assets and liabilities
   - Net worth calculation
   - Account balances summary

2. **Cash Flow Analysis**:
   - Monthly income vs expenses
   - Savings rate
   - Recurring transactions review

3. **Spending Analysis**:
   - Top spending categories (last 30 days)
   - Unusual or large transactions
   - Comparison to previous month

4. **Budget Status**:
   - Categories on track vs off track
   - Projected month-end status

5. **Action Items**:
   - Specific recommendations
   - Areas needing attention
   - Positive trends to maintain

Be concise but thorough. Highlight the most important insights first."""


@mcp.prompt()
def transaction_categorization_help(description: str) -> str:
    """
    Generate a prompt to help categorize a transaction.

    Args:
        description: The transaction description or merchant name
    """
    return f"""Help me categorize this transaction: "{description}"

First, use the categories://list resource to see all available categories.

Then suggest:
1. **Best Category Match**: The most appropriate category for this transaction
2. **Alternative Options**: Other categories that might fit
3. **Reasoning**: Why you recommend this categorization

If this is a merchant I transact with frequently, also note if the categorization
should be applied to future transactions from the same merchant."""


class AuthState(Enum):
    """Track authentication state to prevent duplicate initialization attempts."""

    NOT_INITIALIZED = "not_initialized"
    INITIALIZING = "initializing"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


# Global variables for authentication
mm_client: MonarchMoney | None = None
auth_state: AuthState = AuthState.NOT_INITIALIZED
auth_lock: asyncio.Lock | None = None  # Created in async context
auth_error: str | None = None  # Store last auth error for debugging
auth_failed_at: float | None = None  # Timestamp of last auth failure for cooldown
AUTH_RETRY_COOLDOWN_SECONDS = 60  # Wait 60 seconds before retrying after FAILED state

# Secure session directory with proper permissions
session_dir = Path(".mm")
session_dir.mkdir(mode=0o700, exist_ok=True)
session_file = session_dir / "session.pickle"


def is_auth_error(error: Exception) -> bool:
    """Determine if an error is a genuine authentication/authorization failure.

    Only returns True for actual auth failures like 401, 403, invalid credentials.
    Does NOT treat library errors, connection issues, or other problems as auth failures.
    """
    error_str = str(error).lower()

    # Exclude false positives first - these are NOT auth errors
    false_positives = [
        "connector",  # Library compatibility issue
        "aiohttp",  # Library issue
        "transport",  # Library issue
        "connection refused",  # Network issue, not auth
        "connection reset",  # Network issue, not auth
        "timeout",  # Network issue, not auth
    ]

    # Check for false positives first
    if any(fp in error_str for fp in false_positives):
        return False

    # Genuine authentication/authorization error indicators
    auth_indicators = [
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "invalid credentials",
        "bad credentials",
        "authentication failed",
        "auth failed",  # Match "auth failed" messages
        "not authenticated",
        "invalid token",
        "token expired",
        "session expired",
        "session has expired",
    ]

    # Check for genuine auth errors
    return any(indicator in error_str for indicator in auth_indicators)


def clear_session(reason: str = "unknown") -> None:
    """Clear session files and reset authentication state to allow fresh re-authentication.

    This function performs a complete authentication reset:
    - Clears session files from disk (.mm/session.pickle, .mm/mm_session.pickle)
    - Resets the client instance (mm_client = None)
    - Resets auth state to NOT_INITIALIZED
    - Clears auth errors and failure timestamps

    Only call when genuinely needed (e.g., after auth errors, on forced re-login).

    Args:
        reason: Why the session is being cleared (for logging/debugging)
    """
    global mm_client, auth_state, auth_error, auth_failed_at

    log.info("auth_reset", reason=reason, previous_state=auth_state.value)

    if mm_client is not None:
        mm_client = None

    auth_state = AuthState.NOT_INITIALIZED
    auth_error = None
    auth_failed_at = None

    # Clear session files
    for path in [session_file, session_dir / "mm_session.pickle"]:
        if path.exists():
            try:
                path.unlink()
                log.info("session_file_cleared", path=str(path))
            except Exception as e:
                log.warning("session_file_clear_failed", path=str(path), error=str(e))


async def api_call_with_retry(method_name: str, *args: Any, max_retries: int = 3, **kwargs: Any) -> Any:
    """Wrapper for API calls that handles session expiration and retries.

    Only clears sessions and re-authenticates for genuine auth errors.
    Other errors (network, library issues, etc.) are raised immediately.

    Args:
        method_name: Name of the method to call on mm_client (e.g., "get_accounts")
        *args: Positional arguments to pass to the method
        max_retries: Maximum number of retry attempts for auth failures (default: 3)
        **kwargs: Keyword arguments to pass to the method

    Returns:
        Result from the API method call

    Raises:
        ValueError: If mm_client is not initialized
        Exception: Re-raises any non-auth errors or auth errors after max_retries
    """
    global auth_state, mm_client

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):  # +1 for initial attempt
        try:
            # Get the method from the current mm_client instance
            if mm_client is None:
                raise ValueError("mm_client is not initialized")

            method = getattr(mm_client, method_name)
            return await method(*args, **kwargs)

        except Exception as e:
            last_error = e

            # Check if this is an auth error that should trigger retry
            if is_auth_error(e):
                if attempt < max_retries:
                    # Calculate exponential backoff delay (1s, 2s, 4s, ...)
                    backoff_delay = 2**attempt
                    log.warning(
                        "api_auth_error",
                        attempt=attempt + 1,
                        max_attempts=max_retries + 1,
                        error=str(e),
                        backoff_s=backoff_delay,
                    )

                    # clear_session() will reset auth state and error automatically
                    clear_session(reason=f"authentication failure during API call (attempt {attempt + 1})")

                    # Wait before retry (exponential backoff)
                    if backoff_delay > 0:
                        await asyncio.sleep(backoff_delay)

                    # Re-authenticate
                    await ensure_authenticated()
                    log.info("api_retry_after_reauth", attempt=attempt + 2, max_attempts=max_retries + 1)

                    # Continue to next iteration to retry with NEW mm_client
                    continue
                else:
                    # Max retries exhausted for auth error
                    log.error("api_auth_retries_exhausted", max_retries=max_retries, error=str(e))
                    raise
            else:
                # Not an auth error - raise immediately without retry
                raise

    # Should never reach here, but handle it defensively
    if last_error:
        raise last_error
    raise RuntimeError("api_call_with_retry completed without result or error")


async def initialize_client() -> None:
    """Initialize the MonarchMoney client with authentication.

    This function attempts to use cached sessions when possible and only
    performs fresh authentication when necessary. It does NOT validate
    sessions immediately - validation happens on first API call.
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

    log.info("auth_init", email=email)
    mm_client = MonarchMoney()

    # Try to load existing session first (unless forced to skip)
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

    # Perform fresh authentication
    max_retries = 2
    retry_delay = 3

    for attempt in range(max_retries):
        try:
            log.info("auth_login_attempt", attempt=attempt + 1, max_retries=max_retries, mfa=bool(mfa_secret))
            if mfa_secret:
                await mm_client.login(email, password, mfa_secret_key=mfa_secret, use_saved_session=False)
            else:
                await mm_client.login(email, password, use_saved_session=False)

            # Save session with stdout/stderr suppression
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
    """Ensure the client is authenticated, initializing on-demand if needed.

    This function uses a lock to prevent concurrent initialization attempts
    and returns immediately if already authenticated.

    Implements cooldown-based recovery from FAILED state: After a failure,
    waits AUTH_RETRY_COOLDOWN_SECONDS before allowing retry attempts.

    Call this at the start of every tool that needs the mm_client.
    """
    global mm_client, auth_state, auth_lock, auth_error, auth_failed_at

    # Initialize lock on first call (must be done in async context)
    if auth_lock is None:
        auth_lock = asyncio.Lock()

    # Fast path
    if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
        return

    log.info("auth_needed", state=auth_state.value)

    async with auth_lock:
        if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
            return

        # Cooldown recovery from FAILED state
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


# FastMCP Tool definitions using decorators


@mcp.tool(annotations=READONLY)
@track_usage
async def get_accounts() -> str:
    """Retrieve all linked financial accounts."""
    await ensure_authenticated()

    try:
        accounts = await api_call_with_retry("get_accounts")
        accounts = convert_dates_to_strings(accounts)
        return json.dumps(accounts, indent=2)
    except Exception as e:
        log.error("Failed to fetch accounts", error=str(e))
        raise


@mcp.tool(annotations=READONLY)
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
) -> str:
    """Fetch transactions with flexible date filtering and smart output formatting.

    Args:
        limit: Maximum number of transactions to return (default: 100, max: 1000)
        offset: Number of transactions to skip for pagination (default: 0)
        start_date: Filter transactions from this date onwards. Supports natural language like 'last month', 'yesterday', '30 days ago'
                    NOTE: If you provide start_date without end_date, end_date will auto-default to 'today'
        end_date: Filter transactions up to this date. Supports natural language
                  NOTE: If you provide end_date without start_date, start_date will auto-default to 'this month'
        account_id: Filter by specific account ID (converted to list internally)
        category_id: Filter by specific category ID (converted to list internally)
        tag_ids: Comma-separated tag IDs to filter by (e.g., "tag1,tag2")
        has_attachments: Filter to transactions with (True) or without (False) attachments
        has_notes: Filter to transactions with (True) or without (False) notes
        hidden_from_reports: Include hidden transactions (True), exclude them (False), or show all (None)
        is_split: Filter to split transactions only (True) or non-split (False)
        is_recurring: Filter to recurring transactions only (True) or non-recurring (False)
        verbose: Output format control (default: False)
            - False (compact mode): Returns essential fields only (~80% smaller)
                Fields included: id, date, amount, merchant, plaidName, category,
                                account, pending, needsReview, notes

            - True (verbose mode): Returns ALL fields including:
                Essential fields (same as compact) PLUS:
                • hideFromReports (bool)
                • reviewStatus (str: "needs_review" | "reviewed" | null)
                • isSplitTransaction (bool)
                • isRecurring (bool)
                • attachments (list of attachment objects)
                • tags (list of tag objects)
                • createdAt (ISO timestamp)
                • updatedAt (ISO timestamp)
                • __typename (GraphQL metadata)
                • Full nested objects with all their fields

            Use verbose=False for most queries to reduce token usage.
            Use verbose=True when you need: timestamps, split info, attachment details,
            or are updating transactions (need full context).

    Key Transaction Fields:
        Core Identifiers:
            - id: Unique transaction ID (required for updates)
            - date: Transaction date (YYYY-MM-DD format)
            - amount: Transaction amount (negative = expense, positive = income)

        Merchant Information:
            IMPORTANT: Monarch normalizes merchant names for cleaner UI
            - merchant.name: User-facing display name shown in Monarch UI (normalized/cleaned)
                Example: "Chipotle" for all Chipotle locations
            - plaidName: Original bank statement text from Plaid/institution (raw data)
                Example: "CHIPOTLE 4963", "CHIPOTLE MEX GR ONLINE", "CHIPOTLE 1879"
                Use this to see location numbers or original descriptors
            - Multiple transactions from different locations share the same merchant.name
            - Use plaidName to distinguish between specific locations/variants

        Categorization:
            - category.id: Category ID (for filtering/updates)
            - category.name: Category display name (e.g., "Restaurants & Bars")
            - tags: List of tag objects applied to transaction

        Account Info:
            - account.id: Account ID where transaction occurred
            - account.displayName: Account name (e.g., "Main Credit Card")

        Status Flags:
            - pending: True if transaction hasn't cleared yet
            - needsReview: True if flagged for user review
            - reviewStatus: "needs_review", "reviewed", or null
            - hideFromReports: True if hidden from budget/reports

        Transaction Types:
            - isSplitTransaction: True if split into multiple categories
            - isRecurring: True if part of a recurring series

        User Annotations:
            NOTE: These are different fields with different purposes
            - notes: Free-form user memo/annotation (e.g., "Business lunch with client")
            - merchant_name: The merchant's display name (e.g., "Olive Garden")
            - Both are editable, but serve different purposes in the UI
            - attachments: List of receipt/document attachments

        Metadata (verbose mode only):
            - createdAt: When transaction was first imported
            - updatedAt: Last modification timestamp
            - __typename: GraphQL type information

    Returns:
        JSON string containing transaction list

    Common Filter Examples:
        - Unreviewed transactions: has_notes=False, needs_review=True
        - Split transactions: is_split=True
        - Transactions with receipts: has_attachments=True
        - Manual transactions: synced_from_institution=False
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
        return json.dumps(transactions, indent=2)
    except Exception as e:
        log.error("Failed to fetch transactions", error=str(e), limit=limit, start_date=start_date)
        raise


@mcp.tool(annotations=READONLY)
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
) -> str:
    """Search transactions by text using Monarch Money's built-in search.

    Searches merchant names, descriptions, notes, and other fields.
    Accepts all the same filters as get_transactions plus a search query.
    Returns compact results by default (use verbose=True for full details).

    Args:
        query: Search term to find in transactions
        limit: Maximum transactions to return (default: 500, max: 1000)
        offset: Number of transactions to skip for pagination
        start_date: Filter from this date (supports natural language like 'last month')
        end_date: Filter to this date (supports natural language)
        account_id: Filter by specific account ID
        category_id: Filter by specific category ID
        tag_ids: Comma-separated tag IDs to filter by
        has_attachments: Filter by attachment presence
        has_notes: Filter by notes presence
        hidden_from_reports: Filter by report visibility
        is_split: Filter split transactions
        is_recurring: Filter recurring transactions
        verbose: False=compact fields, True=all fields

    Returns:
        JSON with search_metadata and matching transactions
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

        result = {
            "search_metadata": {
                "query": query_str,
                "result_count": len(transactions),
                "filters_applied": {k: v for k, v in filters.items() if k != "search"},
            },
            "transactions": transactions,
        }

        log.info("Search complete", query=query_str, result_count=len(transactions))
        return json.dumps(result, indent=2)

    except Exception as e:
        log.error("Failed to search transactions", error=str(e), query=query)
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_budgets(start_date: str | None = None, end_date: str | None = None) -> str:
    """Retrieve budget information with flexible date filtering.

    Args:
        start_date: Filter budgets from this date onwards. Supports natural language like 'last month', 'this year'
        end_date: Filter budgets up to this date. Supports natural language

    Returns:
        JSON string containing budget information
    """
    await ensure_authenticated()

    # Use build_date_filter for consistent natural language date support
    kwargs = build_date_filter(start_date, end_date)

    try:
        budgets = await api_call_with_retry("get_budgets", **kwargs)  # type: ignore[arg-type]
        budgets = convert_dates_to_strings(budgets)
        return json.dumps(budgets, indent=2)
    except Exception as e:
        # Handle the case where no budgets exist
        if "Something went wrong while processing: None" in str(e):
            return json.dumps(
                {"budgets": [], "message": "No budgets configured in your Monarch Money account"}, indent=2
            )
        else:
            # Re-raise other errors
            raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_cashflow(start_date: str | None = None, end_date: str | None = None) -> str:
    """Analyze cashflow data with flexible date filtering.

    Args:
        start_date: Filter cashflow from this date onwards. Supports natural language like 'last month', 'this year'
        end_date: Filter cashflow up to this date. Supports natural language

    Returns:
        JSON string containing cashflow analysis
    """
    await ensure_authenticated()

    # Use build_date_filter for consistent natural language date support
    kwargs = build_date_filter(start_date, end_date)

    cashflow = await api_call_with_retry("get_cashflow", **kwargs)  # type: ignore[arg-type]
    cashflow = convert_dates_to_strings(cashflow)
    return json.dumps(cashflow, indent=2)


@mcp.tool(annotations=READONLY)
@track_usage
async def get_transaction_categories(verbose: bool = False) -> str:
    """List all transaction categories.

    Args:
        verbose: Output format control (default: False)
            - False: Returns compact format with just {id, name} per category (~80% smaller).
                     Ideal for category lookups when mapping names to IDs.
            - True: Returns full category details including group, order, timestamps, system flags.

    Returns:
        JSON string containing category list
    """
    await ensure_authenticated()

    categories = await api_call_with_retry("get_transaction_categories")
    categories = convert_dates_to_strings(categories)

    if not verbose and isinstance(categories, list):
        categories = [{"id": cat.get("id"), "name": cat.get("name")} for cat in categories if isinstance(cat, dict)]

    return json.dumps(categories, indent=2)


@mcp.tool(annotations=WRITE_CREATE)
@track_usage
async def create_transaction(
    amount: float,
    merchant_name: str,
    account_id: str,
    date: str,
    category_id: str,
    notes: str | None = None,
    update_balance: bool = False,
) -> str:
    """Create a new manual transaction.

    Args:
        amount: Transaction amount (positive for income, negative for expense)
        merchant_name: Name of the merchant/payee (e.g., "Starbucks", "Monthly Rent")
        account_id: ID of the account for this transaction
        date: Transaction date in YYYY-MM-DD format
        category_id: ID of the category to assign (required for new transactions)
        notes: Optional notes/memo for this transaction
        update_balance: Whether to update account balance when creating this transaction (default: False)
            - False: Transaction is recorded but doesn't affect account balance (typical for synced accounts)
            - True: Adjusts account balance by transaction amount (useful for manual accounts)

    Returns:
        JSON string with created transaction details
    """
    await ensure_authenticated()

    try:
        # Validate required fields
        if not merchant_name or merchant_name.strip() == "":
            raise ValueError("merchant_name cannot be empty")
        if not category_id:
            raise ValueError("category_id is required when creating transactions")

        # Convert date string to ISO format string (API expects YYYY-MM-DD)
        try:
            transaction_date = datetime.strptime(date, "%Y-%m-%d").date()
            date_str = transaction_date.isoformat()
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD (e.g., 2024-01-15). Error: {e}") from e

        log.info("creating_transaction", merchant=merchant_name, amount=amount, date=date_str)

        # Use api_call_with_retry for session expiration handling and add timeout
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
            timeout=30.0,  # 30 second timeout
        )
        result = convert_dates_to_strings(result)
        return json.dumps(result, indent=2)
    except asyncio.TimeoutError as e:
        log.error("create_transaction_timeout")
        raise ValueError("Transaction creation timed out after 30 seconds. Please try again.") from e
    except ValueError:
        raise
    except Exception as e:
        log.error("create_transaction_failed", error=str(e))
        raise


@mcp.tool(annotations=WRITE_IDEMPOTENT)
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
) -> str:
    """Update an existing transaction.

    Args:
        transaction_id: ID of the transaction to update (required)
        amount: New transaction amount
        merchant_name: New merchant display name shown in Monarch UI
            - This updates the user-facing name (merchant.name field)
            - Does NOT change plaidName (original bank statement text, read-only)
            - Empty strings are ignored by the API
            - Example: Change "AMZN Mktp US" to "Amazon"
        category_id: ID of the new category to assign
        date: New transaction date in YYYY-MM-DD format
        notes: User notes/memo for this transaction (separate from merchant name)
            NOTE: This is different from merchant_name
            - notes: Free-form user memo/annotation (e.g., "Business lunch with client")
            - merchant_name: The merchant's display name (e.g., "Olive Garden")
            - Both are editable, but serve different purposes in the UI
            - Use empty string "" to clear existing notes
        goal_id: ID of savings goal to associate with this transaction
            - Use empty string "" to clear goal association
        hide_from_reports: Whether to hide this transaction from reports/analytics
        needs_review: Flag transaction as needing review

    Field Editability:
        Editable Fields (can be updated):
            - amount: Transaction amount
            - merchant_name: User-facing merchant display name
            - category_id: Category assignment
            - date: Transaction date
            - notes: User memo/notes
            - goal_id: Goal association
            - hide_from_reports: Visibility in reports
            - needs_review: Review flag

        Read-Only Fields (cannot be updated):
            - id: Transaction ID (immutable)
            - plaidName: Original bank statement text (from institution)
            - account: Account where transaction occurred
            - pending: Pending status (controlled by institution)
            - createdAt: Creation timestamp
            - isSplitTransaction: Split status (use separate split API)
            - attachments: Use separate attachment API

    Returns:
        JSON string with updated transaction details

    Common Use Cases:
        - Change merchant: merchant_name="Starbucks"
        - Add note: notes="Business expense"
        - Recategorize: category_id="cat_groceries_123"
        - Mark for review: needs_review=True
        - Clear notes: notes=""
    """
    await ensure_authenticated()

    try:
        # Validate parameters before API call
        if merchant_name is not None and merchant_name.strip() == "":
            log.warning("empty_merchant_name_ignored")

        # Build update parameters
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

        # Log what we're updating (for debugging)
        update_fields = [k for k in updates if k != "transaction_id"]
        log.info("updating_transaction", transaction_id=transaction_id, fields=update_fields)

        # Use api_call_with_retry for session expiration handling and add timeout
        result = await asyncio.wait_for(
            api_call_with_retry("update_transaction", **updates),
            timeout=30.0,  # 30 second timeout
        )
        result = convert_dates_to_strings(result)
        return json.dumps(result, indent=2)
    except asyncio.TimeoutError as e:
        log.error("update_transaction_timeout", transaction_id=transaction_id)
        raise ValueError("Transaction update timed out after 30 seconds. Please try again.") from e
    except ValueError as e:
        # Enhanced error messages for validation failures
        error_msg = str(e)
        if "date" in error_msg.lower():
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD (e.g., 2024-01-15). Error: {e}") from e
        raise
    except Exception as e:
        log.error("update_transaction_failed", transaction_id=transaction_id, error=str(e))
        raise


@mcp.tool(annotations=WRITE_IDEMPOTENT)
@track_usage
async def update_transactions_bulk(updates: str) -> str:
    """Update multiple transactions in a single call to save round-trips.

    This is much more efficient than calling update_transaction multiple times.
    Updates are executed in parallel for maximum performance.

    Args:
        updates: JSON string containing list of transaction updates. Each update should have:
            - transaction_id (required): ID of transaction to update
            - amount (optional): New amount
            - merchant_name (optional): New merchant display name
            - category_id (optional): New category ID
            - date (optional): New date in YYYY-MM-DD format
            - notes (optional): New notes
            - goal_id (optional): Goal ID or empty string to clear
            - hide_from_reports (optional): Boolean visibility flag
            - needs_review (optional): Boolean review flag

    Example:
        [
            {"transaction_id": "123", "category_id": "cat_456", "notes": "Updated"},
            {"transaction_id": "789", "merchant_name": "Starbucks", "needs_review": false}
        ]

    Returns:
        JSON with results for each transaction including successes and any failures
    """
    await ensure_authenticated()

    try:
        # Parse the updates JSON
        try:
            updates_list = json.loads(updates)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in updates parameter: {e}") from e

        if not isinstance(updates_list, list):
            raise ValueError("updates parameter must be a JSON array of transaction updates")

        if len(updates_list) == 0:
            return json.dumps({"message": "No updates provided", "results": []}, indent=2)

        log.info("bulk_update_start", count=len(updates_list))

        # Build list of update tasks
        async def update_single(update_data: dict[str, Any]) -> dict[str, Any]:
            """Update a single transaction and return result with transaction_id."""
            try:
                if not isinstance(update_data, dict):
                    return {"transaction_id": None, "status": "error", "error": "Update must be a dictionary"}

                if "transaction_id" not in update_data:
                    return {"transaction_id": None, "status": "error", "error": "transaction_id is required"}

                txn_id = update_data["transaction_id"]

                # Build update parameters
                update_params: dict[str, Any] = {"transaction_id": txn_id}

                if "amount" in update_data:
                    update_params["amount"] = float(update_data["amount"])
                if "merchant_name" in update_data:
                    update_params["merchant_name"] = str(update_data["merchant_name"])
                if "category_id" in update_data:
                    update_params["category_id"] = str(update_data["category_id"])
                if "date" in update_data:
                    date_str = str(update_data["date"])
                    update_params["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
                if "notes" in update_data:
                    update_params["notes"] = str(update_data["notes"])
                if "goal_id" in update_data:
                    update_params["goal_id"] = str(update_data["goal_id"])
                if "hide_from_reports" in update_data:
                    update_params["hide_from_reports"] = bool(update_data["hide_from_reports"])
                if "needs_review" in update_data:
                    update_params["needs_review"] = bool(update_data["needs_review"])

                # Execute update with timeout
                await asyncio.wait_for(api_call_with_retry("update_transaction", **update_params), timeout=30.0)

                # Compact success response: just confirm the update succeeded
                return {"transaction_id": txn_id, "status": "success"}

            except asyncio.TimeoutError:
                return {
                    "transaction_id": update_data.get("transaction_id"),
                    "status": "error",
                    "error": "Update timed out after 30 seconds",
                }
            except Exception as e:
                return {"transaction_id": update_data.get("transaction_id"), "status": "error", "error": str(e)}

        # Execute all updates in parallel
        results = await asyncio.gather(
            *[update_single(update_data) for update_data in updates_list],
            return_exceptions=False,  # We handle exceptions in update_single
        )

        # Count successes and failures
        success_count = sum(1 for r in results if r["status"] == "success")
        failure_count = len(results) - success_count

        log.info("bulk_update_complete", succeeded=success_count, failed=failure_count)

        response = {
            "summary": {"total": len(results), "succeeded": success_count, "failed": failure_count},
            "results": results,
        }

        return json.dumps(response, indent=2)

    except Exception as e:
        log.error("bulk_update_failed", error=str(e))
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_account_holdings() -> str:
    """Get investment portfolio data from brokerage accounts."""
    await ensure_authenticated()

    try:
        holdings = await api_call_with_retry("get_account_holdings")
        holdings = convert_dates_to_strings(holdings)
        return json.dumps(holdings, indent=2)
    except Exception as e:
        log.error("Failed to fetch account holdings", error=str(e))
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_account_history(account_id: str, start_date: str | None = None, end_date: str | None = None) -> str:
    """Get historical account balance data."""
    await ensure_authenticated()

    kwargs: dict[str, Any] = {"account_id": account_id}
    if start_date:
        kwargs["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        kwargs["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()

    try:
        history = await api_call_with_retry("get_account_history", **kwargs)
        history = convert_dates_to_strings(history)
        return json.dumps(history, indent=2)
    except Exception as e:
        log.error("Failed to fetch account history", error=str(e), account_id=account_id)
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_institutions() -> str:
    """Get linked financial institutions."""
    await ensure_authenticated()

    try:
        institutions = await api_call_with_retry("get_institutions")
        institutions = convert_dates_to_strings(institutions)
        return json.dumps(institutions, indent=2)
    except Exception as e:
        log.error("Failed to fetch institutions", error=str(e))
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_recurring_transactions() -> str:
    """Get scheduled recurring transactions."""
    await ensure_authenticated()

    try:
        recurring = await api_call_with_retry("get_recurring_transactions")
        recurring = convert_dates_to_strings(recurring)
        return json.dumps(recurring, indent=2)
    except Exception as e:
        log.error("Failed to fetch recurring transactions", error=str(e))
        raise


@mcp.tool(annotations=WRITE_IDEMPOTENT)
@track_usage
async def set_budget_amount(category_id: str, amount: float) -> str:
    """Set budget amount for a category."""
    await ensure_authenticated()

    try:
        result = await api_call_with_retry("set_budget_amount", category_id=category_id, amount=amount)
        result = convert_dates_to_strings(result)
        log.info("Budget amount updated", category_id=category_id, amount=amount)
        return json.dumps(result, indent=2)
    except Exception as e:
        log.error("Failed to set budget amount", error=str(e), category_id=category_id)
        raise


@mcp.tool(annotations=WRITE_CREATE)
@track_usage
async def create_manual_account(account_name: str, account_type: str, balance: float) -> str:
    """Create a manually tracked account."""
    await ensure_authenticated()

    try:
        result = await api_call_with_retry(
            "create_manual_account", account_name=account_name, account_type=account_type, balance=balance
        )
        result = convert_dates_to_strings(result)
        log.info("Manual account created", name=account_name, type=account_type)
        return json.dumps(result, indent=2)
    except Exception as e:
        log.error("Failed to create manual account", error=str(e), name=account_name)
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_spending_summary(
    start_date: str | None = None, end_date: str | None = None, group_by: str = "category"
) -> str:
    """Get intelligent spending summary with aggregations.

    Args:
        start_date: Start date (supports natural language like 'last month')
        end_date: End date (supports natural language)
        group_by: Group spending by 'category', 'account', or 'month'
    """
    await ensure_authenticated()

    try:
        log.info("Generating spending summary", start_date=start_date, end_date=end_date, group_by=group_by)

        # Get transactions for the period
        filters = build_date_filter(start_date, end_date)
        response = await api_call_with_retry("get_transactions", limit=1000, **filters)  # type: ignore[arg-type]
        # Extract transactions list from nested response structure
        transactions = extract_transactions_list(response)

        # Aggregate spending data
        summary: dict[str, Any] = {
            "period": {"start": start_date, "end": end_date},
            "groups": {},
            "totals": {"income": 0, "expenses": 0, "net": 0},
        }

        for txn in transactions:
            amount = float(txn.get("amount", 0))

            # Track totals
            totals: dict[str, float] = summary["totals"]
            if amount > 0:
                totals["income"] += amount
            else:
                totals["expenses"] += abs(amount)

            # Group by specified field
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

        # Sort groups by total spending (expenses)
        sorted_groups = dict(sorted(summary["groups"].items(), key=lambda x: x[1]["expenses"], reverse=True))
        summary["groups"] = sorted_groups

        log.info(
            "Spending summary generated",
            total_transactions=len(transactions),
            groups_count=len(summary["groups"]),
            net_amount=summary["totals"]["net"],
        )

        return json.dumps(summary, indent=2)

    except Exception as e:
        log.error("Failed to generate spending summary", error=str(e))
        raise


@mcp.tool(annotations=WRITE_SIDE_EFFECT)
@track_usage
async def refresh_accounts() -> str:
    """Request a refresh of all account data from financial institutions."""
    await ensure_authenticated()

    try:
        result = await api_call_with_retry("request_accounts_refresh")
        result = convert_dates_to_strings(result)
        log.info("Account refresh requested")
        return json.dumps(result, indent=2)
    except Exception as e:
        log.error("Failed to refresh accounts", error=str(e))
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def get_complete_financial_overview(period: str = "this month") -> str:
    """Get complete financial overview in a single call - accounts, transactions, budgets, cashflow.

    This intelligent batch tool combines multiple API calls to provide comprehensive financial analysis,
    reducing round-trips and providing deeper insights.

    Args:
        period: Time period for analysis ("this month", "last month", "this year", etc.)
    """
    await ensure_authenticated()

    try:
        # Parse the period into date filters
        filters = build_date_filter(period, None)

        # Execute all API calls in parallel for maximum efficiency
        accounts_task = api_call_with_retry("get_accounts")
        budgets_task = api_call_with_retry("get_budgets", **filters)  # type: ignore[arg-type]
        cashflow_task = api_call_with_retry("get_cashflow", **filters)  # type: ignore[arg-type]
        transactions_task = api_call_with_retry("get_transactions", limit=500, **filters)  # type: ignore[arg-type]
        categories_task = api_call_with_retry("get_transaction_categories")

        # Wait for all results
        api_results = await asyncio.gather(
            accounts_task, budgets_task, cashflow_task, transactions_task, categories_task, return_exceptions=True
        )
        accounts, budgets, cashflow, transactions, categories = api_results

        # Handle any exceptions gracefully
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
            # Extract transactions list from nested response structure
            transactions_list = extract_transactions_list(transactions)
            results["transactions"] = convert_dates_to_strings(transactions_list)
            # Add intelligent transaction analysis
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

        # Add metadata about the batch operation
        results["_batch_metadata"] = {
            "period": period,
            "filters_applied": convert_dates_to_strings(filters),
            "api_calls_made": 5,
            "timestamp": datetime.now().isoformat(),
        }

        accounts_val = results.get("accounts", [])
        log.info(
            "Complete financial overview generated",
            period=period,
            accounts_count=len(accounts_val) if isinstance(accounts_val, list) else 0,
            transactions_count=results.get("transaction_summary", {}).get("total_count", 0),
        )

        return json.dumps(results, indent=2)

    except Exception as e:
        log.error("Failed to generate financial overview", error=str(e), period=period)
        raise


@mcp.tool(annotations=READONLY)
@track_usage
async def analyze_spending_patterns(lookback_months: int = 6, include_forecasting: bool = True) -> str:
    """Intelligent spending pattern analysis with trend forecasting.

    Combines multiple data sources to provide deep spending insights including:
    - Monthly spending trends by category
    - Account usage patterns
    - Budget performance analysis
    - Predictive spending forecasts

    Args:
        lookback_months: Number of months to analyze (default 6)
        include_forecasting: Whether to include spending forecasts
    """
    await ensure_authenticated()

    try:
        # Calculate date ranges for analysis
        end_date = datetime.now().date()
        start_date = end_date - relativedelta(months=lookback_months)

        # Batch API calls for comprehensive data
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
            # Extract transactions list from nested response structure
            transactions_list = extract_transactions_list(transactions)

            # Monthly spending trends
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

                # Monthly trends (YYYY-MM)
                month_key = txn_date[:7] if len(txn_date) >= 7 else "Unknown"
                if month_key not in monthly_data:
                    monthly_data[month_key] = {"income": 0.0, "expenses": 0.0, "net": 0.0, "transaction_count": 0.0}

                if amount > 0:
                    monthly_data[month_key]["income"] += amount
                else:
                    monthly_data[month_key]["expenses"] += abs(amount)
                monthly_data[month_key]["net"] += amount
                monthly_data[month_key]["transaction_count"] += 1

                # Category analysis
                if category_name not in category_totals:
                    category_totals[category_name] = {"total": 0.0, "transactions": 0.0, "avg_amount": 0.0}
                category_totals[category_name]["total"] += abs(amount) if amount < 0 else 0.0  # Only expenses
                category_totals[category_name]["transactions"] += 1

                # Account usage
                if account_name not in account_usage:
                    account_usage[account_name] = {"total_volume": 0.0, "transactions": 0.0}
                account_usage[account_name]["total_volume"] += abs(amount)
                account_usage[account_name]["transactions"] += 1

            # Calculate averages and sort data
            for category in category_totals:
                if category_totals[category]["transactions"] > 0:
                    category_totals[category]["avg_amount"] = (
                        category_totals[category]["total"] / category_totals[category]["transactions"]
                    )

            analysis["monthly_trends"] = dict(sorted(monthly_data.items()))
            analysis["category_analysis"] = dict(
                sorted(category_totals.items(), key=lambda x: x[1]["total"], reverse=True)  # type: ignore[call-overload, index]
            )
            analysis["account_usage"] = dict(
                sorted(account_usage.items(), key=lambda x: x[1]["total_volume"], reverse=True)  # type: ignore[call-overload, index]
            )

            # Simple forecasting if requested
            if include_forecasting and monthly_data:
                recent_months = list(monthly_data.values())[-3:]  # Last 3 months
                if recent_months:
                    avg_monthly_expenses = sum(m["expenses"] for m in recent_months) / len(recent_months)
                    avg_monthly_income = sum(m["income"] for m in recent_months) / len(recent_months)

                    next_month = (end_date + relativedelta(months=1)).strftime("%Y-%m")
                    analysis["forecast"] = {
                        "next_month": next_month,
                        "predicted_expenses": round(avg_monthly_expenses, 2),
                        "predicted_income": round(avg_monthly_income, 2),
                        "predicted_net": round(avg_monthly_income - avg_monthly_expenses, 2),
                        "confidence": "medium",  # Based on 3-month average
                        "note": "Forecast based on 3-month spending average",
                    }

        if not isinstance(budgets, Exception):
            analysis["budget_performance"] = convert_dates_to_strings(budgets)

        # Add metadata
        txn_count = len(transactions_list) if not isinstance(transactions, Exception) else 0
        analysis["_metadata"] = {
            "api_calls_made": 4,
            "total_transactions_analyzed": txn_count,
            "analysis_timestamp": datetime.now().isoformat(),
        }

        log.info(
            "Spending pattern analysis completed",
            lookback_months=lookback_months,
            transactions_analyzed=txn_count,
            include_forecasting=include_forecasting,
        )

        return json.dumps(analysis, indent=2)

    except Exception as e:
        log.error("Failed to analyze spending patterns", error=str(e), lookback_months=lookback_months)
        raise


async def main() -> None:
    """Main entry point for the server.

    The server starts immediately without authentication. Authentication
    happens lazily on the first tool call via ensure_authenticated().
    """
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


if __name__ == "__main__":

    def signal_handler(signum: int, frame: Any) -> None:
        log.info("signal_received", signum=signum)
        # Let asyncio handle the shutdown

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

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
