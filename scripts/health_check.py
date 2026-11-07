#!/usr/bin/env python3
"""Check Monarch Money authentication and API connectivity.

Run with: uv run --env-file .env scripts/health_check.py

Requires MONARCH_EMAIL and MONARCH_PASSWORD; MONARCH_MFA_SECRET is optional.
Uses the upstream client's default session handling.
"""

import asyncio
import os
import sys

from monarchmoney import MonarchMoney


async def health_check() -> bool:
    """Test basic API connectivity and authentication."""
    email = os.environ.get("MONARCH_EMAIL")
    password = os.environ.get("MONARCH_PASSWORD")
    mfa_secret = os.environ.get("MONARCH_MFA_SECRET")

    if not email or not password:
        print("❌ MONARCH_EMAIL and MONARCH_PASSWORD environment variables required")
        print("   Run with: uv run --env-file .env scripts/health_check.py")
        return False

    print("Testing Monarch Money API connectivity...")
    print(f"  Email: {email}")

    mm = MonarchMoney()

    print("\n1. Testing authentication...")
    try:
        await mm.login(email, password, mfa_secret_key=mfa_secret)
        print("   ✅ Login successful")
    except Exception as e:
        print(f"   ❌ Login failed: {type(e).__name__}: {e}")
        return False

    print("\n2. Testing get_accounts API...")
    try:
        accounts = await mm.get_accounts()
        count = len(accounts.get("accounts", []))
        print(f"   ✅ Got {count} accounts")
    except Exception as e:
        print(f"   ❌ Get accounts failed: {type(e).__name__}: {e}")
        return False

    print("\n3. Testing get_transactions API...")
    try:
        txns = await mm.get_transactions(limit=5)
        if isinstance(txns, dict):
            txn_list = txns.get("allTransactions", {}).get("results", [])
        else:
            txn_list = txns
        print(f"   ✅ Got {len(txn_list)} transactions")
    except Exception as e:
        print(f"   ❌ Get transactions failed: {type(e).__name__}: {e}")
        return False

    print("\n4. Testing get_budgets API...")
    try:
        budgets = await mm.get_budgets()
        print("   ✅ Got budgets response")
    except Exception as e:
        print(f"   ❌ Get budgets failed: {type(e).__name__}: {e}")
        return False

    print("\n" + "=" * 50)
    print("✅ All health checks passed! API is working.")
    print("=" * 50)
    return True


def main() -> int:
    """Run health check and return exit code."""
    return 0 if asyncio.run(health_check()) else 1


if __name__ == "__main__":
    sys.exit(main())
