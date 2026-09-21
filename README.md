<!-- mcp-name: io.github.jamiew/monarch-mcp -->
# Monarch Money MCP Server

Use an AI assistant to read and update your [Monarch Money](https://www.monarchmoney.com/) accounts, transactions, and budgets through [MCP](https://modelcontextprotocol.io/).

## Why this fork?

This FastMCP rewrite adds these tools to [colvint's original server](https://github.com/colvint/monarch-money-mcp):

- **Search and bulk edits:** `search_transactions` finds merchants or keywords; `update_transactions_bulk` edits transactions in parallel with per-item results.
- **Spending analysis:** `get_spending_summary` groups totals by category, account, or month; `analyze_spending_patterns` compares months.
- **One-call overview:** `get_complete_financial_overview` combines accounts, budgets, cashflow, transactions, and categories.
- **Splits and recurring schedules:** read and replace transaction splits, view scheduled occurrences, and edit merchant-wide recurrence.

Unlike the original and [keithah's enhanced Python fork](https://github.com/keithah/monarch-money-mcp-enhanced-python), this server also provides:

- **Typed results:** structured output with `outputSchema`, plus a text fallback.
- **MCP resources and prompts:** account/category/institution resources, per-account holdings/history templates, and guided prompts with argument completion.
- **Assistant-friendly calls:** compact transaction/category records, natural-language dates, read/write labels, and progress on batch analysis.

Comparison checked September 14, 2026. Other forks overlap on financial tools; the enhanced Python fork exposes a broader library API. This project focuses on analysis workflows and MCP integration, not exposing every API method. See the [tool catalog](#tools).

## Setup

Install [`uv`](https://docs.astral.sh/uv/), then configure your MCP client to run `uvx monarch-mcp-jamiew`. You'll need your Monarch email and password, plus an [MFA secret](#getting-your-mfa-secret) for TOTP-based 2FA.

These features are included in [0.5.0](https://github.com/jamiew/monarch-mcp/releases/tag/v0.5.0), available through [PyPI](https://pypi.org/project/monarch-mcp-jamiew/).

### Standard config

For clients with an `mcpServers` config:

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

Set up a local stdio MCP server with command `uvx`, argument `monarch-mcp-jamiew`, and the credentials above. Follow your client's config format.

Not sure how? Tell your agent:

> Install the Monarch Money MCP server from https://github.com/jamiew/monarch-mcp. The PyPI package is `monarch-mcp-jamiew`, run via `uvx monarch-mcp-jamiew`. It needs `MONARCH_EMAIL`, `MONARCH_PASSWORD`, and `MONARCH_MFA_SECRET` for TOTP-based 2FA.

</details>

<details>
<summary id="from-source-development"><b>From source (development)</b></summary>

Source installs use a pinned `monarchmoneycommunity` commit:

```bash
git clone https://github.com/jamiew/monarch-mcp
cd monarch-mcp
uv sync --locked
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

The server exposes these 28 tools.

| Tool | Description |
|------|-------------|
| `get_accounts` | List accounts with balances |
| `get_transactions` | Transactions with date/account/category and pending/posted filtering |
| `search_transactions` | Search by merchant name or keyword, optionally pending/posted only |
| `get_transaction_categories` | Category list (compact by default) |
| `get_transaction_rules` | Page through compact automation rules in priority order |
| `create_transaction_rule` | Create a rule from merchant, statement, amount, account, or category criteria |
| `update_transaction_rule` | Edit a rule, optionally re-running it on existing transactions |
| `delete_transaction_rule` | Delete a rule; already-changed transactions keep their values |
| `get_household_members` | Household members and IDs for ownership updates |
| `create_transaction` | Create a manual transaction |
| `update_transaction` | Update transaction fields or assign ownership |
| `update_transactions_bulk` | Update fields or owners with per-item success/failure |
| `get_transaction_splits` | Read a transaction's splits |
| `update_transaction_splits` | Replace all splits; an empty list removes them |
| `get_budgets` | Budget data and spending analysis |
| `get_cashflow` | Income and expense analysis |
| `get_account_holdings` | Investment holdings for an account (requires `account_id`) |
| `get_all_holdings` | Holdings grouped by brokerage account; excludes other account types |
| `get_account_history` | Paginated balance history with inclusive, locally applied ISO date bounds |
| `get_institutions` | Linked financial institutions |
| `get_recurring_transactions` | Scheduled occurrences within a date range |
| `update_recurring_transaction` | Change a merchant's recurring schedule |
| `set_budget_amount` | Set a budget category amount |
| `create_manual_account` | Create a manually tracked account |
| `refresh_accounts` | Trigger account data refresh |
| `get_spending_summary` | Spending aggregated by category, account, or month |
| `get_complete_financial_overview` | Compact account, transaction, budget, and cashflow summaries; full sections opt-in |
| `analyze_spending_patterns` | Monthly trends and forecasts, with compact budgets and explicit upstream errors |

Use `is_pending=True` for pending transactions or `False` for posted ones; omit it
for both. Single and bulk updates accept `owner_user_id` from `get_household_members`.
An empty string sets Shared ownership; omitted/null leaves ownership unchanged.
Assignments override inherited ownership. Inspect `ownedByUser` with `verbose=True`;
the update response does not include it.

Rules default to 25 per page (maximum 100); history defaults to 100 (maximum 1,000).
Use `limit`, `offset`, and returned `next_offset` to continue; `total_count` covers all
matching records. Rule details remain available with `verbose=True`.

Rule actions can set a category, merchant name, tags, hidden-from-reports, or review
status. Rule edits leave omitted fields unchanged; `[]` or `""` clears a value. Monarch
replaces the whole rule on update, so the server resends the current rule with your
changes, including settings it cannot edit such as owners, goals, and splits.
`apply_to_existing_transactions=True` also rewrites matching past transactions, which
is hard to undo.

Overviews and spending analysis default to compact summaries; `verbose=True` restores
full sections. Transaction samples are capped at 500 and 2,000 respectively, even
in verbose mode. Check `batch_metadata.transactions_truncated` before treating
totals as complete; `null` means the upstream count was unavailable.

### Recurring transactions

`get_recurring_transactions(start_date, end_date)` returns a forecast, not posted history.
Dates accept ISO or natural language. No dates selects this month; one date fills
the missing bound from that month.

Occurrences include stream, account, category, and a matched `transactionId` when
available. `isPast` does not mean paid. Use `get_transactions(is_recurring=True)`
for recorded transactions; do not count forecasts and posted matches twice.

`update_recurring_transaction` changes a merchant-wide schedule, not one occurrence.
Use `stream.merchant.id`, not `stream.id` or `transactionId`, and the current merchant
name to avoid renaming it. Pass only settings to change: `frequency`, `base_date`,
`amount`, `is_recurring`, or `is_active`. Omitted settings stay unchanged.
Use Monarch's frequency and signed amount. This does not cancel subscriptions,
move money, or create posted transactions.

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

Integration tests share one fresh login to avoid MFA reuse and login throttling. They never load `.env` themselves or read/write saved sessions.

### CI checks

Run the same checks as CI:

```bash
uv run python scripts/ci.py
```

### Releasing

Use `/release` to bump `pyproject.toml`, commit, tag `vX.Y.Z`, push, and create a GitHub release. The [publish workflow](.github/workflows/publish.yml) publishes to PyPI and the MCP Registry through OIDC, setting `server.json` versions from the tag.

### Log analysis

Measure tool calls and output sizes:

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

Forked from [colvint/monarch-money-mcp](https://github.com/colvint/monarch-money-mcp). API access uses [bradleyseanf/monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity), based on [hammem/monarchmoney](https://github.com/hammem/monarchmoney). Source installs pin a commit; PyPI installs use the published library.

