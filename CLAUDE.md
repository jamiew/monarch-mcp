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

### Debugging Startup Issues (Updated July 2025)
- **Session expired**: Delete `.mm/session.pickle` or set `MONARCH_FORCE_LOGIN=true`
- **JSON parse errors**: Fixed - all stdout output suppressed with `contextlib.redirect_stdout()`
- **MCP protocol compliance**: All logging/warnings redirected to stderr, third-party lib output suppressed
- **AsyncIO errors**: Fixed - uses `run_stdio_async()` in async context
- **SSL warnings**: Suppressed from gql.transport.aiohttp to prevent stdout contamination
- **Date serialization errors**: Fixed - `build_date_filter()` returns ISO strings for JSON safety
- **Broken pipe errors**: Fixed - comprehensive graceful shutdown and error recovery implemented
- **Date parsing failures**: Enhanced with multi-format fallbacks and helpful error messages

### Usage Analytics & Optimization Monitoring

**View usage analytics in Claude's MCP log:**
```bash
# Monitor all analytics (tool calls, performance, errors)
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "\[ANALYTICS\]"

# Watch for optimization suggestions
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "\[OPTIMIZATION\]"

# Monitor performance (slow operations > 1 second)
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "\[ANALYTICS\]" | grep -E "time: [1-9][0-9]*\.[0-9]+s"

# View session summaries and top tools
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "session_summary"

# NEW: Debug tool calls with arguments (for optimization)
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "\[TOOL_CALL\]"

# NEW: Monitor result sizes for context usage optimization
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "\[RESULT_SIZE\]"

# NEW: Watch for large results (> 50KB) that may need optimization
tail -f /Users/jamie/Library/Logs/Claude/mcp-server-monarch-money.log | grep "\[RESULT_SIZE\]" | grep -E "[5-9][0-9]\.[0-9]+ KB|[0-9]{3,}\.[0-9]+ KB"
```

**Log Format Examples:**
- `[TOOL_CALL] get_transactions | args: {'limit': 100, 'start_date': 'last month', 'verbose': False}`
- `[ANALYTICS] tool_called: get_transactions | time: 0.234s | status: success`
- `[RESULT_SIZE] get_transactions | chars: 12,543 | size: 12.25 KB | transactions: 42 items`
- `[OPTIMIZATION] Consider using get_complete_financial_overview instead of separate get_accounts + get_transactions calls`
- `[ANALYTICS] session_summary: 15 calls | top_tool: get_transactions`

## Development Workflow & Git Guidelines

### Automated Development Process
**When no specific instructions are provided, follow this workflow:**

1. **Read Current Status**: Always start by reading the latest TODO items and status in this CLAUDE.md file
2. **Select Next Task**: Choose the highest priority pending task from the current status section
3. **Implement & Test**: Work on the task following the quality standards below
4. **Validate Before Commit**: Always run type checks and tests before committing
5. **Commit Each Feature**: Make atomic commits for individual features, fixes, or optimizations
6. **Update Status**: Periodically update CLAUDE.md status section (not every commit)

### Git Commit Standards

**Quality Gates (MANDATORY before any commit):**
```bash
# ALWAYS run these before committing - DO NOT commit if either fails
uv run mypy server.py     # Type checking must pass
uv run pytest tests/ -v   # All tests must pass
```

**Commit Message Format:**
```
<type>: <concise description>

<optional body explaining why/what changed>

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

**Commit Types:**
- **feat**: New feature implementation
- **fix**: Bug fix or error resolution  
- **perf**: Performance optimization
- **refactor**: Code restructuring without behavior change
- **test**: Test additions or improvements
- **docs**: Documentation updates (including CLAUDE.md)
- **chore**: Maintenance tasks, dependency updates

**Examples:**
```bash
feat: add intelligent caching for frequently accessed accounts data

fix: resolve date serialization errors in get_transactions tool
- Convert build_date_filter() to return ISO strings instead of date objects
- Update tests to expect string dates for JSON serialization safety

perf: implement connection pooling for Monarch Money API requests

