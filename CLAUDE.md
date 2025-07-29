# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Basic Operations
- `uv sync` - Install dependencies and create/update virtual environment
- `uv run python server.py` - Run the MCP server directly for testing
- `uv add <package>` - Add new dependencies to the project
- `uv remove <package>` - Remove dependencies from the project

### Testing & Validation
- `uv run pytest tests/ -v` - Run all tests with verbose output
- `uv run mypy server.py` - Run type checking
- `uv run python server.py` - Test server directly (all logs to stderr)
- `MONARCH_FORCE_LOGIN=true uv run python server.py` - Force fresh login (if session expires)

### Debugging Startup Issues
- **Session expired**: Delete `.mm/session.pickle` or set `MONARCH_FORCE_LOGIN=true`
- **JSON parse errors**: All stdout contamination fixed - logs go to stderr only
- **MCP protocol issues**: Ensure no print statements or logs go to stdout

## Code Philosophy & Standards

### Human-Centric Design Principles
- **Simplicity over complexity** - Choose the most straightforward solution
- **Clean, self-documenting code** - Well-named functions/variables tell the story
- **Human-readable over clever** - Code should be immediately understandable
- **Minimal comments** - Code itself should explain what and why

### Type Safety (Zero Tolerance)
- **NO `Any` types** - Every value must have explicit, specific types
- **NO `as` assertions** - Use runtime validation with Pydantic instead
- **Explicit annotations** - Every function parameter and return value typed
- **Union types** with proper type guards for multiple valid types

### Error Handling
- **Specific exceptions** - Never catch generic `Exception`
- **Structured logging** - Context-rich logs for debugging
- **Fail fast** - Validate early, fail clearly
- **Graceful degradation** - Handle expected failures elegantly

## Current Architecture (FastMCP + Structured Logging)

**Modern FastMCP Implementation**
- Uses `FastMCP` from `mcp.server.fastmcp` (latest MCP protocol)
- Individual `@mcp.tool()` decorated functions (clean separation)
- JSON-RPC 2.0 over stdio transport
- Automatic capability negotiation and tool discovery

**Secure Authentication & Session Management**
- Sessions stored in `.mm/` directory with 0700 permissions  
- Proper `RequireMFAException` handling
- Structured logging with `structlog` for debugging
- Environment variables: `MONARCH_EMAIL`, `MONARCH_PASSWORD`, `MONARCH_MFA_SECRET`

**Complete Monarch Money API Coverage (20 Tools)**
- **Core**: `get_accounts`, `get_transactions`, `get_budgets`, `get_cashflow`
- **Categories**: `get_transaction_categories`
- **Transactions**: `create_transaction`, `update_transaction`
- **Investments**: `get_account_holdings`, `get_account_history`
- **Banking**: `get_institutions`, `refresh_accounts`
- **Planning**: `get_recurring_transactions`, `set_budget_amount`
- **Manual**: `create_manual_account`
- **Batch Operations**: `get_transactions_batch`, `get_spending_summary`
- **Intelligent Analysis**: `get_complete_financial_overview`, `analyze_spending_patterns`
- **Analytics**: `get_usage_analytics`

**Type-Safe Data Processing**
- Pydantic models for validation (still available for reference)
- `convert_dates_to_strings()` ensures JSON compatibility
- Strict typing throughout (only 8 mypy warnings remain - untyped decorators)

### Monarch Money API Integration

**Available API Methods** (from monarchmoney library):
- **Authentication**: `login()`, `interactive_login()`, `save_session()`, `load_session()`
- **Account Data**: `get_accounts()`, `get_account_holdings()`, `get_account_history()`, `get_institutions()`
- **Transaction Operations**: `get_transactions()`, `create_transaction()`, `update_transaction()`, `delete_transaction()`
- **Budget & Analysis**: `get_budgets()`, `set_budget_amount()`, `get_cashflow()`, `get_recurring_transactions()`
- **Categories**: `get_transaction_categories()`, `create_transaction_category()`
- **Account Management**: `create_manual_account()`, `request_accounts_refresh()`

**Error Handling**
- Handle `RequireMFAException` for multi-factor authentication scenarios
- Implement graceful fallback for invalid sessions and missing budget data
- All API responses must be validated before use

### Key Design Patterns

1. **Single Client Instance**: Global `mm_client` variable maintains one MonarchMoney connection
2. **Session Persistence**: Authentication state cached to avoid repeated logins
3. **Type-Safe Error Handling**: All exceptions properly typed and handled
4. **Runtime Validation**: All external data validated before processing
5. **MCP Protocol Compliance**: Strict adherence to JSON-RPC 2.0 and MCP specifications

