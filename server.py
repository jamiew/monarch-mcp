#!/usr/bin/env python3
"""MonarchMoney MCP Server - Provides access to Monarch Money financial data via MCP protocol."""

import os
import asyncio
import json
import uuid
from typing import Optional, List, Union, Dict, Any
from datetime import datetime, date
from pathlib import Path

import structlog
from dateutil import parser as date_parser
from pydantic import BaseModel, Field, ValidationError
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from monarchmoney import MonarchMoney, RequireMFAException

# Type definitions for Monarch Money API responses
JsonSerializable = Union[str, int, float, bool, None, List['JsonSerializable'], Dict[str, 'JsonSerializable']]
DateConvertible = Union[date, datetime, JsonSerializable]

# Pydantic models for tool arguments (replacing Dict[str, Any])
class GetTransactionsArgs(BaseModel):  # type: ignore[misc]
    """Arguments for get_transactions tool."""
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    start_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    account_id: Optional[str] = None
    category_id: Optional[str] = None
    verbose: bool = Field(default=False)

class GetBudgetsArgs(BaseModel):  # type: ignore[misc]
    """Arguments for get_budgets tool."""
    start_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')

class GetCashflowArgs(BaseModel):  # type: ignore[misc]
    """Arguments for get_cashflow tool."""
    start_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')

class CreateTransactionArgs(BaseModel):  # type: ignore[misc]
    """Arguments for create_transaction tool."""
    amount: float
    description: str = Field(min_length=1)
    category_id: Optional[str] = None
    account_id: str
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    notes: Optional[str] = None

class UpdateTransactionArgs(BaseModel):  # type: ignore[misc]
    """Arguments for update_transaction tool."""
    transaction_id: str
    amount: Optional[float] = None
    description: Optional[str] = Field(default=None, min_length=1)
    category_id: Optional[str] = None
    date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    notes: Optional[str] = None


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
        from datetime import timedelta
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
        from datetime import timedelta
        return today - timedelta(days=7)
    elif date_input == "this week":
        from datetime import timedelta
        # Start of this week (Monday)
        days_since_monday = today.weekday()
        return today - timedelta(days=days_since_monday)
    
    # Handle relative patterns like "30 days ago", "6 months ago"
    import re
    relative_pattern = re.match(r'(\d+)\s+(days?|weeks?|months?|years?)\s+ago', date_input)
    if relative_pattern:
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta
        
        amount = int(relative_pattern.group(1))
        unit = relative_pattern.group(2).rstrip('s')  # Remove plural 's'
        
        try:
            if unit == 'day':
                return today - timedelta(days=amount)
            elif unit == 'week':
                return today - timedelta(weeks=amount)
            elif unit == 'month':
                result = today - relativedelta(months=amount)
                return result.date() if hasattr(result, 'date') else result
            elif unit == 'year':
                result = today - relativedelta(years=amount)
                return result.date() if hasattr(result, 'date') else result
        except (ValueError, OverflowError) as e:
            log.warning("Invalid relative date calculation", input=date_input, amount=amount, unit=unit, error=str(e))
            raise ValueError(f"Invalid relative date: {date_input}")
    
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
            "Or relative: 30 days ago, 6 months ago, 1 year ago"
        ]
        suggestion_text = ". ".join(suggestions)
        raise ValueError(f"Could not parse date '{date_input}'. {suggestion_text}")


def build_date_filter(start_date: Optional[str], end_date: Optional[str]) -> Dict[str, str]:
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
    filters: Dict[str, str] = {}

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
                log.info("Auto-filling missing start_date with first of end_date's month",
                        end_date=end_date, calculated_start=start_date)
            else:
                # End date is this month, use "this month"
                start_date = "this month"
                log.info("Auto-filling missing start_date with 'this month'", end_date=end_date)
        except ValueError:
            # If we can't parse end_date yet, just use "this month" and let validation catch issues later
            start_date = "this month"
            log.info("Auto-filling missing start_date with 'this month' (end_date parse pending)", end_date=end_date)

    if start_date:
        try:
            parsed_date = parse_flexible_date(start_date)
            filters["start_date"] = parsed_date.isoformat()
            log.info("Successfully parsed start_date", input=start_date, parsed=parsed_date.isoformat())
        except ValueError as e:
            log.warning("Flexible date parsing failed for start_date", input=start_date, error=str(e))
            
            # Fallback 1: Try strict ISO format parsing
            try:
                parsed_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                filters["start_date"] = parsed_date.isoformat()
                log.info("Successfully parsed start_date with ISO fallback", input=start_date, parsed=parsed_date.isoformat())
            except ValueError:
                # Fallback 2: Try common date formats
                common_formats = [
                    "%m/%d/%Y",     # MM/DD/YYYY
                    "%d/%m/%Y",     # DD/MM/YYYY
                    "%Y/%m/%d",     # YYYY/MM/DD
                    "%m-%d-%Y",     # MM-DD-YYYY
                    "%d-%m-%Y",     # DD-MM-YYYY
                    "%B %d, %Y",    # January 1, 2024
                    "%b %d, %Y",    # Jan 1, 2024
                    "%d %B %Y",     # 1 January 2024
                    "%d %b %Y",     # 1 Jan 2024
                ]
                
                parsed = False
                for fmt in common_formats:
                    try:
                        parsed_date = datetime.strptime(start_date, fmt).date()
                        filters["start_date"] = parsed_date.isoformat()
                        log.info("Successfully parsed start_date with format fallback", 
                                input=start_date, format=fmt, parsed=parsed_date.isoformat())
                        parsed = True
                        break
                    except ValueError:
                        continue
                
                if not parsed:
                    log.error("All date parsing attempts failed for start_date", input=start_date)
                    raise ValueError(f"Could not parse start_date '{start_date}'. {str(e)}")
    
    if end_date:
        try:
            parsed_date = parse_flexible_date(end_date)
            filters["end_date"] = parsed_date.isoformat()
            log.info("Successfully parsed end_date", input=end_date, parsed=parsed_date.isoformat())
        except ValueError as e:
            log.warning("Flexible date parsing failed for end_date", input=end_date, error=str(e))
            
            # Fallback 1: Try strict ISO format parsing
            try:
                parsed_date = datetime.strptime(end_date, "%Y-%m-%d").date()
                filters["end_date"] = parsed_date.isoformat()
                log.info("Successfully parsed end_date with ISO fallback", input=end_date, parsed=parsed_date.isoformat())
            except ValueError:
                # Fallback 2: Try common date formats
                common_formats = [
                    "%m/%d/%Y",     # MM/DD/YYYY
                    "%d/%m/%Y",     # DD/MM/YYYY
                    "%Y/%m/%d",     # YYYY/MM/DD
                    "%m-%d-%Y",     # MM-DD-YYYY
                    "%d-%m-%Y",     # DD-MM-YYYY
                    "%B %d, %Y",    # January 1, 2024
                    "%b %d, %Y",    # Jan 1, 2024
                    "%d %B %Y",     # 1 January 2024
                    "%d %b %Y",     # 1 Jan 2024
                ]
                
                parsed = False
                for fmt in common_formats:
                    try:
                        parsed_date = datetime.strptime(end_date, fmt).date()
                        filters["end_date"] = parsed_date.isoformat()
                        log.info("Successfully parsed end_date with format fallback", 
                                input=end_date, format=fmt, parsed=parsed_date.isoformat())
                        parsed = True
                        break
                    except ValueError:
                        continue
                
                if not parsed:
                    log.error("All date parsing attempts failed for end_date", input=end_date)
                    raise ValueError(f"Could not parse end_date '{end_date}'. {str(e)}")
    
    # Validate date range logic
    if "start_date" in filters and "end_date" in filters:
        start = date.fromisoformat(filters["start_date"])
        end = date.fromisoformat(filters["end_date"])
        
        if start > end:
            log.warning("Start date is after end date", start_date=filters["start_date"], end_date=filters["end_date"])
            raise ValueError(f"Start date ({filters['start_date']}) cannot be after end date ({filters['end_date']})")
    
    return filters