refactor: split server.py into modular components (auth, tools, models)
```

### CLAUDE.md Maintenance Schedule

**Update CLAUDE.md in these situations:**
- ✅ **Major milestones completed** (Phase completion, significant features)
- ✅ **Architecture changes** (New dependencies, structural changes)
- ✅ **Status changes** (Moving between development phases)
- ✅ **New TODO items discovered** during implementation
- ❌ **NOT every commit** - only for significant progress or new findings

**What to update:**
- Move completed tasks from "REMAINING" to "COMPLETED" sections
- Add newly discovered tasks to appropriate priority sections  
- Update "Current Status" metrics (test counts, error counts, etc.)
- Note any breaking changes or migration requirements

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

**Complete Monarch Money API Coverage (22 Tools)**
- **Core**: `get_accounts`, `get_transactions`, `get_budgets`, `get_cashflow`
- **Categories**: `get_transaction_categories`
- **Transactions**: `create_transaction`, `update_transaction`, `update_transactions_bulk`, `search_transactions`
- **Investments**: `get_account_holdings`, `get_account_history`
- **Banking**: `get_institutions`, `refresh_accounts`
- **Planning**: `get_recurring_transactions`, `set_budget_amount`
- **Manual**: `create_manual_account`
- **Batch Operations**: `get_transactions_batch`, `get_spending_summary`, `update_transactions_bulk`
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

### Dependencies (Latest Versions - Updated July 2025)

- **mcp[cli]**: Latest MCP protocol with FastMCP support (≥1.12.2)  
- **monarchmoney**: Python client for Monarch Money API (≥0.1.15)
- **pydantic**: Runtime type validation and data models (≥2.11.7)
- **python-dateutil**: Enhanced date parsing support (≥2.9.0.post0)
- **structlog**: Structured logging for debugging (≥25.4.0)
- **types-python-dateutil**: Type stubs for proper dateutil typing (≥2.9.0.20250708)
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

#### Quality Metrics (Updated October 2025)
- **70 passing tests** with comprehensive coverage including analytics, search, and bulk operations
- **MyPy errors reduced**: 111 → 84 (mainly untyped external decorators, ongoing improvement)
- **Security**: Proper session handling and MFA support
- **Modern stack**: FastMCP 1.12.2, Pydantic, structlog, pytest
- **Usage analytics**: Real-time performance tracking and optimization suggestions
- **Codebase**: 1,447 lines in server.py, 7 test files with comprehensive coverage

### ✅ ADVANCED FEATURES (Recently Completed)

#### Smart Tool Design & UX
- **✅ Batch operations**: `get_transactions_batch()` for efficient multi-queries with parallel execution
- **✅ Bulk updates** (NEW October 2025): `update_transactions_bulk()` for updating multiple transactions in one call
  - Parallel execution for maximum performance
  - Individual error handling per transaction
  - Summary statistics (succeeded/failed counts)
  - Significantly reduces round-trips for batch updates
- **✅ Enhanced Date Parsing** (Updated July 2025): Comprehensive natural language support
  - Natural language: "last month", "yesterday", "this year", "last week", "this week"
  - Relative dates: "30 days ago", "6 months ago", "1 year ago"
  - Multiple formats: ISO, US, European, named months with comprehensive fallbacks
  - Range validation: Prevents invalid date ranges and provides helpful error messages
- **✅ Smart aggregations**: `get_spending_summary()` with category/account/month grouping
- **✅ AsyncIO Runtime Fix**: Server now uses `mcp.run_stdio()` for proper MCP protocol compliance

#### Usage Analytics & Optimization (NEW)
- **✅ Usage tracking**: `@track_usage` decorator on all 20 tools for comprehensive analytics
- **✅ Performance monitoring**: Execution time, error rates, and pattern detection
- **✅ Intelligent batching**: Real-time analysis of usage patterns to suggest optimizations
- **✅ Analytics logging**: Special markers in Claude's MCP log for easy filtering and optimization insights
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

#### Production Stability & Reliability Fixes (NEW)
- **✅ JSON-RPC Protocol Compliance**: All logging redirected to stderr to prevent stdout contamination
- **✅ Third-party Library Logging**: Configured aiohttp and monarchmoney to use stderr only
- **✅ Session Expiration Handling**: Clear error messages and recovery instructions for expired sessions
- **✅ Startup Error Prevention**: Eliminated all sources of stdout output during initialization
- **✅ Date Serialization Fix** (July 2025): Resolved critical JSON serialization errors in date handling
- **✅ Broken Pipe Error Handling** (July 2025): Comprehensive graceful shutdown and I/O error recovery
- **✅ Enhanced Date Parsing** (July 2025): Robust natural language parsing with multi-format fallbacks
- **✅ Dependency Updates** (July 2025): Updated to latest stable versions with security patches

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
4. **✅ Phase 4a (Critical Resilience)** (July 2025): Date serialization fixes, broken pipe handling, dependency updates - DONE

### 🔄 **REMAINING PHASES**
4. **Phase 4b (Advanced Resilience)**: Retry logic, circuit breakers, connection pooling, caching
5. **Phase 5 (Intelligence)**: ML features, advanced analytics, financial insights
6. **Phase 6 (Ecosystem)**: MCP extensions, developer tools, architectural improvements

**Current Status** (Updated October 2025): Production-ready with 70 passing tests, 22 intelligent tools, comprehensive analytics, robust error handling, and enhanced reliability. Recent features: `update_transactions_bulk()` for parallel batch updates, `search_transactions` tool for efficient context-aware search, detailed tool call debugging with result size tracking. Recent critical fixes completed: authentication retry bug (stale client after re-auth), date serialization, broken pipe handling, dependency updates, and enhanced date parsing. Server is stable and ready for real-world usage with excellent user experience for date filtering, bulk operations, and error recovery.

## Documentation References

**Keep These Updated Regularly:**
- **MCP Protocol**: https://modelcontextprotocol.io/llms-full.txt
- **MCP Python SDK**: https://github.com/modelcontextprotocol/python-sdk
- **Monarch Money API**: https://github.com/hammem/monarchmoney
- **MCP Server Examples**: https://github.com/modelcontextprotocol/servers
- Current MCP Protocol Version: "2025-06-18"
- Re-read all resources regularly to ensure compliance with any API or protocol changes