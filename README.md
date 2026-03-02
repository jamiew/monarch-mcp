# Monarch Money MCP Server

An [MCP](https://modelcontextprotocol.io/) server for [Monarch Money](https://www.monarchmoney.com/) — gives AI assistants like Claude access to your financial accounts, transactions, budgets, and more.

Originally forked from [@colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp) but has diverged significantly with a full rewrite, many new features, and a modern FastMCP architecture.

## Features

- **19 tools** covering accounts, transactions, budgets, cashflow, investments, categories, goals, net worth, recurring transactions, and more
- **MCP resources** for quick access to categories, accounts, and institutions
- **MCP prompts** for guided financial analysis workflows
- **Smart output formatting** — compact transaction format reduces token usage by ~80%
- **Natural language dates** — "last month", "30 days ago", "this year" all work
- **Batch operations** — parallel multi-account queries, bulk transaction updates
- **Spending analysis** — multi-month trend analysis with category/account breakdowns
- **Tool annotations** — proper read/write metadata for MCP clients

## Setup

### 1. Install dependencies

```bash
cd /path/to/monarch-mcp
uv sync
```

### 2. Configure your MCP client

Add to your `.mcp.json` (Claude Desktop, Claude Code, etc.):

```json
{
  "mcpServers": {
    "monarch-money": {
      "command": "/path/to/uv",
      "args": [
        "--directory",
        "/path/to/monarch-mcp",
        "run",
        "python",
        "server.py"
      ],
      "env": {
        "MONARCH_EMAIL": "your-email@example.com",
        "MONARCH_PASSWORD": "your-password",
        "MONARCH_MFA_SECRET": "your-mfa-secret-key"
      }
    }
  }
}
```

Use absolute paths — find yours with `which uv` and `pwd`.

### 3. Get your MFA secret

1. Go to Monarch Money settings and enable 2FA
2. When shown the QR code, look for "Can't scan?" or "Enter manually"
3. Copy the secret key (a string like `T5SPVJIBRNPNNINFSH5W7RFVF2XYADYX`)
4. Use this as your `MONARCH_MFA_SECRET`

## Tools

| Tool | Description |
|------|-------------|
| `get_accounts` | List accounts with balances |
| `get_transactions` | Transactions with date/account/category filtering |
| `search_transactions` | Search by merchant name or keyword |
| `get_transaction_categories` | Category list (compact by default) |
| `create_transaction` | Create a manual transaction |
| `update_transaction` | Update a single transaction |
| `update_transactions_bulk` | Update multiple transactions in parallel |
| `get_budgets` | Budget data and spending analysis |
| `get_cashflow` | Income and expense analysis |
| `get_account_holdings` | Investment holdings |
| `get_account_history` | Account balance history |
| `get_institutions` | Linked financial institutions |
| `get_recurring_transactions` | Recurring transaction detection |
| `set_budget_amount` | Set a budget category amount |
| `create_manual_account` | Create a manually-tracked account |
| `refresh_accounts` | Trigger account data refresh |
| `get_complete_financial_overview` | Combined 5-API call in parallel |
| `analyze_spending_patterns` | Multi-month trend analysis |
| `get_usage_analytics` | Tool usage stats and optimization tips |

### Transaction format

By default, transactions return a compact format with the fields that matter:

```json
{
  "id": "123456789012345678",
  "date": "2025-03-15",
  "amount": -12.50,
  "merchant": "Corner Deli",
  "plaidName": "CORNER DELI NYC",
  "category": "Restaurants & Bars",
  "categoryId": "cat_001",
  "account": "Main Credit Card",
  "needsReview": true
}
```

`pending` and `notes` are included only when present. Set `verbose=True` on any tool for the full API response with all metadata.

## Session management

Sessions are cached in a `.mm` directory for faster subsequent logins. If you hit auth issues:

- Delete `.mm/session.pickle` to clear the cached session
- Set `MONARCH_FORCE_LOGIN=true` in your env config to force a fresh login
- Make sure your system clock is accurate (required for TOTP)

## Development

### Local setup

Create a `.env` file (git-ignored):

```bash
MONARCH_EMAIL="your-email@example.com"
MONARCH_PASSWORD="your-password"
MONARCH_MFA_SECRET="YOUR_TOTP_SECRET_KEY"
```

### Tests

```bash
uv run pytest tests/ -v                          # unit tests (no creds needed)
uv run pytest tests/test_integration.py -v        # integration tests (needs .env)
uv run scripts/health_check.py                    # quick API connectivity check
```

### CI checks

Run all checks locally (same as GitHub Actions CI):

```bash
uv run python scripts/ci.py
```

### Log analysis

Tools for measuring and optimizing token usage across MCP sessions:

```bash
uv run scripts/analyze_logs.py                    # full report
uv run scripts/analyze_logs.py --json             # JSON output
uv run scripts/eval_session.py snapshot           # mark log position
# ... use tools in Claude ...
uv run scripts/eval_session.py analyze            # analyze new entries
```

## Security

> **Warning**: Monarch Money does not provide an official API. This server uses unofficial API access that requires your actual account credentials. Use with appropriate caution.

- Your credentials have full account access — treat them like passwords
- The MFA secret (TOTP key) provides ongoing access
- Session files in `.mm/` contain auth tokens — keep them secure
- Never commit `.env` or `.mcp.json` files to version control
- This is an unofficial API — Monarch Money could change or restrict access at any time

## Credits

This project started as a fork of [colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp) by [@colvint](https://github.com/colvint). Thanks for the original implementation!

Built with the [MonarchMoney](https://github.com/hammem/monarchmoney) Python library by [@hammem](https://github.com/hammem) — a fantastic unofficial API for Monarch Money with full MFA support. We currently use the [community fork](https://github.com/bradleyseanf/monarchmoneycommunity) by [@bradleyseanf](https://github.com/bradleyseanf) which tracks the latest Monarch Money API changes.
