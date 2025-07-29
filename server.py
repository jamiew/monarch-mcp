#!/usr/bin/env python3
"""MonarchMoney MCP Server - Provides access to Monarch Money financial data via MCP protocol."""

import os
import asyncio
import json
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
    """Parse flexible date input using natural language parsing."""
    if not date_input:
        raise ValueError("Date input cannot be empty")
    
    # Handle common natural language patterns
    date_input = date_input.lower().strip()
    
    # Map common phrases to relative dates
    today = date.today()
    
    if date_input in ["today", "now"]:
        return today
    elif date_input == "yesterday":
        return date(today.year, today.month, today.day - 1) if today.day > 1 else date(today.year, today.month - 1, 30)
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
    
    # Try parsing with dateutil for more complex patterns
    try:
        parsed_datetime = date_parser.parse(date_input)
        return parsed_datetime.date()
    except (ValueError, TypeError) as e:
        log.warning("Failed to parse date", input=date_input, error=str(e))
        raise ValueError(f"Could not parse date: {date_input}")


def build_date_filter(start_date: Optional[str], end_date: Optional[str]) -> Dict[str, date]:
    """Build date filter dictionary with flexible parsing."""
    filters: Dict[str, date] = {}
    
    if start_date:
        try:
            filters["start_date"] = parse_flexible_date(start_date)
        except ValueError:
            # Fallback to strict parsing
            filters["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    
    if end_date:
        try:
            filters["end_date"] = parse_flexible_date(end_date)
        except ValueError:
            # Fallback to strict parsing
            filters["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()
    
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

# Configure structured logging (stderr only to avoid interfering with MCP stdio)
import sys
from pathlib import Path
import logging

# Redirect all logging to stderr to prevent stdout contamination
root_logger = logging.getLogger()
root_logger.handlers.clear()
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(logging.WARNING)  # Only show warnings and errors
root_logger.addHandler(stderr_handler)
root_logger.setLevel(logging.WARNING)

# Specifically handle aiohttp and monarchmoney logging
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logging.getLogger('monarchmoney').setLevel(logging.WARNING)

# Create logs directory
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Configure dual logging: stderr for debugging + file for usage analytics
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()  # Human-readable for development
    ],
    wrapper_class=structlog.make_filtering_bound_logger(30),  # WARNING level to reduce noise
    context_class=dict,
    logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
    cache_logger_on_first_use=False,
)

# Separate usage analytics logger
usage_log = structlog.get_logger("usage_analytics")
usage_log = usage_log.bind(logger_type="usage_analytics")

# Configure usage analytics file logging
import logging
from structlog.stdlib import LoggerFactory

usage_file_handler = logging.FileHandler(logs_dir / "usage_analytics.jsonl")
usage_file_handler.setLevel(logging.INFO)
usage_logger = logging.getLogger("usage_analytics")
usage_logger.setLevel(logging.INFO)
usage_logger.addHandler(usage_file_handler)

# Structured logger for usage analytics  
usage_structlog = structlog.wrap_logger(
    usage_logger,
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)

log = structlog.get_logger()

# Usage analytics tracking
import functools
import time
from typing import Dict, List, Any
import uuid

# Session tracking for usage analytics
current_session_id = str(uuid.uuid4())
usage_patterns: Dict[str, List[Dict[str, Any]]] = {}

