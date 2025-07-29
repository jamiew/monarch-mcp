#!/usr/bin/env python3
"""MonarchMoney MCP Server - Provides access to Monarch Money financial data via MCP protocol."""

import os
import asyncio
import json
from typing import Optional, List, Union, Dict, Any
from datetime import datetime, date
from pathlib import Path

from pydantic import BaseModel, Field, validator
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from monarchmoney import MonarchMoney

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

# Initialize the FastMCP server
mcp = FastMCP("monarch-money")

# Global variable to store the MonarchMoney client
mm_client: Optional[MonarchMoney] = None
session_file = Path.home() / ".monarchmoney_session"


async def initialize_client() -> None:
    """Initialize the MonarchMoney client with authentication."""
    global mm_client
    
    email = os.getenv("MONARCH_EMAIL")
    password = os.getenv("MONARCH_PASSWORD")
    mfa_secret = os.getenv("MONARCH_MFA_SECRET")
    
    if not email or not password:
        raise ValueError("MONARCH_EMAIL and MONARCH_PASSWORD environment variables are required")
    
    mm_client = MonarchMoney()
    
    # Try to load existing session first
    if session_file.exists() and not os.getenv("MONARCH_FORCE_LOGIN"):
        try:
            mm_client.load_session(str(session_file))
            # Test if session is still valid
            await mm_client.get_accounts()
            print("Loaded existing session successfully")
            return
        except Exception:
            print("Existing session invalid, logging in fresh")
    
    # Login with credentials
    if mfa_secret:
        await mm_client.login(email, password, mfa_secret_key=mfa_secret)
    else:
        await mm_client.login(email, password)
    
    # Save session for future use
    mm_client.save_session(str(session_file))
    print("Logged in and saved session")


# FastMCP Tool definitions using decorators

@mcp.tool()
async def get_accounts() -> str:
    """Retrieve all linked financial accounts."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    accounts = await mm_client.get_accounts()
    accounts = convert_dates_to_strings(accounts)
    return json.dumps(accounts, indent=2)


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
async def refresh_accounts() -> str:
    """Request a refresh of all account data from financial institutions."""
    if not mm_client:
        raise ValueError("MonarchMoney client not initialized")
    
    result = await mm_client.request_accounts_refresh()
    result = convert_dates_to_strings(result)
    return json.dumps(result, indent=2)


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