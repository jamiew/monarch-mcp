# Monarch Money MCP Server

An MCP (Model Context Protocol) server that provides access to Monarch Money financial data and operations.

## Features

- **Account Management**: List and retrieve account information
- **Transaction Operations**: Get transactions with filtering by date range, accounts, and categories
- **Budget Analysis**: Access budget data and spending insights
- **Category Management**: List and manage transaction categories
- **Goal Tracking**: Access financial goals and progress
- **Net Worth Tracking**: Retrieve net worth snapshots over time

## Installation

1. Clone or download this MCP server
2. Install dependencies:
   ```bash
   cd /path/to/monarch-money-mcp
   uv sync
   ```

## Configuration

### Environment Variables

The server requires the following environment variables for authentication:

| Variable | Required | Description |
|----------|----------|-------------|
| `MONARCH_EMAIL` | Yes | Your Monarch Money account email |
| `MONARCH_PASSWORD` | Yes | Your Monarch Money account password |
| `MONARCH_MFA_SECRET` | Yes* | Your TOTP secret for 2FA (*required if 2FA is enabled) |
| `MONARCH_FORCE_LOGIN` | No | Set to `true` to bypass session cache |

### MCP Client Configuration

Add the server to your `.mcp.json` configuration file:

```json
{
  "mcpServers": {
    "monarch-money": {
      "command": "/path/to/uv",
      "args": [
        "--directory", 
        "/path/to/monarch-money-mcp",
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

**Important Notes:**
- Replace `/path/to/uv` with the full path to your `uv` executable (find it with `which uv`)
- Replace `/path/to/monarch-money-mcp` with the absolute path to this server directory
- Use absolute paths, not relative paths

### Getting Your MFA Secret

1. Go to Monarch Money settings and enable 2FA
2. When shown the QR code, look for the "Can't scan?" or "Enter manually" option
3. Copy the secret key (it will be a string like `T5SPVJIBRNPNNINFSH5W7RFVF2XYADYX`)
4. Use this as your `MONARCH_MFA_SECRET`

## Available Tools

### `get_accounts`
List all accounts with their balances and details.

### `get_transactions`
Get transactions with smart output formatting and flexible filtering:
- **Smart Output**: Returns compact format by default (reduces data size by ~80%)
  - Compact fields: `id`, `date`, `amount`, `merchant`, `plaidName`, `category`, `categoryId`, `account`, `needsReview` (plus `pending` and `notes` when present)
  - Set `verbose=True` for complete transaction details with all metadata
- **Flexible Date Filtering**: Supports natural language dates
  - Examples: `"last month"`, `"yesterday"`, `"30 days ago"`, `"this year"`
  - Also supports standard formats: `"2024-01-01"`, `"01/15/2024"`
- **Additional Filters**:
  - `account_id`: Filter by specific account
  - `category_id`: Filter by specific category
  - `limit`: Maximum transactions to return (default: 100, max: 1000)
  - `offset`: Pagination offset

### `get_transaction_categories`
List all transaction categories. Returns compact `{id, name}` pairs by default. Set `verbose=True` for full details.

### `get_budgets`
Get budget information and spending analysis.

### `get_goals`
List financial goals and their progress.

### `get_cashflow`
Get cashflow data for income and expense analysis.

### `get_investments`
Get investment account details and performance.

### `get_net_worth`
Get net worth snapshots over time.

## Usage Examples

### Basic Account Information
```
Use the get_accounts tool to see all my accounts and their current balances.
```

### Transaction Analysis (Compact Format)
```
Get all transactions from last month - returns compact format with essential fields only.
```

The compact format is perfect for most queries and looks like:
```json
{
  "id": "123456789012345678",
  "date": "2025-08-24",
  "amount": -8.49,
  "merchant": "Corner Deli",
  "plaidName": "CORNER DELI NYC",
  "category": "Restaurants & Bars",
  "categoryId": "cat_001",
  "account": "Main Credit Card",
  "needsReview": true
}
```

### Transaction Analysis (Verbose Format)
```
Get detailed transaction data with verbose=True to see all metadata, nested objects, and timestamps.
```

### Budget Tracking
```
Show me my current budget status using the get_budgets tool.
```

## Session Management

The server automatically manages authentication sessions:
- Sessions are cached in a `.mm` directory for faster subsequent logins
- The session cache is automatically created and managed
- Use `MONARCH_FORCE_LOGIN=true` in the env section to force a fresh login if needed

## Troubleshooting

### MFA Issues
- Ensure your MFA secret is correct and properly formatted
- Try setting `MONARCH_FORCE_LOGIN=true` in your `.mcp.json` env section
- Check that your system time is accurate (required for TOTP)

### Connection Issues
- Verify your email and password are correct in `.mcp.json`
- Check your internet connection
- Try running the server directly to see detailed error messages:
  ```bash
  uv run server.py
  ```

### Session Problems
- Delete the `.mm` directory to clear cached sessions
- Set `MONARCH_FORCE_LOGIN=true` in your `.mcp.json` env section temporarily

## Development

### Local Development Setup

For local development and testing, create a `.env` file in the project root:

```bash
# .env (git-ignored, never commit this file)
MONARCH_EMAIL="your-email@example.com"
MONARCH_PASSWORD="your-password"
MONARCH_MFA_SECRET="YOUR_TOTP_SECRET_KEY"
```

### Running Tests

```bash
# Run unit tests (no credentials needed)
uv run pytest tests/ -v

# Run integration tests (requires .env with valid credentials)
uv run pytest tests/test_integration.py -v

