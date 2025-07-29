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
- `uv run python server.py` - Test server directly (structured logs to stdout)
- `MONARCH_FORCE_LOGIN=true uv run python server.py` - Force fresh login

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

**Complete Monarch Money API Coverage (14 Tools)**
- **Core**: `get_accounts`, `get_transactions`, `get_budgets`, `get_cashflow`
- **Categories**: `get_transaction_categories`
- **Transactions**: `create_transaction`, `update_transaction`
- **Investments**: `get_account_holdings`, `get_account_history`
- **Banking**: `get_institutions`, `refresh_accounts`
- **Planning**: `get_recurring_transactions`, `set_budget_amount`
- **Manual**: `create_manual_account`

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
- **34 passing tests** with comprehensive coverage
- **MyPy errors reduced**: 13 → 8 (only untyped decorator warnings)
- **Security**: Proper session handling and MFA support
- **Modern stack**: FastMCP, Pydantic, structlog, pytest

### 🚧 CURRENT PRIORITIES

#### Smart Tool Design & UX
- **Batch operations** for efficient multi-account/transaction queries
- **Intelligent filtering** with natural language date parsing
- **Smart aggregations** (monthly summaries, category totals)
- **Contextual suggestions** based on transaction patterns

### HIGH PRIORITY (Significant Impact)

#### 4. Error Handling Improvements
**Current Issues:**
- Line 264: Generic try/catch blocks without specific exception types
- Line 299-313: Hardcoded error message detection for budget API
- Missing logging for debugging and monitoring
- No proper error response formatting for MCP clients

**Required Fixes:**
- Replace generic `Exception` catches with specific exception types
- Add proper logging using `structlog` or similar: `uv add structlog`
- Implement MCP-compliant error response format
- Add retry logic for network failures with exponential backoff

#### 5. Session Management Reliability
**Current Issues:**
- Session validation only on startup, not per-request
- No session refresh mechanism
- Missing session cleanup on authentication failures
- Session file conflicts in multi-process environments

**Required Fixes:**
- Implement per-request session validation
- Add automatic session refresh before expiration
- Add session cleanup and recreation on auth failures
- Use atomic file operations for session management

#### 6. Tool Schema Validation
**Current Issues:**
- JSON schemas are defined inline without validation
- No runtime validation of tool inputs
- Missing comprehensive tool metadata
- Tool responses lack proper structure validation

**Required Fixes:**
- Create Pydantic models for all tool inputs and outputs
- Implement runtime validation for all tool parameters
- Add comprehensive docstrings and metadata for tool discovery
- Validate all responses before returning to MCP client

### MEDIUM PRIORITY (Good to Have)

#### 7. Performance Optimizations
**Issues:**
- Date conversion function processes entire response recursively
- No caching of frequently accessed data (accounts, categories)
- Synchronous session file I/O operations
- No connection pooling for API requests

**Improvements:**
- Implement lazy date conversion only when needed
- Add Redis or in-memory caching: `uv add redis` or use `functools.lru_cache`
- Use async file I/O operations: `uv add aiofiles`
- Configure connection pooling in monarchmoney client

#### 8. Observability and Monitoring
**Missing Features:**
- No structured logging
- No metrics collection
- No health check endpoint
- No performance monitoring

**Additions:**
- Add structured logging with correlation IDs
- Implement OpenTelemetry metrics: `uv add opentelemetry-api`
- Add health check tool for MCP clients
- Monitor API response times and error rates

#### 9. Enhanced Tool Functionality
**Missing Monarch Money API Methods:**
- `get_account_holdings()` - Investment portfolio data
- `get_account_history()` - Historical account balances
- `get_institutions()` - Linked financial institutions
- `get_recurring_transactions()` - Scheduled transactions
- `set_budget_amount()` - Budget modification
- `create_manual_account()` - Manual account creation

**Implementation:**
- Add all missing Monarch Money API methods as MCP tools
- Create proper input/output schemas for each new tool
- Add comprehensive error handling for each API method

### LOW PRIORITY (Nice to Have)

#### 10. Code Organization
**Current Issues:**
- Single file contains all functionality
- No separation of concerns
- Missing configuration management
- No plugin architecture for extending functionality

**Improvements:**
- Split into modules: `auth.py`, `tools.py`, `models.py`, `config.py`
- Implement proper configuration management with Pydantic Settings
- Add plugin system for custom tools
- Create proper project structure with tests directory

#### 11. Development Experience
**Missing Features:**
- No unit tests
- No integration tests
- No development/debugging tools
- No API documentation generation

**Additions:**
- Add pytest test suite: `uv add pytest pytest-asyncio`
- Create integration tests with mock Monarch Money API
- Add debugging tools and development server mode
- Auto-generate API documentation from Pydantic schemas

## Implementation Priority Order

1. **Phase 1 (Critical)**: Type safety migration, MCP protocol compliance, security fixes
2. **Phase 2 (High)**: Error handling, session management, schema validation
3. **Phase 3 (Medium)**: Performance optimization, monitoring, API completeness
4. **Phase 4 (Low)**: Code organization, development experience

## Documentation References

**Keep These Updated Regularly:**
- **MCP Protocol**: https://modelcontextprotocol.io/llms-full.txt
- **MCP Python SDK**: https://github.com/modelcontextprotocol/python-sdk
- **Monarch Money API**: https://github.com/hammem/monarchmoney
- **MCP Server Examples**: https://github.com/modelcontextprotocol/servers
- Current MCP Protocol Version: "2025-06-18"
- Re-read all resources regularly to ensure compliance with any API or protocol changes