def track_usage(func):
    """Decorator to track tool usage patterns for analytics."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        tool_name = func.__name__
        
        # Track this call
        call_info = {
            "session_id": current_session_id,
            "tool_name": tool_name,
            "timestamp": time.time(),
            "args": list(args),
            "kwargs": {k: v for k, v in kwargs.items() if k not in ['password', 'mfa_secret']},  # Exclude sensitive data
        }
        
        try:
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time
            call_info.update({
                "status": "success",
                "execution_time": execution_time,
                "result_size": len(str(result)) if result else 0
            })
            
            # Log for analytics
            usage_structlog.info(
                "tool_called",
                **call_info
            )
            
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
            
            usage_structlog.error(
                "tool_error",
                **call_info
            )
            raise
            
    return wrapper

# Initialize the FastMCP server
mcp = FastMCP("monarch-money")

# Global variable to store the MonarchMoney client
mm_client: Optional[MonarchMoney] = None

# Secure session directory with proper permissions
session_dir = Path(".mm")
session_dir.mkdir(mode=0o700, exist_ok=True)
session_file = session_dir / "session.pickle"


async def initialize_client() -> None:
    """Initialize the MonarchMoney client with authentication."""
    global mm_client
    
    email = os.getenv("MONARCH_EMAIL")
    password = os.getenv("MONARCH_PASSWORD")
    mfa_secret = os.getenv("MONARCH_MFA_SECRET")
    
    if not email or not password:
        print("Missing required environment variables", file=sys.stderr)
        raise ValueError("MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required")
    
    mm_client = MonarchMoney()
    
    # Try to load existing session first
    if session_file.exists() and not os.getenv("MONARCH_FORCE_LOGIN"):
        try:
            mm_client.load_session(str(session_file))
            # Test if session is still valid
            await mm_client.get_accounts()
            print("Session loaded successfully", file=sys.stderr)
            return
        except Exception as e:
            print(f"Session invalid, attempting fresh login: {e}", file=sys.stderr)
    
    # Login with credentials
    try:
        if mfa_secret:
            await mm_client.login(email, password, mfa_secret_key=mfa_secret)
        else:
            await mm_client.login(email, password)
        
        # Save session for future use
        mm_client.save_session(str(session_file))
        if session_file.exists():
            session_file.chmod(0o600)  # Secure permissions
        print("Authentication successful, session saved", file=sys.stderr)
        
    except RequireMFAException:
        print("MFA required but not provided", file=sys.stderr)
        raise ValueError("Multi-factor authentication required but MONARCH_MFA_SECRET not set")
    except Exception as e:
        print(f"Authentication failed: {e}", file=sys.stderr)
        raise


# FastMCP Tool definitions using decorators

@mcp.tool()
@track_usage
async def get_accounts() -> str:
    """Retrieve all linked financial accounts."""
    if not mm_client:
        log.error("Client not initialized")
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        log.info("Fetching accounts")
        accounts = await mm_client.get_accounts()
        accounts = convert_dates_to_strings(accounts)
        log.info("Accounts retrieved successfully", count=len(accounts) if isinstance(accounts, list) else "unknown")
        return json.dumps(accounts, indent=2)
    except Exception as e:
        log.error("Failed to fetch accounts", error=str(e))
        raise


@mcp.tool()
@track_usage
async def get_transactions(
    limit: int = 100,
    offset: int = 0, 
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None
) -> str:
    """Fetch transactions with flexible date filtering (supports natural language like 'last month', 'yesterday')."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        log.info("Fetching transactions", limit=limit, offset=offset, start_date=start_date, end_date=end_date)
        
        # Build filter parameters with flexible date parsing
        filters: Dict[str, Any] = build_date_filter(start_date, end_date)
        
        if account_id:
            filters["account_id"] = account_id
        if category_id:
            filters["category_id"] = category_id
        
        transactions = await mm_client.get_transactions(
            limit=limit,
            offset=offset,
            **filters
        )
        transactions = convert_dates_to_strings(transactions)
        log.info("Transactions retrieved successfully", count=len(transactions) if isinstance(transactions, list) else "unknown")
        return json.dumps(transactions, indent=2)
    except Exception as e:
        log.error("Failed to fetch transactions", error=str(e), limit=limit, start_date=start_date)
        raise