# Quick health check script
uv run scripts/health_check.py
```

### Health Check

The health check verifies API connectivity by testing authentication, accounts, transactions, and budgets:

```bash
# As a standalone script (reads .env automatically)
uv run scripts/health_check.py

# As a pytest test
uv run pytest tests/test_integration.py::TestHealthCheck -v
```

If the health check fails with a 525 SSL error, it typically means the upstream Monarch Money API has changed and dependencies need updating.

## Log Analysis & Eval

Tools for measuring and optimizing token usage across MCP sessions.

### Log Analyzer

Parses the MCP server log and generates a report with tool usage stats, argument patterns, session flows, and optimization recommendations.

```bash
# Full report against real logs
uv run scripts/analyze_logs.py

# JSON output for programmatic use
uv run scripts/analyze_logs.py --json

# Filter by date
uv run scripts/analyze_logs.py --since 2026-02-01

# Custom log path
uv run scripts/analyze_logs.py --log /path/to/mcp-server-monarch-money.log
```

### Session Evaluator

Measure the efficiency of a specific session (a single conversation or prompt).

**Manual mode** — snapshot before, analyze after:
```bash
uv run scripts/eval_session.py snapshot
# ... use Monarch tools in Claude Desktop ...
uv run scripts/eval_session.py analyze
uv run scripts/eval_session.py analyze --json
```

**Automated mode** — run a prompt via `claude` CLI and analyze in one step:
```bash
uv run scripts/eval_session.py run "analyze last month's transactions for miscategorizations"
uv run scripts/eval_session.py run "what's my spending breakdown this month?" --json
```

The `run` mode requires the `claude` CLI to be installed (`npm install -g @anthropic-ai/claude-code`) and uses the MCP server config from `.mcp.json` in the project directory.

### What the analyzer finds

Based on real usage (~126 tool calls, ~889K tokens):
- **Caching candidates**: `get_transaction_categories` called 5x with identical 32KB results
- **Response bloat**: `update_transactions_bulk` echoing full transaction objects (~68K wasted tokens)
- **Oversized responses**: single calls returning 300KB+
- **Repeated sequences**: 18 consecutive `search_transactions` calls (refinement loops)

## Notable Differences from Forked Repository

This implementation has evolved significantly from the original forked repository with substantial architectural improvements and enhanced capabilities:

### Advanced Architecture
- **FastMCP Framework**: Complete migration from basic MCP to modern FastMCP with `@mcp.tool()` decorators for cleaner, more maintainable code
- **Comprehensive Testing**: 151 passing tests across 12 test files covering analytics, validation, error handling, and log analysis
- **Type Safety**: Strict typing throughout with Pydantic models and minimal MyPy warnings (8 remaining, down from 111+)
- **Structured Logging**: Professional logging with `structlog` for debugging and analytics tracking

### Enhanced Financial Intelligence
- **Compact Transaction Format**: Smart output formatting that reduces data size by ~80% while preserving essential fields (verbose mode available for full details)
- **Batch Operations**: Intelligent tools like `get_complete_financial_overview()` that combine 5 APIs in parallel execution
- **Pattern Analysis**: `analyze_spending_patterns()` with multi-month trend analysis and predictive forecasting
- **Smart Date Processing**: Natural language date parsing ("last month", "this year") with flexible fallback mechanisms
- **Usage Analytics**: Real-time performance monitoring with optimization suggestions and session tracking

### Production-Ready Features
- **Error Resilience**: Comprehensive error handling including broken pipe errors, session expiration, and graceful API failure recovery
- **Security Enhancements**: Proper session management with 0700 permissions and MFA support
- **JSON-RPC Compliance**: All logging redirected to stderr to prevent stdout contamination
- **Development Workflow**: Extensive documentation in `CLAUDE.md` with git commit standards and quality gates

### Tool Expansion
- **20+ Tools**: Complete Monarch Money API coverage vs basic implementation
- **Intelligent Filtering**: Advanced transaction filtering with category, account, and date combinations
- **Optimization Tracking**: Built-in analytics that suggest batch operations based on usage patterns

This implementation represents a complete rewrite focused on production readiness, developer experience, and advanced financial analysis capabilities.

## Credits

### MCP Server
- **Author**: Taurus Colvin ([@colvint](https://github.com/colvint))
- **Description**: MCP (Model Context Protocol) server wrapper for Monarch Money

### MonarchMoney Python Library
- **Original Author**: hammem ([@hammem](https://github.com/hammem))
- **Original Repository**: [https://github.com/hammem/monarchmoney](https://github.com/hammem/monarchmoney)
- **Community Fork**: [https://github.com/bradleyseanf/monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity) (currently used due to API endpoint updates)
- **License**: MIT License
- **Description**: The underlying Python library that provides API access to Monarch Money

This MCP server uses the monarchmoneycommunity fork which tracks the latest Monarch Money API changes.

## Security Notes

> **⚠️ Important**: Monarch Money does not provide an OAuth API or official API access. This MCP server uses unofficial API access that requires your actual account credentials (email, password, and MFA secret). Use with appropriate caution.

### Credential Security
- **Your credentials have full account access** - they can view all financial data and make changes
- Keep credentials secure in your `.mcp.json` file (restrict file permissions)
- The MFA secret (TOTP key) provides ongoing access - treat it like a password
- Session files in `.mm` directory contain authentication tokens - keep them secure
- Never commit `.env` or `.mcp.json` files to version control

### Risk Considerations
- This is an **unofficial API** - Monarch Money could change or restrict access at any time
- Credentials are stored in plain text in configuration files
- Consider using a dedicated Monarch Money account with limited permissions if available
- Review the source code if you have security concerns - it's fully open source