### Dependencies (Modern Stack)

- **mcp[cli]**: Latest MCP protocol with FastMCP support (≥1.9.4)  
- **monarchmoney**: Python client for Monarch Money API (≥0.1.15)
- **pydantic**: Runtime type validation and data models (≥2.11.7)
- **structlog**: Structured logging for debugging (≥25.4.0)
- **pytest + mypy**: Testing and type checking (dev dependencies)
- Built with Python 3.10+ using modern async/await patterns

### Configuration

Server runs as MCP server configured in `.mcp.json` with:
- Command: `uv run python server.py` 
- Environment variables for Monarch Money credentials
- Absolute paths required for proper MCP integration
- Implements MCP capability negotiation for feature discovery

### Session Management

- Session files stored in `.mm/` directory (created automatically)
- Session invalidation handled gracefully with automatic re-authentication
- Use `MONARCH_FORCE_LOGIN=true` to bypass session cache for debugging
- Sessions follow Monarch Money API session management patterns

## Status & Achievements

### ✅ COMPLETED (Production Ready)

#### Phase 1 Critical Fixes (All Complete)
- **✅ Type Safety**: Eliminated `Any` types, added Pydantic models, strict typing
- **✅ FastMCP Migration**: Modern MCP protocol with `@mcp.tool()` decorators
- **✅ Authentication Security**: `.mm/` directory, 0600 permissions, `RequireMFAException` handling
- **✅ Structured Logging**: Context-rich logs with `structlog`
- **✅ Complete API Coverage**: All 14 Monarch Money API methods as tools

#### Quality Metrics
- **42 passing tests** with comprehensive coverage including analytics features
- **MyPy errors reduced**: 13 → 8 (only untyped decorator warnings)
- **Security**: Proper session handling and MFA support
- **Modern stack**: FastMCP, Pydantic, structlog, pytest
- **Usage analytics**: Real-time performance tracking and optimization suggestions

### ✅ ADVANCED FEATURES (Recently Completed)

#### Smart Tool Design & UX
- **✅ Batch operations**: `get_transactions_batch()` for efficient multi-queries with parallel execution
- **✅ Intelligent filtering**: Natural language date parsing ("last month", "yesterday", "this year")
- **✅ Smart aggregations**: `get_spending_summary()` with category/account/month grouping
- **✅ AsyncIO Runtime Fix**: Server now uses `mcp.run_stdio()` for proper MCP protocol compliance

#### Usage Analytics & Optimization (NEW)
- **✅ Usage tracking**: `@track_usage` decorator on all 20 tools for comprehensive analytics
- **✅ Performance monitoring**: Execution time, error rates, and pattern detection
- **✅ Intelligent batching**: Real-time analysis of usage patterns to suggest optimizations
- **✅ Analytics logging**: Dual logging (stderr + `logs/usage_analytics.jsonl`) for debugging and insights
- **✅ Session-based tracking**: UUID-based session tracking with in-memory pattern analysis

#### Advanced Financial Analysis Tools (NEW)
- **✅ Complete Overview**: `get_complete_financial_overview(period)` - Single call combining 5 APIs:
  - Parallel execution: accounts, budgets, cashflow, transactions, categories
  - Intelligent summaries: transaction counts, income/expense totals, unique categories/accounts
  - Graceful error handling: Individual API failures don't break entire operation
  - Natural language periods: "this month", "last quarter", "this year"
  
- **✅ Pattern Analysis**: `analyze_spending_patterns(lookback_months, include_forecasting)` - Deep insights:
  - Multi-month trend analysis by category, account, and time period
  - Predictive forecasting based on 3-month rolling averages
  - Smart aggregations with confidence indicators
  - Account usage patterns and category performance metrics
  
- **✅ Optimization Insights**: `get_usage_analytics()` - Real-time optimization:
  - Tool usage frequency analysis and performance metrics
  - Automatic detection of common usage sequences (30-second windows)
  - Intelligent suggestions for batch operations
  - Performance bottleneck identification

#### Production Stability Fixes (NEW)
- **✅ JSON-RPC Protocol Compliance**: All logging redirected to stderr to prevent stdout contamination
- **✅ Third-party Library Logging**: Configured aiohttp and monarchmoney to use stderr only
- **✅ Session Expiration Handling**: Clear error messages and recovery instructions for expired sessions
- **✅ Startup Error Prevention**: Eliminated all sources of stdout output during initialization

