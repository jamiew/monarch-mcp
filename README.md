<!-- mcp-name: io.github.jamiew/monarch-mcp -->
# Monarch Money MCP Server

An [MCP](https://modelcontextprotocol.io/) server that lets AI assistants query and update your [Monarch Money](https://www.monarchmoney.com/) accounts, transactions, and budgets.

A **FastMCP** rewrite of [@colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp), with transaction search, bulk updates, spending analysis, and a combined financial overview.

Built on [`monarchmoneycommunity`](https://github.com/bradleyseanf/monarchmoneycommunity) by [@bradleyseanf](https://github.com/bradleyseanf), a community fork of [`monarchmoney`](https://github.com/hammem/monarchmoney) by [@hammem](https://github.com/hammem). Source installs pin the client to a commit; PyPI installs use the published dependency.

## Features

- **Tools** covering accounts, transactions, budgets, cashflow, investments, categories, recurring transactions, and spending analysis
- **Structured output** with a typed schema (`outputSchema`), machine-readable content, and a text fallback for older clients
- **MCP resources** for quick access to categories, accounts, and institutions, plus parameterized templates for per-account holdings and history (`accounts://{account_id}/holdings|history`)
- **MCP prompts** for guided financial analysis workflows, with live argument autocompletion
- **Compact output** for transactions and categories, with optional full details
- **Natural-language dates** such as "last month", "30 days ago", and "this year"
- **Batch operations** for combined queries and parallel transaction updates, with progress reporting
- **Spending analysis** — multi-month trend analysis with category/account breakdowns
- **Tool annotations and titles** to help clients distinguish reads from writes

## Setup

Install [`uv`](https://docs.astral.sh/uv/), then configure your client to run the [PyPI package](https://pypi.org/project/monarch-mcp-jamiew/) with `uvx monarch-mcp-jamiew`. You'll need your Monarch email and password, plus an [MFA secret](#getting-your-mfa-secret) if you use TOTP-based 2FA.

**Release status:** This README describes the current source checkout. Recurring date filters, `update_recurring_transaction`, stricter bulk-input validation, and the latest authentication fixes are unreleased. Use the [source setup](#from-source-development) for these changes; `uvx` runs the latest published release.

### Standard config

For clients that use an `mcpServers` configuration, use the following. Other clients may use different keys or setup commands.

```json
{
  "mcpServers": {
    "monarch-money": {
      "command": "uvx",
      "args": ["monarch-mcp-jamiew"],
      "env": {
        "MONARCH_EMAIL": "your-email@example.com",
        "MONARCH_PASSWORD": "your-password",
        "MONARCH_MFA_SECRET": "your-mfa-secret-key"
      }
    }
  }
}
```

Pick your client below for the exact steps.

<details>
<summary><b>Claude Desktop</b></summary>

Add the `monarch-money` entry from the [standard config](#standard-config) to your config file's `mcpServers` object (create the file if needed):

- **macOS**: `~/Library/Application\ Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Then fully quit and reopen Claude Desktop.

</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add monarch-money \
  -e MONARCH_EMAIL=your-email@example.com \
  -e MONARCH_PASSWORD=your-password \
  -e MONARCH_MFA_SECRET=your-mfa-secret-key \
  -- uvx monarch-mcp-jamiew
```

Add `-s user` to make it available across all your projects. Verify with `claude mcp list`.

</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex mcp add monarch-money \
  --env MONARCH_EMAIL=your-email@example.com \
  --env MONARCH_PASSWORD=your-password \
  --env MONARCH_MFA_SECRET=your-mfa-secret-key \
  -- uvx monarch-mcp-jamiew
```

Or add the equivalent block to `~/.codex/config.toml`:

```toml
[mcp_servers.monarch-money]
command = "uvx"
args = ["monarch-mcp-jamiew"]
env = { MONARCH_EMAIL = "your-email@example.com", MONARCH_PASSWORD = "your-password", MONARCH_MFA_SECRET = "your-mfa-secret-key" }
```

</details>

<details>
<summary><b><code>.mcp.json</code> (project-scoped)</b></summary>

For Claude Code's project scope, save the [standard config](#standard-config) as `.mcp.json` in your project root. Keep credential-bearing files out of version control.

</details>

<details>
<summary><b>Hermes</b></summary>

Add to `~/.hermes/config.yaml` under `mcp_servers:`, then `/reload-mcp` (or restart Hermes):

```yaml
mcp_servers:
  monarch-money:
    command: uvx
    args: ["monarch-mcp-jamiew"]
    env:
      MONARCH_EMAIL: "your-email@example.com"
      MONARCH_PASSWORD: "your-password"
      MONARCH_MFA_SECRET: "your-mfa-secret-key"
```

</details>

<details>
<summary><b>OpenClaw</b></summary>

Add the [standard config](#standard-config) to `~/.openclaw/openclaw.json` under `mcpServers`, then restart the gateway.

</details>

<details>
<summary><b>Any other MCP client (Cursor, VS Code, Windsurf, Cline, Zed, …)</b></summary>

Use your client's instructions for a local stdio MCP server. Set the command to `uvx`, the argument to `monarch-mcp-jamiew`, and the credential environment variables shown above. Config keys and file locations vary by client.

Not sure how? Tell your agent:

> Install the Monarch Money MCP server from https://github.com/jamiew/monarch-mcp. The PyPI package is `monarch-mcp-jamiew`, run via `uvx monarch-mcp-jamiew`. It needs `MONARCH_EMAIL`, `MONARCH_PASSWORD`, and `MONARCH_MFA_SECRET` for TOTP-based 2FA.

</details>

<details>
<summary id="from-source-development"><b>From source (development)</b></summary>

To run against a local checkout (and the git-pinned `monarchmoneycommunity` lib):

```bash
git clone https://github.com/jamiew/monarch-mcp
cd monarch-mcp
uv sync
```

Then point your client at the local copy with absolute paths (find them with `which uv` and `pwd`):

```json
{
  "mcpServers": {
    "monarch-money": {
      "command": "/abs/path/to/uv",
      "args": ["--directory", "/abs/path/to/monarch-mcp", "run", "python", "server.py"],
      "env": {
        "MONARCH_EMAIL": "your-email@example.com",
        "MONARCH_PASSWORD": "your-password",
        "MONARCH_MFA_SECRET": "your-mfa-secret-key"
      }
    }
  }
}
```

</details>

> [!NOTE]
> The `claude mcp add` and `codex mcp add` commands can save credentials in shell history. Edit the client's config directly to avoid that, and protect the config file.

### Getting your MFA secret

1. Go to Monarch Money settings and enable 2FA
2. When shown the QR code, look for "Can't scan?" or "Enter manually"
3. Copy the TOTP secret key, not the rotating six-digit code
4. Use this as your `MONARCH_MFA_SECRET`

## Tools

The source checkout exposes these 22 tools. See [release status](#setup) for unpublished changes.

| Tool | Description |
|------|-------------|
| `get_accounts` | List accounts with balances |
| `get_transactions` | Transactions with date/account/category filtering |
| `search_transactions` | Search by merchant name or keyword |
| `get_transaction_categories` | Category list (compact by default) |
| `create_transaction` | Create a manual transaction |
| `update_transaction` | Update a single transaction |
| `update_transactions_bulk` | Update multiple transactions in parallel |
| `get_transaction_splits` | Read a transaction's splits |
| `update_transaction_splits` | Replace all splits; an empty list removes them |
| `get_budgets` | Budget data and spending analysis |
| `get_cashflow` | Income and expense analysis |
| `get_account_holdings` | Investment holdings for an account (requires `account_id`) |
| `get_account_history` | Account balance history |
| `get_institutions` | Linked financial institutions |
| `get_recurring_transactions` | Scheduled occurrences within a date range |
| `update_recurring_transaction` | Change a merchant's recurring schedule |
| `set_budget_amount` | Set a budget category amount |
| `create_manual_account` | Create a manually tracked account |
| `refresh_accounts` | Trigger account data refresh |
| `get_spending_summary` | Spending aggregated by category, account, or month |
| `get_complete_financial_overview` | Combine accounts, budgets, cashflow, transactions, and categories |
| `analyze_spending_patterns` | Multi-month trend analysis |

### Recurring transactions

`get_recurring_transactions(start_date, end_date)` returns a schedule forecast, not posted transaction history.
Dates accept ISO or natural-language input. No dates means the current calendar
month; one date fills the missing bound from that date's month.

Each occurrence includes its stream, account, category, and matched
`transactionId`, when present. `isPast` does not mean paid. For recorded
transactions, use `get_transactions(is_recurring=True)` instead.

`update_recurring_transaction` changes the merchant-wide schedule, not one
occurrence. Use `stream.merchant.id` (not `stream.id` or `transactionId`) and the current merchant name to avoid renaming it.
Pass only settings you want to change: `frequency`, `base_date`, `amount`,
`is_recurring`, or `is_active`. Omitted settings stay unchanged. Use Monarch's
existing frequency and signed amount. This does not cancel subscriptions, move
money, or create posted transactions.

### Transaction format

`get_transactions` and `search_transactions` return compact records by default:

```json
{
  "id": "txn_123",
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

`pending` appears only when true; `notes` appears only when nonempty. Set `verbose=True` on `get_transactions` or `search_transactions` for full transaction details, or on `get_transaction_categories` for full category details.

## Session management

Sessions are cached in `~/.monarch-mcp/` for faster subsequent logins (override the location with the `MONARCH_SESSION_DIR` env var). If you hit auth issues:

- Delete `~/.monarch-mcp/session.pickle` to clear the cached session
- Set `MONARCH_FORCE_LOGIN=true` in your env config to force a fresh login
- Make sure your system clock is accurate (required for TOTP)

## Development

### Local setup

For live checks, create a `.env` file (git-ignored) and load it explicitly with `uv --env-file`:

```bash
MONARCH_EMAIL="your-email@example.com"
MONARCH_PASSWORD="your-password"
MONARCH_MFA_SECRET="YOUR_TOTP_SECRET_KEY"
```

### Tests

```bash
uv run pytest tests/ -v                          # offline; live tests are skipped
MONARCH_RUN_INTEGRATION=true uv run --env-file .env pytest tests/test_integration.py -v
uv run --env-file .env scripts/health_check.py    # live API connectivity check
```

Integration tests never load `.env` themselves or read/write saved sessions.

### CI checks

Run all checks locally (same as GitHub Actions CI):

```bash
uv run python scripts/ci.py
```

### Releasing

Use `/release` to bump `pyproject.toml`, commit, tag `vX.Y.Z`, push, and create a GitHub release. Publishing the release triggers [`.github/workflows/publish.yml`](.github/workflows/publish.yml), which publishes to PyPI and the MCP Registry through OIDC trusted publishing. The workflow sets `server.json` versions from the tag; only bump `pyproject.toml` by hand.

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

> **Warning:** This server uses unofficial Monarch Money API access. Your credentials grant full account access, including writes.

- The server runs locally and returns requested financial data to your MCP client. Review the client's privacy settings and tool approvals.
- Protect your password and MFA secret. The TOTP secret enables ongoing code generation.
- Session files in `~/.monarch-mcp/` contain auth tokens. Protect them and any custom `MONARCH_SESSION_DIR`.
- Logs can include financial input values and error details. Review them before sharing.
- Never commit credential-bearing `.env`, `.mcp.json`, or client config files.
- Monarch Money may change or restrict unofficial API access at any time.

## Credits

This project started as a fork of [colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp) by [@colvint](https://github.com/colvint). Thanks for the original implementation!