@mcp.tool()
@track_usage
async def get_budgets(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
    """Retrieve budget information."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    kwargs: Dict[str, Any] = {}
    if start_date:
        kwargs["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        kwargs["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()
    
    try:
        budgets = await mm_client.get_budgets(**kwargs)
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
    """Analyze cashflow data."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    kwargs: Dict[str, Any] = {}
    if start_date:
        kwargs["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        kwargs["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()
    
    cashflow = await mm_client.get_cashflow(**kwargs)
    cashflow = convert_dates_to_strings(cashflow)
    return json.dumps(cashflow, indent=2)


@mcp.tool()
@track_usage
async def get_transaction_categories() -> str:
    """List all transaction categories."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    categories = await mm_client.get_transaction_categories()
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    # Convert date string to date object
    transaction_date = datetime.strptime(date, "%Y-%m-%d").date()
    
    result = await mm_client.create_transaction(
        amount=amount,
        description=description,
        category_id=category_id,
        account_id=account_id,
        date=transaction_date,
        notes=notes
    )
    result = convert_dates_to_strings(result)
    return json.dumps(result, indent=2)


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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
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
    
    result = await mm_client.update_transaction(**updates)
    result = convert_dates_to_strings(result)
    return json.dumps(result, indent=2)


@mcp.tool()
@track_usage
async def get_account_holdings() -> str:
    """Get investment portfolio data from brokerage accounts."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        holdings = await mm_client.get_account_holdings()
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    kwargs: Dict[str, Any] = {"account_id": account_id}
    if start_date:
        kwargs["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        kwargs["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()
    
    try:
        history = await mm_client.get_account_history(**kwargs)
        history = convert_dates_to_strings(history)
        return json.dumps(history, indent=2)
    except Exception as e:
        log.error("Failed to fetch account history", error=str(e), account_id=account_id)
        raise


@mcp.tool()
@track_usage
async def get_institutions() -> str:
    """Get linked financial institutions."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        institutions = await mm_client.get_institutions()
        institutions = convert_dates_to_strings(institutions)
        return json.dumps(institutions, indent=2)
    except Exception as e:
        log.error("Failed to fetch institutions", error=str(e))
        raise


@mcp.tool()
@track_usage
async def get_recurring_transactions() -> str:
    """Get scheduled recurring transactions."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        recurring = await mm_client.get_recurring_transactions()
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        result = await mm_client.set_budget_amount(category_id=category_id, amount=amount)
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        result = await mm_client.create_manual_account(
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
async def get_transactions_batch(
    queries: str
) -> str:
    """Execute multiple transaction queries efficiently in batch.
    
    Args:
        queries: JSON string of query objects, each with optional: limit, offset, start_date, end_date, account_id, category_id
        
    Example:
        [
            {"start_date": "last month", "category_id": "cat123"},
            {"account_id": "acc456", "limit": 50},
            {"start_date": "this year", "end_date": "today"}
        ]
    """
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        query_list = json.loads(queries)
        if not isinstance(query_list, list):
            raise ValueError("Queries must be a JSON array")
        
        log.info("Executing batch transaction queries", count=len(query_list))
        
        async def execute_single_query(query: Dict[str, Any]) -> Dict[str, Any]:
            filters = build_date_filter(query.get("start_date"), query.get("end_date"))
            
            if query.get("account_id"):
                filters["account_id"] = query["account_id"]
            if query.get("category_id"):
                filters["category_id"] = query["category_id"]
            
            transactions = await mm_client.get_transactions(
                limit=query.get("limit", 100),
                offset=query.get("offset", 0),
                **filters
            )
            return {
                "query": query,
                "results": convert_dates_to_strings(transactions),
                "count": len(transactions) if isinstance(transactions, list) else 0
            }
        
        # Execute all queries in parallel for efficiency
        import asyncio
        results = await asyncio.gather(*[execute_single_query(q) for q in query_list])
        
        batch_result = {
            "batch_summary": {
                "total_queries": len(query_list),
                "total_transactions": sum(r["count"] for r in results)
            },
            "results": results
        }
        
        log.info("Batch queries completed", 
                query_count=len(query_list), 
                total_transactions=batch_result["batch_summary"]["total_transactions"])
        
        return json.dumps(batch_result, indent=2)
        
    except json.JSONDecodeError:
        log.error("Invalid JSON in batch queries")
        raise ValueError("Queries parameter must be valid JSON")
    except Exception as e:
        log.error("Failed to execute batch queries", error=str(e))
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        log.info("Generating spending summary", start_date=start_date, end_date=end_date, group_by=group_by)
        
        # Get transactions for the period
        filters = build_date_filter(start_date, end_date)
        transactions = await mm_client.get_transactions(limit=1000, **filters)
        
        if not isinstance(transactions, list):
            transactions = []
        
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        result = await mm_client.request_accounts_refresh()
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        # Parse the period into date filters
        filters = build_date_filter(period, None)
        
        # Execute all API calls in parallel for maximum efficiency
        accounts_task = mm_client.get_accounts()
        budgets_task = mm_client.get_budgets(**filters)
        cashflow_task = mm_client.get_cashflow(**filters)
        transactions_task = mm_client.get_transactions(limit=500, **filters)
        categories_task = mm_client.get_transaction_categories()
        
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
            results["transactions"] = convert_dates_to_strings(transactions)
            # Add intelligent transaction analysis
            if isinstance(transactions, list):
                results["transaction_summary"] = {
                    "total_count": len(transactions),
                    "total_income": sum(float(t.get("amount", 0)) for t in transactions if float(t.get("amount", 0)) > 0),
                    "total_expenses": sum(abs(float(t.get("amount", 0))) for t in transactions if float(t.get("amount", 0)) < 0),
                    "unique_categories": len(set(t.get("category", {}).get("name", "Unknown") for t in transactions if isinstance(t.get("category"), dict))),
                    "unique_accounts": len(set(t.get("account", {}).get("name", "Unknown") for t in transactions if isinstance(t.get("account"), dict)))
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
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    try:
        from dateutil.relativedelta import relativedelta
        
        # Calculate date ranges for analysis
        end_date = datetime.now().date()
        start_date = end_date - relativedelta(months=lookback_months)
        
        # Batch API calls for comprehensive data
        transactions_task = mm_client.get_transactions(
            limit=2000, 
            start_date=start_date, 
            end_date=end_date
        )
        budgets_task = mm_client.get_budgets(start_date=start_date, end_date=end_date)
        accounts_task = mm_client.get_accounts()
        categories_task = mm_client.get_transaction_categories()
        
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
        
        if not isinstance(transactions, Exception) and isinstance(transactions, list):
            # Monthly spending trends
            monthly_data = {}
            category_totals = {}
            account_usage = {}
            
            for txn in transactions:
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
        analysis["_metadata"] = {
            "api_calls_made": 4,
            "total_transactions_analyzed": len(transactions) if isinstance(transactions, list) else 0,
            "analysis_timestamp": datetime.now().isoformat()
        }
        
        log.info("Spending pattern analysis completed",
                lookback_months=lookback_months,
                transactions_analyzed=len(transactions) if isinstance(transactions, list) else 0,
                include_forecasting=include_forecasting)
        
        return json.dumps(analysis, indent=2)
        
    except Exception as e:
        log.error("Failed to analyze spending patterns", error=str(e), lookback_months=lookback_months)
        raise


@mcp.tool()
@track_usage
async def get_usage_analytics() -> str:
    """Get usage analytics to understand tool usage patterns and optimize batching."""
    try:
        # Analyze current session usage patterns
        analytics = {
            "session_id": current_session_id,
            "total_tools_called": sum(len(calls) for calls in usage_patterns.values()),
            "tools_usage_frequency": {tool: len(calls) for tool, calls in usage_patterns.items()},
            "common_patterns": [],
            "optimization_suggestions": []
        }
        
        # Identify common usage patterns
        if len(usage_patterns) > 1:
            # Look for tools called in sequence within short time windows
            all_calls = []
            for tool_calls in usage_patterns.values():
                all_calls.extend(tool_calls)
            all_calls.sort(key=lambda x: x["timestamp"])
            
            # Identify potential batching opportunities
            time_window = 30  # 30 seconds
            sequences = []
            current_sequence = []
            
            for call in all_calls:
                if not current_sequence or call["timestamp"] - current_sequence[-1]["timestamp"] <= time_window:
                    current_sequence.append(call)
                else:
                    if len(current_sequence) > 1:
                        sequences.append([c["tool_name"] for c in current_sequence])
                    current_sequence = [call]
            
            if len(current_sequence) > 1:
                sequences.append([c["tool_name"] for c in current_sequence])
            
            analytics["common_patterns"] = sequences
            
            # Generate optimization suggestions
            suggestions = []
            for seq in sequences:
                if len(seq) >= 2:
                    if "get_accounts" in seq and "get_transactions" in seq:
                        suggestions.append("Consider using get_complete_financial_overview instead of separate get_accounts + get_transactions calls")
                    if seq.count("get_transactions") > 1:
                        suggestions.append("Multiple get_transactions calls detected - consider using get_transactions_batch")
                    if "get_transactions" in seq and "get_budgets" in seq and "get_cashflow" in seq:
                        suggestions.append("Full financial analysis pattern detected - use get_complete_financial_overview for better performance")
            
            analytics["optimization_suggestions"] = list(set(suggestions))
        
        # Performance metrics
        execution_times = []
        for calls in usage_patterns.values():
            execution_times.extend([call.get("execution_time", 0) for call in calls])
        
        if execution_times:
            analytics["performance_metrics"] = {
                "avg_execution_time": sum(execution_times) / len(execution_times),
                "max_execution_time": max(execution_times),
                "min_execution_time": min(execution_times),
                "total_execution_time": sum(execution_times)
            }
        
        log.info("Usage analytics generated", total_calls=analytics["total_tools_called"])
        return json.dumps(analytics, indent=2)
        
    except Exception as e:
        log.error("Failed to generate usage analytics", error=str(e))
        raise


async def main() -> None:
    """Main entry point for the server."""
    # Initialize the MonarchMoney client
    try:
        await initialize_client()
    except Exception as e:
        print(f"Failed to initialize MonarchMoney client: {e}", file=sys.stderr)
        return
    
    # Run the FastMCP server with stdio transport
    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())