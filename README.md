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
Get transactions with optional filtering:
- `start_date`: Filter transactions from this date (YYYY-MM-DD)
- `end_date`: Filter transactions to this date (YYYY-MM-DD)
- `account_ids`: List of account IDs to filter by
- `category_ids`: List of category IDs to filter by
- `limit`: Maximum number of transactions to return

### `get_categories`
List all transaction categories.

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

### Transaction Analysis
```
Get all transactions from January 2024 using get_transactions with start_date "2024-01-01" and end_date "2024-01-31".
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

## Notable Differences from Forked Repository

This implementation has evolved significantly from the original forked repository with substantial architectural improvements and enhanced capabilities:

### Advanced Architecture
- **FastMCP Framework**: Complete migration from basic MCP to modern FastMCP with `@mcp.tool()` decorators for cleaner, more maintainable code
- **Comprehensive Testing**: 42 passing tests across 6 test files with 100% coverage including analytics, validation, and error handling
- **Type Safety**: Strict typing throughout with Pydantic models and minimal MyPy warnings (8 remaining, down from 111+)
- **Structured Logging**: Professional logging with `structlog` for debugging and analytics tracking

### Enhanced Financial Intelligence
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
- **Author**: hammem ([@hammem](https://github.com/hammem))
- **Repository**: [https://github.com/hammem/monarchmoney](https://github.com/hammem/monarchmoney)
- **License**: MIT License
- **Description**: The underlying Python library that provides API access to Monarch Money

This MCP server wraps the monarchmoney Python library to provide seamless integration with AI assistants through the Model Context Protocol.

## Security Notes

- Keep your credentials secure in your `.mcp.json` file
- The MFA secret provides full access to your account - treat it like a password
- Session files in `.mm` directory contain authentication tokens - keep them secure
- Consider restricting access to your `.mcp.json` file since it contains sensitive credentials