### 🔄 REMAINING HIGH PRIORITY TASKS

#### 1. Enhanced Error Handling & Resilience
**Current State**: Basic error handling implemented, but can be improved
**Remaining Work:**
- Add retry logic with exponential backoff for network failures
- Implement circuit breaker pattern for API rate limiting
- Add specific exception types for different Monarch Money API errors
- Create MCP-compliant error response formatting with error codes

#### 2. Advanced Session Management
**Current State**: Basic session persistence with expiration handling
**Remaining Work:**
- Implement per-request session validation (currently only startup)
- Add automatic session refresh before expiration (proactive)
- Implement atomic file operations for session management
- Add session health monitoring and automatic recovery

#### 3. Real-time Data Caching & Performance
**Current State**: No caching implemented
**Remaining Work:**
- Add in-memory caching for frequently accessed data (accounts, categories)
- Implement Redis-based caching for multi-instance deployments: `uv add redis`
- Add cache invalidation strategies and TTL management
- Implement connection pooling for Monarch Money API requests

### 🔄 REMAINING MEDIUM PRIORITY TASKS

#### 4. Advanced Observability & Monitoring
**Current State**: Basic usage analytics and structured logging implemented
**Remaining Work:**
- Add OpenTelemetry metrics integration: `uv add opentelemetry-api`
- Implement health check tool for MCP clients
- Add correlation IDs for request tracing across tools
- Create performance dashboards and alerting

#### 5. Enhanced Financial Intelligence
**Current State**: Basic analysis tools implemented
**Remaining Work:**
- Add ML-based spending predictions and anomaly detection
- Implement category auto-classification for transactions
- Create budget vs. actual variance analysis with alerts
- Add investment performance tracking and portfolio analysis

#### 6. Advanced Tool Features
**Current State**: All 20 core tools implemented
**Remaining Work:**
- Add bulk transaction operations (import/export)
- Implement transaction search with fuzzy matching
- Create automated bill detection and categorization
- Add goal tracking and savings recommendations

### 🔄 REMAINING LOW PRIORITY TASKS

#### 7. Code Architecture & Organization
**Current State**: Single file with 20 tools, comprehensive tests
**Remaining Work:**
- Split into modules: `auth.py`, `tools.py`, `models.py`, `config.py` (optional - current structure works well)
- Implement Pydantic Settings for configuration management
- Add plugin system for custom financial tools
- Create tool auto-discovery and registration system

#### 8. Developer Experience Enhancements
**Current State**: 42 comprehensive tests, type checking, structured logging
**Remaining Work:**
- Add integration tests with live Monarch Money API (optional)
- Create API documentation auto-generation from tool schemas
- Add development server mode with hot reloading
- Implement debugging tools and performance profilers

#### 9. Advanced MCP Features
**Current State**: Full MCP protocol compliance with FastMCP
**Remaining Work:**
- Add MCP resource endpoints for financial data exports
- Implement MCP prompts for guided financial workflows
- Create MCP sampling for transaction data exploration
- Add multi-server coordination for complex financial operations

## Updated Implementation Priority Order

### ✅ **COMPLETED PHASES**
1. **✅ Phase 1 (Critical)**: Type safety migration, MCP protocol compliance, security fixes - DONE
2. **✅ Phase 2 (Advanced Features)**: Smart batching, usage analytics, financial intelligence - DONE  
3. **✅ Phase 3 (Production Stability)**: JSON-RPC fixes, session handling, comprehensive testing - DONE

### 🔄 **REMAINING PHASES**
4. **Phase 4 (Resilience)**: Enhanced error handling, caching, advanced session management
5. **Phase 5 (Intelligence)**: ML features, advanced analytics, financial insights
6. **Phase 6 (Ecosystem)**: MCP extensions, developer tools, architectural improvements

**Current Status**: Production-ready with 42 passing tests, 20 intelligent tools, comprehensive analytics, and robust error handling. Ready for real-world usage while continuing to enhance resilience and intelligence features.

## Documentation References

**Keep These Updated Regularly:**
- **MCP Protocol**: https://modelcontextprotocol.io/llms-full.txt
- **MCP Python SDK**: https://github.com/modelcontextprotocol/python-sdk
- **Monarch Money API**: https://github.com/hammem/monarchmoney
- **MCP Server Examples**: https://github.com/modelcontextprotocol/servers
- Current MCP Protocol Version: "2025-06-18"
- Re-read all resources regularly to ensure compliance with any API or protocol changes