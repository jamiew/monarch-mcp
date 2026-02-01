#!/usr/bin/env python3
"""Health check script to verify Monarch Money API connectivity.

Run with: uv run scripts/health_check.py

Requires environment variables:
  MONARCH_EMAIL, MONARCH_PASSWORD, MONARCH_MFA_SECRET
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from monarchmoney import MonarchMoney


async def health_check() -> bool:
    """Test basic API connectivity and authentication."""
    email = os.environ.get("MONARCH_EMAIL")
    password = os.environ.get("MONARCH_PASSWORD")
    mfa_secret = os.environ.get("MONARCH_MFA_SECRET")

    if not email or not password:
        print("❌ MONARCH_EMAIL and MONARCH_PASSWORD environment variables required")
        print("   Set them in .env file or export them")
        return False

    print(f"Testing Monarch Money API connectivity...")
    print(f"  Email: {email}")

    mm = MonarchMoney()

    # Test 1: Login
    print("\n1. Testing authentication...")
    try:
        await mm.login(email, password, mfa_secret_key=mfa_secret)
        print("   ✅ Login successful")
    except Exception as e:
        print(f"   ❌ Login failed: {type(e).__name__}: {e}")
        return False

    # Test 2: Get accounts
    print("\n2. Testing get_accounts API...")
    try:
        accounts = await mm.get_accounts()
        count = len(accounts.get("accounts", []))
        print(f"   ✅ Got {count} accounts")
    except Exception as e:
        print(f"   ❌ Get accounts failed: {type(e).__name__}: {e}")
        return False

    # Test 3: Get transactions (small limit)
    print("\n3. Testing get_transactions API...")
    try:
        txns = await mm.get_transactions(limit=5)
        # Handle both dict and list responses
        if isinstance(txns, dict):
            txn_list = txns.get("allTransactions", {}).get("results", [])
        else:
            txn_list = txns
        print(f"   ✅ Got {len(txn_list)} transactions")
    except Exception as e:
        print(f"   ❌ Get transactions failed: {type(e).__name__}: {e}")
        return False

    # Test 4: Get budgets
    print("\n4. Testing get_budgets API...")
    try:
        budgets = await mm.get_budgets()
        print(f"   ✅ Got budgets response")
    except Exception as e:
        print(f"   ❌ Get budgets failed: {type(e).__name__}: {e}")
        return False

    print("\n" + "=" * 50)
    print("✅ All health checks passed! API is working.")
    print("=" * 50)
    return True


def main() -> int:
    """Run health check and return exit code."""
    # Load .env if present
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        print(f"Loading credentials from {env_file}")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    # Strip quotes from value
                    value = value.strip().strip('"').strip("'")
                    os.environ[key.strip()] = value

    success = asyncio.run(health_check())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
