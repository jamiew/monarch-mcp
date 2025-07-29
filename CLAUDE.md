# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Basic Operations
- `uv sync` - Install dependencies and create/update virtual environment
- `uv run python server.py` - Run the MCP server directly for testing
- `uv run server.py` - Alternative way to run via project script entry point
- `uv add <package>` - Add new dependencies to the project
- `uv remove <package>` - Remove dependencies from the project

### Testing the Server
- Run `uv run python server.py` to test the server directly and see detailed error messages
- Use `MONARCH_FORCE_LOGIN=true uv run python server.py` to force a fresh login if sessions are problematic

## Code Standards & Type Safety

### Strict Type Safety Requirements
- **ABSOLUTELY NO `Any` types** - All variables, parameters, and return values must have explicit, specific types
- **NO `as` type assertions** - Use runtime validation instead of type assertions
- **Runtime Type Validation** - Use validation libraries (Zod equivalent for Python like Pydantic) to parse unknown data into validated types
- **Explicit Type Annotations** - Every function parameter and return value must have type annotations

### Validation Patterns
- Parse external API responses with runtime validation before using the data
- Validate all user inputs through schema validation
- Use `Union` types with proper type guards for handling multiple valid types
- Prefer `Optional[T]` over `T | None` for clarity in older Python versions

## Architecture Overview

This is a **Model Context Protocol (MCP) server** implementing JSON-RPC 2.0 protocol that provides AI assistants with access to Monarch Money financial data. The codebase is a single-file Python application (`server.py`) that acts as a bridge between MCP clients and the Monarch Money API.

### MCP Protocol Compliance

**Protocol Foundation**
- Implements JSON-RPC 2.0 as underlying RPC protocol
- Uses stdio transport for local process communication
- Supports capability negotiation handshake with explicit feature declaration
- Follows MCP lifecycle: initialization → capability exchange → tool execution

**Core MCP Primitives Exposed**
- **Tools**: Executable functions for financial operations (accounts, transactions, budgets)
- Each tool implements required methods: `list_tools()` and `call_tool()`
- Tools are dynamically discoverable with comprehensive metadata

### Core Components

**MCP Server Framework** (`server.py:39`)
- Uses the `mcp.server` framework to handle MCP protocol communication
- Runs over stdio for communication with MCP clients
- Implements proper JSON-RPC 2.0 message structure
- Handles capability negotiation and protocol version validation

**Authentication & Session Management** (`server.py:46-78`)
- Manages login with email, password, and optional MFA through Monarch Money API
- Caches authentication sessions in `.mm/mm_session.pickle` (referenced via `session_file` variable)
- Automatically loads existing sessions and falls back to fresh login if invalid
- Environment variables: `MONARCH_EMAIL`, `MONARCH_PASSWORD`, `MONARCH_MFA_SECRET`, `MONARCH_FORCE_LOGIN`

**Tool Implementation** (`server.py:82-255`)
- Each financial operation is exposed as an MCP tool with strict JSON schema validation
- Implements complete Monarch Money API surface area including read and write operations
- Tools include: accounts, transactions, budgets, cashflow, categories, transaction CRUD, account refresh
- All tool inputs validated against explicit JSON schemas

**Data Serialization** (`server.py:19-36`)
- Custom date/datetime to string conversion (`convert_dates_to_strings`) ensures JSON compatibility
- Applied to all API responses before returning to MCP clients
- Must be replaced with proper type-safe serialization using validation schemas

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

### Dependencies

- **mcp**: MCP protocol implementation for server-side communication (≥1.9.4)
- **monarchmoney**: Python client library for Monarch Money API access (≥0.1.15)
- **pydantic** (recommended): For runtime type validation replacing current `Any` types
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

## Required Bugfixes and Improvements

### CRITICAL (Must Fix Before Production)

#### 1. Type Safety Violations
**Current Issues:**
- Line 7: `from typing import Any, Dict, Optional, List` - Using prohibited `Any` type
- Line 19: `def convert_dates_to_strings(obj: Any) -> Any:` - Function uses `Any` types
- Line 259: `async def call_tool(name: str, arguments: Dict[str, Any])` - Using `Any` in arguments
- Multiple functions lack explicit return type annotations

**Required Fixes:**
- Replace all `Any` types with specific union types or Pydantic models
- Add Pydantic dependency for runtime validation: `uv add pydantic`
- Create typed data models for all Monarch Money API responses
- Implement runtime validation for all external API responses
- Add type guards for Union type handling

#### 2. MCP Protocol Compliance Issues
**Current Issues:**
- Using outdated `mcp.server.Server` instead of recommended `FastMCP`
- Missing proper MCP protocol version negotiation (should be "2025-06-18")
- No structured output validation for tool responses
- Missing proper capability declaration in initialization

**Required Fixes:**
- Migrate to `FastMCP` from `mcp.server.fastmcp`
- Implement proper protocol version negotiation
- Add structured output validation using Pydantic models
- Use decorators `@mcp.tool()`, `@mcp.resource()` for tool definitions
- Update dependency to include CLI features: `uv add "mcp[cli]"`

#### 3. Authentication Security Issues
**Current Issues:**
- Line 43: Session file stored in home directory without proper permissions
- No validation of environment variables format
- Missing MFA exception handling (`RequireMFAException`)
- Global client variable creates potential race conditions

**Required Fixes:**
- Move session storage to `.mm/` directory with proper permissions (0600)
- Add environment variable validation with Pydantic settings
- Implement proper `RequireMFAException` handling
- Replace global client with proper dependency injection pattern

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