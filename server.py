#!/usr/bin/env python3
"""MonarchMoney MCP Server - Provides access to Monarch Money financial data via MCP protocol."""

import os
import asyncio
import json
from typing import Optional, List, Union, Dict, Any
from datetime import datetime, date
from pathlib import Path

import structlog
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

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)

log = structlog.get_logger()

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
        log.error("Missing required environment variables")
        raise ValueError("MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required")
    
    mm_client = MonarchMoney()
    
    # Try to load existing session first
    if session_file.exists() and not os.getenv("MONARCH_FORCE_LOGIN"):
        try:
            mm_client.load_session(str(session_file))
            # Test if session is still valid
            await mm_client.get_accounts()
            log.info("Session loaded successfully")
            return
        except Exception as e:
            log.warning("Session invalid, attempting fresh login", error=str(e))
    
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
        log.info("Authentication successful, session saved")
        
    except RequireMFAException:
        log.error("MFA required but not provided")
        raise ValueError("Multi-factor authentication required but MONARCH_MFA_SECRET not set")
    except Exception as e:
        log.error("Authentication failed", error=str(e))
        raise


# FastMCP Tool definitions using decorators

@mcp.tool()
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
async def get_transactions(
    limit: int = 100,
    offset: int = 0, 
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_id: Optional[str] = None,
    category_id: Optional[str] = None
) -> str:
    """Fetch transactions with optional filtering."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    # Build filter parameters
    filters: Dict[str, Any] = {}
    if start_date:
        filters["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        filters["end_date"] = datetime.strptime(end_date, "%Y-%m-%d").date()
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
    return json.dumps(transactions, indent=2)


@mcp.tool()
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
async def get_transaction_categories() -> str:
    """List all transaction categories."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    categories = await mm_client.get_transaction_categories()
    categories = convert_dates_to_strings(categories)
    return json.dumps(categories, indent=2)


@mcp.tool()
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


async def main() -> None:
    """Main entry point for the server."""
    # Initialize the MonarchMoney client
    try:
        await initialize_client()
    except Exception as e:
        print(f"Failed to initialize MonarchMoney client: {e}")
        return
    
    # Run the FastMCP server
    await mcp.run()


if __name__ == "__main__":
    asyncio.run(main())