def convert_dates_to_strings(obj: DateConvertible) -> JsonSerializable:
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
        return tuple(convert_dates_to_strings(item) for item in obj)  # type: ignore[unreachable]
    else:
        return obj


def extract_transactions_list(response: Any) -> List[Dict[str, Any]]:
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
        logger.warning(f"Unexpected transaction response structure: {list(response.keys())}")
        return []
    else:
        logger.error(f"Unexpected transaction response type: {type(response)}")
        return []


def format_transactions_compact(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format transactions in a compact format with only essential fields.

    Returns simplified transaction objects with only:
    - id, date, amount
    - merchant name, plaidName (original statement name)
    - category name
    - account display name
    - Basic flags: pending, needsReview

    Use verbose=True to get full transaction details when needed.
    """
    compact: List[Dict[str, Any]] = []

    for txn in transactions:
        if not isinstance(txn, dict):
            continue

        compact_txn: Dict[str, Any] = {
            "id": txn.get("id"),
            "date": txn.get("date"),
            "amount": txn.get("amount"),
            "merchant": txn.get("merchant", {}).get("name") if isinstance(txn.get("merchant"), dict) else None,
            "plaidName": txn.get("plaidName"),
            "category": txn.get("category", {}).get("name") if isinstance(txn.get("category"), dict) else None,
            "account": txn.get("account", {}).get("displayName") if isinstance(txn.get("account"), dict) else None,
            "pending": txn.get("pending", False),
            "needsReview": txn.get("needsReview", False)
        }

        # Include notes if present
        if txn.get("notes"):
            compact_txn["notes"] = txn.get("notes")

        compact.append(compact_txn)

    return compact

# Configure standard logging (stderr only to avoid interfering with MCP stdio)
import sys
from pathlib import Path
import logging

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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[SafeStreamHandler(sys.stderr)]
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
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# Get structured logger for this module
log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Suppress third-party library logging to reduce noise
logging.getLogger('aiohttp').setLevel(logging.ERROR)
logging.getLogger('monarchmoney').setLevel(logging.ERROR)
logging.getLogger('gql').setLevel(logging.ERROR)
logging.getLogger('gql.transport').setLevel(logging.ERROR)

# Suppress SSL warnings that might leak to stdout
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="gql.transport.aiohttp")

# Usage analytics tracking
import functools
import time
from typing import Dict, List, Any
import uuid

# Session tracking for usage analytics
current_session_id = str(uuid.uuid4())
usage_patterns: Dict[str, List[Dict[str, Any]]] = {}

def track_usage(func: Any) -> Any:
    """Decorator to track tool usage patterns for analytics with detailed debugging."""
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.time()
        tool_name = func.__name__

        # Format args for logging (exclude sensitive data)
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in ['password', 'mfa_secret']}

        # Log tool call with arguments BEFORE execution
        logger.info(f"[TOOL_CALL] {tool_name} | args: {safe_kwargs}")

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
                if isinstance(result, str) and result.strip().startswith('{'):
                    import json
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        # Look for common list fields to count items
                        for key in ['transactions', 'accounts', 'budgets', 'categories', 'results']:
                            if key in parsed and isinstance(parsed[key], list):
                                extra_stats += f" | {key}: {len(parsed[key])} items"
                        # Check for batch summaries
                        if 'batch_summary' in parsed:
                            summary = parsed['batch_summary']
                            if isinstance(summary, dict):
                                extra_stats += f" | batch: {summary}"
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

            call_info.update({
                "status": "success",
                "execution_time": execution_time,
                "result_size": result_chars
            })

            # Log for analytics with detailed size info
            logger.info(f"[ANALYTICS] tool_called: {tool_name} | time: {execution_time:.3f}s | status: success")
            logger.info(f"[RESULT_SIZE] {tool_name} | chars: {result_chars:,} | size: {result_kb:.2f} KB{extra_stats}")

            # Track usage patterns in memory for batching analysis
            if tool_name not in usage_patterns:
                usage_patterns[tool_name] = []
            usage_patterns[tool_name].append(call_info)

            return result

        except Exception as e:
            execution_time = time.time() - start_time
            call_info.update({
                "status": "error",
                "execution_time": execution_time,
                "error": str(e)
            })

            logger.error(f"[ANALYTICS] tool_error: {tool_name} | time: {execution_time:.3f}s | error: {str(e)}")
            raise

    return wrapper

# Initialize the FastMCP server
mcp = FastMCP("monarch-money")

# Authentication state management
from enum import Enum

class AuthState(Enum):
    """Track authentication state to prevent duplicate initialization attempts."""
    NOT_INITIALIZED = "not_initialized"
    INITIALIZING = "initializing"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"

# Global variables for authentication
mm_client: Optional[MonarchMoney] = None
auth_state: AuthState = AuthState.NOT_INITIALIZED
auth_lock: Optional[asyncio.Lock] = None  # Created in async context
auth_error: Optional[str] = None  # Store last auth error for debugging

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
        "aiohttp",    # Library issue
        "transport",  # Library issue
        "connection refused", # Network issue, not auth
        "connection reset",   # Network issue, not auth
        "timeout",    # Network issue, not auth
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
        "session has expired"
    ]

    # Check for genuine auth errors
    return any(indicator in error_str for indicator in auth_indicators)


def clear_session(reason: str = "unknown") -> None:
    """Clear existing session files - only call when genuinely needed.

    Args:
        reason: Why the session is being cleared (for logging/debugging)
    """
    global mm_client

    logger.info(f"Clearing session files (reason: {reason})")

    # Clear the client instance to ensure fresh initialization
    if mm_client is not None:
        logger.info("[AUTH] Clearing mm_client instance")
        mm_client = None

    # Clear our custom session file
    if session_file.exists():
        try:
            session_file.unlink()
            logger.info(f"Cleared custom session file: {session_file}")
        except Exception as e:
            logger.warning(f"Failed to clear custom session file: {e}")

    # Clear the monarchmoney library's default session file
    mm_session_file = session_dir / "mm_session.pickle"
    if mm_session_file.exists():
        try:
            mm_session_file.unlink()
            logger.info(f"Cleared mm session file: {mm_session_file}")
        except Exception as e:
            logger.warning(f"Failed to clear mm session file: {e}")

async def api_call_with_retry(method_name: str, *args: Any, **kwargs: Any) -> Any:
    """Wrapper for API calls that handles session expiration and retries.

    Only clears sessions and re-authenticates for genuine auth errors.
    Other errors (network, library issues, etc.) are raised immediately.

    Args:
        method_name: Name of the method to call on mm_client (e.g., "get_accounts")
        *args: Positional arguments to pass to the method
        **kwargs: Keyword arguments to pass to the method
    """
    global auth_state, mm_client

    # Get the method from the current mm_client instance
    if mm_client is None:
        raise ValueError("mm_client is not initialized")

    method = getattr(mm_client, method_name)

    try:
        return await method(*args, **kwargs)
    except Exception as e:
        # Use the new helper to determine if this is a real auth error
        if is_auth_error(e):
            logger.warning(f"API call failed with authentication error: {e}")
            logger.info("Clearing session and re-authenticating")

            # Reset auth state BEFORE clearing session so ensure_authenticated knows to re-init
            logger.info(f"[AUTH] Resetting auth state from {auth_state.value} to not_initialized")
            auth_state = AuthState.NOT_INITIALIZED

            clear_session(reason="authentication failure during API call")

            # Re-authenticate and retry once
            await ensure_authenticated()
            logger.info("Retrying API call after re-authentication")

            # CRITICAL: Get the method from the NEW mm_client instance after re-auth
            if mm_client is None:
                raise ValueError("mm_client is still None after re-authentication")
            method = getattr(mm_client, method_name)
            return await method(*args, **kwargs)
        else:
            # Not an auth error - raise immediately without clearing session
            raise


async def initialize_client() -> None:
    """Initialize the MonarchMoney client with authentication.

    This function attempts to use cached sessions when possible and only
    performs fresh authentication when necessary. It does NOT validate
    sessions immediately - validation happens on first API call.
    """
    global mm_client, auth_state, auth_error

    email = os.getenv("MONARCH_EMAIL")
    password = os.getenv("MONARCH_PASSWORD")
    mfa_secret = os.getenv("MONARCH_MFA_SECRET")

    if not email or not password:
        error_msg = "MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required"
        logger.error(error_msg)
        auth_state = AuthState.FAILED
        auth_error = error_msg
        raise ValueError(error_msg)

    logger.info(f"[AUTH] Initializing MonarchMoney client for {email}")
    mm_client = MonarchMoney()

    # Try to load existing session first (unless forced to skip)
    force_login = os.getenv("MONARCH_FORCE_LOGIN") == "true"
    if session_file.exists() and not force_login:
        try:
            logger.info(f"[AUTH] Found existing session file: {session_file}")
            logger.info(f"[AUTH] Attempting to load session (no validation at this stage)")
            # Load session with stdout/stderr suppression
            import contextlib
            import io

            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture), \
                 contextlib.redirect_stderr(stderr_capture):
                mm_client.load_session(str(session_file))

            # Log what was captured for debugging
            if stdout_capture.getvalue():
                logger.debug(f"[AUTH] Captured stdout during load_session: '{stdout_capture.getvalue()}'")
            if stderr_capture.getvalue():
                logger.debug(f"[AUTH] Captured stderr during load_session: '{stderr_capture.getvalue()}'")

            logger.info("[AUTH] ✓ Session loaded successfully - no validation performed")
            logger.info("[AUTH] Session validity will be tested on first API call")
            auth_state = AuthState.AUTHENTICATED
            return

        except Exception as e:
            logger.warning(f"[AUTH] Failed to load session: {e}")
            # Only clear session if it's a genuine auth error
            if is_auth_error(e):
                logger.info("[AUTH] Session file appears invalid (auth error), clearing before fresh login")
                clear_session(reason="invalid session file")
            else:
                logger.info(f"[AUTH] Non-auth error loading session (error type: {type(e).__name__}), will try fresh login anyway")

    else:
        if not session_file.exists():
            logger.info(f"[AUTH] No existing session file found at {session_file}")
        if force_login:
            logger.info("[AUTH] MONARCH_FORCE_LOGIN=true, forcing fresh authentication")
            clear_session(reason="forced login requested")

    # Perform fresh authentication
    max_retries = 2
    retry_delay = 3  # Increase delay to avoid rate limiting

    logger.info(f"[AUTH] Starting fresh authentication (max {max_retries} attempts)")

    for attempt in range(max_retries):
        try:
            logger.info(f"[AUTH] Attempt {attempt + 1}/{max_retries}: Calling Monarch Money login API")
            if mfa_secret:
                logger.info("[AUTH] Using MFA authentication (TOTP)")
                await mm_client.login(email, password, mfa_secret_key=mfa_secret, use_saved_session=False)
            else:
                logger.info("[AUTH] Using password-only authentication")
                await mm_client.login(email, password, use_saved_session=False)

            logger.info("[AUTH] ✓ Login API call successful")
            logger.info("[AUTH] Saving session to disk")

            # Save session with stdout/stderr suppression
            import contextlib
            import io

            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture), \
                 contextlib.redirect_stderr(stderr_capture):
                mm_client.save_session(str(session_file))

            # Log what was captured for debugging
            if stdout_capture.getvalue():
                logger.debug(f"Captured stdout during save_session: '{stdout_capture.getvalue()}'")
            if stderr_capture.getvalue():
                logger.debug(f"Captured stderr during save_session: '{stderr_capture.getvalue()}'")

            if session_file.exists():
                session_file.chmod(0o600)  # Secure permissions
                logger.info(f"[AUTH] ✓ Session saved with secure permissions: {session_file}")
            else:
                logger.warning("[AUTH] ⚠ Session file was not created by save_session()")

            auth_state = AuthState.AUTHENTICATED
            auth_error = None
            logger.info(f"[AUTH] ✓ Authentication complete - state: {auth_state.value}")
            return  # Success!

        except RequireMFAException:
            error_msg = "Multi-factor authentication required but MONARCH_MFA_SECRET not set"
            logger.error(f"[AUTH] ✗ {error_msg}")
            auth_state = AuthState.FAILED
            auth_error = error_msg
            raise ValueError(error_msg)

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"[AUTH] Attempt {attempt + 1} failed: {e}")
                # Determine if this is an auth error or other error
                if is_auth_error(e):
                    logger.info(f"[AUTH] Detected auth error, clearing session before retry")
                    clear_session(reason=f"auth failure on attempt {attempt + 1}")
                else:
                    logger.info(f"[AUTH] Non-auth error ({type(e).__name__}), keeping session for retry")
                logger.info(f"[AUTH] Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            else:
                error_msg = f"Authentication failed after {max_retries} attempts: {e}"
                logger.error(f"[AUTH] ✗ {error_msg}")
                auth_state = AuthState.FAILED
                auth_error = str(e)
                raise


async def ensure_authenticated() -> None:
    """Ensure the client is authenticated, initializing on-demand if needed.

    This function uses a lock to prevent concurrent initialization attempts
    and returns immediately if already authenticated.

    Call this at the start of every tool that needs the mm_client.
    """
    global mm_client, auth_state, auth_lock, auth_error

    # Initialize lock on first call (must be done in async context)
    if auth_lock is None:
        auth_lock = asyncio.Lock()
        logger.info("[AUTH] Async lock created for authentication")

    # Fast path - already authenticated
    if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
        logger.debug("[AUTH] Fast path: already authenticated")
        return

    logger.info(f"[AUTH] Authentication needed, current state: {auth_state.value}")

    # Use lock to prevent concurrent initialization
    async with auth_lock:
        # Check again after acquiring lock (another task may have initialized)
        if auth_state == AuthState.AUTHENTICATED and mm_client is not None:
            logger.info("[AUTH] Lock acquired: another task completed authentication")
            return

        # Check if in failed state
        if auth_state == AuthState.FAILED:
            error_msg = f"Authentication previously failed: {auth_error or 'unknown error'}"
            logger.error(f"[AUTH] Cannot authenticate: {error_msg}")
            raise ValueError(error_msg)

        # Check if already initializing (shouldn't happen with lock, but defensive)
        if auth_state == AuthState.INITIALIZING:
            logger.warning("[AUTH] Already initializing (unexpected with lock)")
            # Wait a bit and check again
            await asyncio.sleep(1)
            if auth_state == AuthState.AUTHENTICATED:
                logger.info("[AUTH] Initialization completed while waiting")
                return
            logger.error("[AUTH] Initialization timeout")
            raise ValueError("Authentication is taking too long")

        # Perform initialization
        logger.info("[AUTH] Starting lazy authentication")
        auth_state = AuthState.INITIALIZING

        try:
            await initialize_client()
            # initialize_client sets auth_state to AUTHENTICATED on success
            logger.info("[AUTH] Lazy authentication completed successfully")
        except Exception as e:
            auth_state = AuthState.FAILED
            auth_error = str(e)
            logger.error(f"[AUTH] Failed to initialize client: {e}")
            raise


# FastMCP Tool definitions using decorators

@mcp.tool()
@track_usage
async def get_accounts() -> str:
    """Retrieve all linked financial accounts."""
    await ensure_authenticated()

    try:
        logger.info("Fetching accounts")
        accounts = await api_call_with_retry("get_accounts")
        accounts = convert_dates_to_strings(accounts)
        logger.info(f"Accounts retrieved successfully, count: {len(accounts) if isinstance(accounts, list) else 'unknown'}")
        return json.dumps(accounts, indent=2)
    except Exception as e:
        logger.error(f"Failed to fetch accounts: {e}")
        raise


@mcp.tool()
@track_usage
async def get_transactions(
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    verbose: bool = False
) -> str:
    """Fetch transactions with flexible date filtering and smart output formatting.

    Args:
        limit: Maximum number of transactions to return (default: 100, max: 1000)
        offset: Number of transactions to skip for pagination (default: 0)
        start_date: Filter transactions from this date onwards. Supports natural language like 'last month', 'yesterday', '30 days ago'
                    NOTE: If you provide start_date without end_date, end_date will auto-default to 'today'
        end_date: Filter transactions up to this date. Supports natural language
                  NOTE: If you provide end_date without start_date, start_date will auto-default to 'this month'
        account_id: Filter by specific account ID
        category_id: Filter by specific category ID
        verbose: Output format control (default: False)
            - False: Returns compact format with essential fields only (id, date, amount, merchant, plaidName, category, account, pending, needsReview, notes)
                     Reduces context usage by ~80% - ideal for most queries
            - True: Returns complete transaction details with all metadata, nested objects, and timestamps
                    Use when you need full data for analysis or updates

    Returns:
        JSON string containing transaction list
    """
    await ensure_authenticated()

    try:
        logger.info(f"Fetching transactions: limit={limit}, offset={offset}, start_date={start_date}, end_date={end_date}, verbose={verbose}")

        # Build filter parameters with flexible date parsing
        filters: Dict[str, Any] = build_date_filter(start_date, end_date)

        # monarchmoney expects account_ids and category_ids as LISTS
        if account_id:
            filters["account_ids"] = [account_id]
        if category_id:
            filters["category_ids"] = [category_id]

        response = await api_call_with_retry(
            "get_transactions",
            limit=limit,
            offset=offset,
            **filters
        )
        # Extract transactions list from nested response structure
        transactions = extract_transactions_list(response)
        transactions = convert_dates_to_strings(transactions)

        # Format output based on verbose flag
        if not verbose and isinstance(transactions, list):
            transactions = format_transactions_compact(transactions)

        logger.info(f"Transactions retrieved successfully, count: {len(transactions)}")
        return json.dumps(transactions, indent=2)
    except Exception as e:
        logger.error(f"Failed to fetch transactions: {e} (limit={limit}, start_date={start_date})")
        raise


@mcp.tool()
@track_usage
async def search_transactions(
    query: str,
    limit: int = 500,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None,
    verbose: bool = False
) -> str:
    """Search transactions by text using Monarch Money's built-in search.

    Uses the Monarch Money API's native search functionality to find transactions
    matching the query across merchant names, descriptions, notes, and other fields.
    Returns only matching results to reduce context usage.

    Args:
        query: Search term to find in transactions (searches merchant names, descriptions, notes)
        limit: Maximum transactions to return (default: 500, max: 1000)
        offset: Number of transactions to skip for pagination (default: 0)
        start_date: Filter transactions from this date onwards (supports natural language)
                    NOTE: If you provide start_date without end_date, end_date will auto-default to 'today'
        end_date: Filter transactions up to this date (supports natural language)
                  NOTE: If you provide end_date without start_date, start_date will auto-default to 'this month'
        account_id: Filter by specific account ID
        category_id: Filter by specific category ID
        verbose: Output format control (default: False)
            - False: Returns compact format (~80% reduction)
            - True: Returns complete transaction details

    Returns:
        JSON string with search results (list of transactions matching the query)
    """
    await ensure_authenticated()

    if not query or not query.strip():
        raise ValueError("Query parameter cannot be empty")

    try:
        query_str = query.strip()
        logger.info(f"Searching transactions for '{query_str}': limit={limit}, start_date={start_date}, end_date={end_date}")

        # Build filter parameters
        filters: Dict[str, Any] = build_date_filter(start_date, end_date)

        # monarchmoney expects account_ids and category_ids as LISTS
        if account_id:
            filters["account_ids"] = [account_id]
        if category_id:
            filters["category_ids"] = [category_id]

        # Use the library's built-in search parameter
        filters["search"] = query_str

        # Fetch transactions from API with search filter
        response = await api_call_with_retry(
            "get_transactions",
            limit=limit,
            offset=offset,
            **filters
        )
        # Extract transactions list from nested response structure
        transactions = extract_transactions_list(response)
        transactions = convert_dates_to_strings(transactions)

        # Format output based on verbose flag
        if not verbose:
            transactions = format_transactions_compact(transactions)

        result = {
            "search_metadata": {
                "query": query_str,
                "result_count": len(transactions),
                "filters_applied": {k: v for k, v in filters.items() if k != "search"}
            },
            "transactions": transactions
        }

        logger.info(f"Search complete: '{query_str}' returned {len(transactions)} results")
        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(f"Failed to search transactions: {e} (query='{query}')")
        raise


@mcp.tool()
@track_usage
async def get_budgets(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
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
        budgets = await api_call_with_retry("get_budgets", **kwargs)
        budgets = convert_dates_to_strings(budgets)
        return json.dumps(budgets, indent=2)
    except Exception as e:
        # Handle the case where no budgets exist
        if "Something went wrong while processing: None" in str(e):
            return json.dumps({
                "budgets": [],
                "message": "No budgets configured in your Monarch Money account"
            }, indent=2)
        else:
            # Re-raise other errors
            raise


@mcp.tool()
@track_usage
async def get_cashflow(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
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

    cashflow = await api_call_with_retry("get_cashflow", **kwargs)
    cashflow = convert_dates_to_strings(cashflow)
    return json.dumps(cashflow, indent=2)


@mcp.tool()
@track_usage
async def get_transaction_categories() -> str:
    """List all transaction categories."""
    await ensure_authenticated()

    categories = await api_call_with_retry("get_transaction_categories")
    categories = convert_dates_to_strings(categories)
    return json.dumps(categories, indent=2)


@mcp.tool()
@track_usage
async def create_transaction(
    amount: float,
    description: str,
    account_id: str,
    date: str,
    category_id: Optional[str] = None,
    notes: Optional[str] = None
) -> str:
    """Create a new transaction."""
    await ensure_authenticated()

    try:
        # Convert date string to date object
        transaction_date = datetime.strptime(date, "%Y-%m-%d").date()

        # Use api_call_with_retry for session expiration handling and add timeout
        result = await asyncio.wait_for(
            api_call_with_retry(
                "create_transaction",
                amount=amount,
                description=description,
                category_id=category_id,
                account_id=account_id,
                date=transaction_date,
                notes=notes
            ),
            timeout=30.0  # 30 second timeout
        )
        result = convert_dates_to_strings(result)
        return json.dumps(result, indent=2)
    except asyncio.TimeoutError:
        logger.error(f"Timeout creating transaction after 30 seconds")
        raise ValueError(f"Transaction creation timed out after 30 seconds. Please try again.")
    except Exception as e:
        logger.error(f"Failed to create transaction: {e}")
        raise


@mcp.tool()
@track_usage
async def update_transaction(
    transaction_id: str,
    amount: Optional[float] = None,
    description: Optional[str] = None,
    category_id: Optional[str] = None,
    date: Optional[str] = None,
    notes: Optional[str] = None
) -> str:
    """Update an existing transaction."""
    await ensure_authenticated()

    try:
        # Build update parameters
        updates: Dict[str, Any] = {"transaction_id": transaction_id}
        if amount is not None:
            updates["amount"] = amount
        if description is not None:
            updates["description"] = description
        if category_id is not None:
            updates["category_id"] = category_id
        if date is not None:
            updates["date"] = datetime.strptime(date, "%Y-%m-%d").date()
        if notes is not None:
            updates["notes"] = notes

        # Use api_call_with_retry for session expiration handling and add timeout
        result = await asyncio.wait_for(
            api_call_with_retry("update_transaction", **updates),
            timeout=30.0  # 30 second timeout
        )
        result = convert_dates_to_strings(result)
        return json.dumps(result, indent=2)
    except asyncio.TimeoutError:
        logger.error(f"Timeout updating transaction {transaction_id} after 30 seconds")
        raise ValueError(f"Transaction update timed out after 30 seconds. Please try again.")
    except Exception as e:
        logger.error(f"Failed to update transaction {transaction_id}: {e}")
        raise


@mcp.tool()
@track_usage
async def update_transactions_bulk(
    updates: str
) -> str:
    """Update multiple transactions in a single call to save round-trips.

    This is much more efficient than calling update_transaction multiple times.
    Updates are executed in parallel for maximum performance.

    Args:
        updates: JSON string containing list of transaction updates. Each update should have:
            - transaction_id (required): ID of transaction to update
            - amount (optional): New amount
            - description (optional): New description
            - category_id (optional): New category ID
            - date (optional): New date in YYYY-MM-DD format
            - notes (optional): New notes

    Example:
        [
            {"transaction_id": "123", "category_id": "cat_456", "notes": "Updated"},
            {"transaction_id": "789", "amount": 50.00, "description": "Grocery store"}
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
            raise ValueError(f"Invalid JSON in updates parameter: {e}")

        if not isinstance(updates_list, list):
            raise ValueError("updates parameter must be a JSON array of transaction updates")

        if len(updates_list) == 0:
            return json.dumps({"message": "No updates provided", "results": []}, indent=2)

        logger.info(f"Starting bulk transaction update: {len(updates_list)} transactions")

        # Build list of update tasks
        async def update_single(update_data: Dict[str, Any]) -> Dict[str, Any]:
            """Update a single transaction and return result with transaction_id."""
            try:
                if not isinstance(update_data, dict):
                    return {
                        "transaction_id": None,
                        "status": "error",
                        "error": "Update must be a dictionary"
                    }

                if "transaction_id" not in update_data:
                    return {
                        "transaction_id": None,
                        "status": "error",
                        "error": "transaction_id is required"
                    }

                txn_id = update_data["transaction_id"]

                # Build update parameters
                update_params: Dict[str, Any] = {"transaction_id": txn_id}

                if "amount" in update_data:
                    update_params["amount"] = float(update_data["amount"])
                if "description" in update_data:
                    update_params["description"] = str(update_data["description"])
                if "category_id" in update_data:
                    update_params["category_id"] = str(update_data["category_id"])
                if "date" in update_data:
                    date_str = str(update_data["date"])
                    update_params["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
                if "notes" in update_data:
                    update_params["notes"] = str(update_data["notes"])

                # Execute update with timeout
                result = await asyncio.wait_for(
                    api_call_with_retry("update_transaction", **update_params),
                    timeout=30.0
                )

                return {
                    "transaction_id": txn_id,
                    "status": "success",
                    "result": convert_dates_to_strings(result)
                }

            except asyncio.TimeoutError:
                return {
                    "transaction_id": update_data.get("transaction_id"),
                    "status": "error",
                    "error": "Update timed out after 30 seconds"
                }
            except Exception as e:
                return {
                    "transaction_id": update_data.get("transaction_id"),
                    "status": "error",
                    "error": str(e)
                }

        # Execute all updates in parallel
        results = await asyncio.gather(
            *[update_single(update_data) for update_data in updates_list],
            return_exceptions=False  # We handle exceptions in update_single
        )

        # Count successes and failures
        success_count = sum(1 for r in results if r["status"] == "success")
        failure_count = len(results) - success_count

        logger.info(f"Bulk update complete: {success_count} succeeded, {failure_count} failed")

        response = {
            "summary": {
                "total": len(results),
                "succeeded": success_count,
                "failed": failure_count
            },
            "results": results
        }

        return json.dumps(response, indent=2)

    except Exception as e:
        logger.error(f"Failed to execute bulk transaction update: {e}")
        raise


@mcp.tool()
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


@mcp.tool()
@track_usage
async def get_account_history(
    account_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
    """Get historical account balance data."""
    await ensure_authenticated()
    
    kwargs: Dict[str, Any] = {"account_id": account_id}
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
@track_usage
async def set_budget_amount(
    category_id: str,
    amount: float
) -> str:
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


@mcp.tool()
@track_usage
async def create_manual_account(
    account_name: str,
    account_type: str,
    balance: float
) -> str:
    """Create a manually tracked account."""
    await ensure_authenticated()
    
    try:
        result = await api_call_with_retry(
            "create_manual_account",
            account_name=account_name,
            account_type=account_type,
            balance=balance
        )
        result = convert_dates_to_strings(result)
        log.info("Manual account created", name=account_name, type=account_type)
        return json.dumps(result, indent=2)
    except Exception as e:
        log.error("Failed to create manual account", error=str(e), name=account_name)
        raise


@mcp.tool()
@track_usage
async def get_spending_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = "category"
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
        response = await api_call_with_retry("get_transactions", limit=1000, **filters)
        # Extract transactions list from nested response structure
        transactions = extract_transactions_list(response)
        
        # Aggregate spending data
        summary = {"period": {"start": start_date, "end": end_date}, "groups": {}, "totals": {"income": 0, "expenses": 0, "net": 0}}
        
        for txn in transactions:
            amount = float(txn.get("amount", 0))
            
            # Track totals
            if amount > 0:
                summary["totals"]["income"] += amount
            else:
                summary["totals"]["expenses"] += abs(amount)
            
            # Group by specified field
            if group_by == "category":
                key = txn.get("category", {}).get("name", "Uncategorized") if isinstance(txn.get("category"), dict) else "Uncategorized"
            elif group_by == "account":
                key = txn.get("account", {}).get("name", "Unknown") if isinstance(txn.get("account"), dict) else "Unknown"
            elif group_by == "month":
                txn_date = txn.get("date", "")
                key = txn_date[:7] if len(txn_date) >= 7 else "Unknown"  # YYYY-MM format
            else:
                key = "All"
            
            if key not in summary["groups"]:
                summary["groups"][key] = {"income": 0, "expenses": 0, "net": 0, "count": 0}
            
            if amount > 0:
                summary["groups"][key]["income"] += amount
            else:
                summary["groups"][key]["expenses"] += abs(amount)
            
            summary["groups"][key]["net"] += amount
            summary["groups"][key]["count"] += 1
        
        summary["totals"]["net"] = summary["totals"]["income"] - summary["totals"]["expenses"]
        
        # Sort groups by total spending (expenses)
        sorted_groups = dict(sorted(summary["groups"].items(), key=lambda x: x[1]["expenses"], reverse=True))
        summary["groups"] = sorted_groups
        
        log.info("Spending summary generated", 
                total_transactions=len(transactions),
                groups_count=len(summary["groups"]),
                net_amount=summary["totals"]["net"])
        
        return json.dumps(summary, indent=2)
        
    except Exception as e:
        log.error("Failed to generate spending summary", error=str(e))
        raise


@mcp.tool()
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


@mcp.tool()
@track_usage
async def get_complete_financial_overview(
    period: str = "this month"
) -> str:
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
        budgets_task = api_call_with_retry("get_budgets", **filters)
        cashflow_task = api_call_with_retry("get_cashflow", **filters)
        transactions_task = api_call_with_retry("get_transactions", limit=500, **filters)
        categories_task = api_call_with_retry("get_transaction_categories")

        # Wait for all results
        accounts, budgets, cashflow, transactions, categories = await asyncio.gather(
            accounts_task, budgets_task, cashflow_task, transactions_task, categories_task,
            return_exceptions=True
        )
        
        # Handle any exceptions gracefully
        results = {}
        
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
                    "total_income": sum(float(t.get("amount", 0)) for t in transactions_list if float(t.get("amount", 0)) > 0),
                    "total_expenses": sum(abs(float(t.get("amount", 0))) for t in transactions_list if float(t.get("amount", 0)) < 0),
                    "unique_categories": len(set(t.get("category", {}).get("name", "Unknown") for t in transactions_list if isinstance(t.get("category"), dict))),
                    "unique_accounts": len(set(t.get("account", {}).get("name", "Unknown") for t in transactions_list if isinstance(t.get("account"), dict)))
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
            "timestamp": datetime.now().isoformat()
        }
        
        log.info("Complete financial overview generated", 
                period=period, 
                accounts_count=len(results.get("accounts", [])) if isinstance(results.get("accounts"), list) else 0,
                transactions_count=results.get("transaction_summary", {}).get("total_count", 0))
        
        return json.dumps(results, indent=2)
        
    except Exception as e:
        log.error("Failed to generate financial overview", error=str(e), period=period)
        raise


@mcp.tool()
@track_usage
async def analyze_spending_patterns(
    lookback_months: int = 6,
    include_forecasting: bool = True
) -> str:
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
        from dateutil.relativedelta import relativedelta
        
        # Calculate date ranges for analysis
        end_date = datetime.now().date()
        start_date = end_date - relativedelta(months=lookback_months)
        
        # Batch API calls for comprehensive data
        transactions_task = api_call_with_retry(
            "get_transactions",
            limit=2000,
            start_date=start_date,
            end_date=end_date
        )
        budgets_task = api_call_with_retry("get_budgets", start_date=start_date, end_date=end_date)
        accounts_task = api_call_with_retry("get_accounts")
        categories_task = api_call_with_retry("get_transaction_categories")

        transactions, budgets, accounts, categories = await asyncio.gather(
            transactions_task, budgets_task, accounts_task, categories_task,
            return_exceptions=True
        )
        
        analysis = {
            "analysis_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(), 
                "months_analyzed": lookback_months
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
            monthly_data = {}
            category_totals = {}
            account_usage = {}

            for txn in transactions_list:
                txn_date = txn.get("date", "")
                amount = float(txn.get("amount", 0))
                category_name = txn.get("category", {}).get("name", "Uncategorized") if isinstance(txn.get("category"), dict) else "Uncategorized"
                account_name = txn.get("account", {}).get("name", "Unknown") if isinstance(txn.get("account"), dict) else "Unknown"
                
                # Monthly trends (YYYY-MM)
                month_key = txn_date[:7] if len(txn_date) >= 7 else "Unknown"
                if month_key not in monthly_data:
                    monthly_data[month_key] = {"income": 0, "expenses": 0, "net": 0, "transaction_count": 0}
                
                if amount > 0:
                    monthly_data[month_key]["income"] += amount
                else:
                    monthly_data[month_key]["expenses"] += abs(amount)
                monthly_data[month_key]["net"] += amount
                monthly_data[month_key]["transaction_count"] += 1
                
                # Category analysis
                if category_name not in category_totals:
                    category_totals[category_name] = {"total": 0, "transactions": 0, "avg_amount": 0}
                category_totals[category_name]["total"] += abs(amount) if amount < 0 else 0  # Only expenses
                category_totals[category_name]["transactions"] += 1
                
                # Account usage
                if account_name not in account_usage:
                    account_usage[account_name] = {"total_volume": 0, "transactions": 0}
                account_usage[account_name]["total_volume"] += abs(amount)
                account_usage[account_name]["transactions"] += 1
            
            # Calculate averages and sort data
            for category in category_totals:
                if category_totals[category]["transactions"] > 0:
                    category_totals[category]["avg_amount"] = category_totals[category]["total"] / category_totals[category]["transactions"]
            
            analysis["monthly_trends"] = dict(sorted(monthly_data.items()))
            analysis["category_analysis"] = dict(sorted(category_totals.items(), key=lambda x: x[1]["total"], reverse=True))
            analysis["account_usage"] = dict(sorted(account_usage.items(), key=lambda x: x[1]["total_volume"], reverse=True))
            
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
                        "note": "Forecast based on 3-month spending average"
                    }
        
        if not isinstance(budgets, Exception):
            analysis["budget_performance"] = convert_dates_to_strings(budgets)

        # Add metadata
        txn_count = len(transactions_list) if not isinstance(transactions, Exception) else 0
        analysis["_metadata"] = {
            "api_calls_made": 4,
            "total_transactions_analyzed": txn_count,
            "analysis_timestamp": datetime.now().isoformat()
        }

        log.info("Spending pattern analysis completed",
                lookback_months=lookback_months,
                transactions_analyzed=txn_count,
                include_forecasting=include_forecasting)
        
        return json.dumps(analysis, indent=2)
        
    except Exception as e:
        log.error("Failed to analyze spending patterns", error=str(e), lookback_months=lookback_months)
        raise


async def main() -> None:
    """Main entry point for the server.

    The server starts immediately without authentication. Authentication
    happens lazily on the first tool call via ensure_authenticated().
    """
    logger.info("=" * 70)
    logger.info("[AUTH] MCP Server starting - LAZY AUTHENTICATION MODE")
    logger.info("[AUTH] Authentication will be performed on-demand (first tool call)")
    logger.info(f"[AUTH] Session file location: {session_file}")
    logger.info(f"[AUTH] Current auth state: {auth_state.value}")
    logger.info("=" * 70)

    # Run the FastMCP server with comprehensive error handling
    try:
        logger.info("Starting MCP server with stdio transport")
        await mcp.run_stdio_async()
    except BrokenPipeError:
        # This is expected when the client disconnects - exit gracefully
        logger.info("Client disconnected (broken pipe) - shutting down gracefully")
    except ConnectionResetError:
        # Similar to BrokenPipeError but for connection resets
        logger.info("Connection reset by client - shutting down gracefully")
    except KeyboardInterrupt:
        logger.info("Received interrupt signal - shutting down")
    except Exception as e:
        logger.error(f"Unexpected error in MCP server: {e}")
        raise


if __name__ == "__main__":
    # Add signal handling for graceful shutdown
    import signal

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully")
        # Let asyncio handle the shutdown

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(main())
    except ExceptionGroup as eg:  # type: ignore[misc]
        # Handle exception groups (from anyio TaskGroups) - filter out expected shutdown errors
        remaining_exceptions = []
        for exc in eg.exceptions:
            # Check for shutdown-related exceptions including nested ones
            is_shutdown_error = (
                isinstance(exc, (BrokenPipeError, ConnectionResetError, OSError, EOFError)) or
                (isinstance(exc, Exception) and any(
                    err_str in str(exc).lower()
                    for err_str in ["broken pipe", "connection reset", "[errno 32]", "eof"]
                ))
            )
            if not is_shutdown_error:
                remaining_exceptions.append(exc)

        if remaining_exceptions:
            logger.error(f"Fatal error: {eg}")
            raise
        else:
            # All exceptions were shutdown-related - exit quietly
            logger.info("Shutdown complete (broken pipe expected during client disconnect)")
    except BrokenPipeError:
        # Handle broken pipe at the top level (direct exception, not in ExceptionGroup)
        logger.info("Broken pipe during shutdown - exiting quietly")
    except ConnectionResetError:
        # Handle connection reset at the top level (direct exception)
        logger.info("Connection reset during shutdown - exiting quietly")
    except KeyboardInterrupt:
        logger.info("Interrupted by user - exiting")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise