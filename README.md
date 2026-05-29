<!-- mcp-name: io.github.jamiew/monarch-mcp -->
# Monarch Money MCP Server

An [MCP](https://modelcontextprotocol.io/) server for [Monarch Money](https://www.monarchmoney.com/) — gives AI assistants like Claude access to your financial accounts, transactions, budgets, and more.

Originally forked from [@colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp) but has diverged into a full rewrite on a modern **FastMCP** architecture. It's grown from a handful of tools to a broad toolset — adding server-side transaction search, parallel **bulk transaction updates**, multi-month **spending-pattern analysis** with forecasting, and a single-call **financial overview** that fans out to five Monarch APIs at once. Responses are tuned hard for token efficiency: the default compact transaction format cuts payload size by **~80%**, categories return just `id`+`name` unless you ask for more, and every tool accepts a `verbose` flag when you want the full payload. It also ships MCP **resources** and guided **prompts**, natural-language date parsing ("last month", "30 days ago"), and proper read/write **tool annotations** so clients know what's safe to call.

Built on the [`monarchmoneycommunity`](https://github.com/bradleyseanf/monarchmoneycommunity) library by [@bradleyseanf](https://github.com/bradleyseanf) — an actively-maintained community fork that tracks the latest Monarch Money API changes (the `api.monarch.com` domain move, gql 4.0, auth persistence) with full MFA support, pinned to a specific commit for reproducible builds. It descends from the original [`monarchmoney`](https://github.com/hammem/monarchmoney) library by [@hammem](https://github.com/hammem), which is no longer actively maintained.

## Features

- **Tools** covering accounts, transactions, budgets, cashflow, investments, categories, recurring transactions, and spending analysis
- **Structured output** — every tool returns a typed schema (`outputSchema` + machine-readable structured content) with a text fallback for older clients
- **MCP resources** for quick access to categories, accounts, and institutions, plus parameterized templates for per-account holdings and history (`accounts://{account_id}/holdings|history`)
- **MCP prompts** for guided financial analysis workflows, with live argument autocompletion
- **Smart output formatting** — compact transaction format reduces token usage by ~80%
- **Natural language dates** — "last month", "30 days ago", "this year" all work
- **Batch operations** — parallel multi-account queries, bulk transaction updates, with progress reporting
- **Spending analysis** — multi-month trend analysis with category/account breakdowns
- **Tool annotations & titles** — read/write metadata and human-friendly titles for MCP clients

## Setup

**One-line install, no clone, no absolute-path wrangling.** The server is published to [PyPI](https://pypi.org/project/monarch-mcp-jamiew/), so [`uv`](https://docs.astral.sh/uv/) runs it on demand with `uvx monarch-mcp-jamiew`. You'll need `uv` installed and your Monarch credentials (see [Getting your MFA secret](#getting-your-mfa-secret) below).

### Standard config

Every MCP client uses the same shape — command `uvx`, package `monarch-mcp-jamiew`, and your three credentials as env vars:

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

Edit your config file (create it if it doesn't exist) and add the [standard config](#standard-config) above under `mcpServers`:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
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

Drop the [standard config](#standard-config) into a `.mcp.json` file at your project root. Claude Code (project scope) and most clients auto-load it.

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

Most accept the same [standard config](#standard-config) — drop it into the client's MCP config (e.g. Cursor's `~/.cursor/mcp.json`, or VS Code via `code --add-mcp`). Anything that speaks MCP over stdio works.

Not sure how? Tell your agent:

> Install the Monarch Money MCP server from https://github.com/jamiew/monarch-mcp — it's on PyPI as `monarch-mcp-jamiew`, runs via `uvx monarch-mcp-jamiew`, and needs env vars `MONARCH_EMAIL`, `MONARCH_PASSWORD`, and `MONARCH_MFA_SECRET`.

</details>

<details>
<summary><b>From source (development)</b></summary>

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
> The `claude mcp add` / `codex mcp add` one-liners put your credentials in shell history. If that bothers you, edit the client's config file directly (as shown for Claude Desktop / Codex above) instead.

### Getting your MFA secret

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
| `get_account_holdings` | Investment holdings for an account (requires `account_id`) |
| `get_account_history` | Account balance history |
| `get_institutions` | Linked financial institutions |
| `get_recurring_transactions` | Recurring transaction detection |
| `set_budget_amount` | Set a budget category amount |
| `create_manual_account` | Create a manually-tracked account |
| `refresh_accounts` | Trigger account data refresh |
| `get_spending_summary` | Spending aggregated by category, account, or month |
| `get_complete_financial_overview` | Combined 5-API call in parallel |
| `analyze_spending_patterns` | Multi-month trend analysis |

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

### Releasing

Cut a release with the `/release` flow (bump version in `pyproject.toml` → commit → tag `vX.Y.Z` → push → `gh release create`). Publishing the GitHub release triggers [`.github/workflows/publish.yml`](.github/workflows/publish.yml), which builds and pushes to **PyPI** and the **MCP Registry** via OIDC trusted publishing — no API tokens are stored anywhere. The workflow injects the tag version into `server.json` automatically, so `pyproject.toml` is the only version field you bump by hand.

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

- The server runs locally on your machine — your credentials live in your MCP client config and **never pass through the LLM**. Only the financial data you actually query is returned to the assistant.
- Your credentials have full account access — treat them like passwords
- The MFA secret (TOTP key) provides ongoing access
- Session files in `.mm/` contain auth tokens — keep them secure
- Never commit `.env` or `.mcp.json` files to version control
- This is an unofficial API — Monarch Money could change or restrict access at any time

## Credits

This project started as a fork of [colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp) by [@colvint](https://github.com/colvint). Thanks for the original implementation!

