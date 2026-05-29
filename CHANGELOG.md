# Changelog

## 2026-05-28

### Dependencies refreshed and mypy 2.x adopted

- bumped the lockfile to latest compatible across the board (pydantic 2.13, cryptography 48, starlette 1.2, rich 15, mcp 1.27, monarchmoneycommunity 1.3.2).
- raised the mypy floor to 2.1 and narrowed two `type: ignore` comments it flagged as redundant.

### track_usage decorator preserves tool signatures

- typed the analytics decorator with `ParamSpec`/`TypeVar` instead of `Any`, so every decorated tool keeps its real signature under mypy.

### Test suite order-independence and full success/failure coverage

- added a shared `conftest.py` with an autouse fixture that gives each test a clean authenticated baseline, fixing latent order-dependence (test files now pass in isolation, not just as part of the full run).
- added a coverage matrix so every tool and resource has at least one success and one failure test, including the previously-untested `get_budgets`, `get_cashflow`, `get_transaction_categories`, `get_spending_summary`, and `refresh_accounts`.

## 2026-03-02

### MCP Registry and PyPI publishing

- set up automated publishing to PyPI and the MCP Registry, released as 0.3.0 then 0.3.1.

### Tool annotations, MCP resources and prompts, log analyzer

- added read-only/write annotations to every tool so clients can reason about side effects.
- exposed categories, accounts, and institutions as MCP resources, plus reusable prompt templates for common financial analyses.
- added a log analyzer and token-optimization work to keep tool responses compact.

## 2026-02-02

### CI, linting, and coverage

- added a GitHub Actions workflow running tests, ruff lint/format, and coverage across Python 3.10–3.13.
- resolved all outstanding mypy type errors.

## 2026-02-01

### Switched to the monarchmoneycommunity fork

- moved off the original `monarchmoney` library to the community fork to pick up an API endpoint fix, released as 0.2.0.
- added integration tests with `.env` setup docs and security warnings about the unofficial API and credential handling.

## 2025-10-20

### Enhanced transaction tool definitions

- expanded transaction tool schemas with clearer parameter descriptions and editability notes.
- improved logging clarity and documentation.

## 2025-10-19

### Auto-fill missing date parameters

- `get_transactions` and `search_transactions` now auto-fill a missing start or end date instead of erroring.

## 2025-10-15

### Bulk transaction updates

- added `update_transactions_bulk` for updating many transactions in one parallel call.
- fixed the auth retry path to use a fresh client after re-authentication, and ensured every tool routes through the retry wrapper.

## 2025-10-13

### Lazy authentication

- the server now authenticates only when a tool is invoked rather than at startup, with better handling of startup auth errors.

## 2025-10-10

### Transaction tool reliability fixes

- fixed several transaction-tool bugs and removed redundant tools (including the duplicative `get_transactions_batch`).
- made date parsing consistent across tools.

## 2025-10-08

### Claude GitHub Actions

- added Claude-powered PR assistant, code review, and website-regeneration workflows.

## 2025-10-06

### Compact transaction format

- added a compact transaction output format with a `verbose` option to control response size.
- automatically clear the session on authentication errors so a stale session recovers on the next call.

## 2025-07-30

### Date parsing and broken-pipe resilience

- added comprehensive broken-pipe error handling and graceful shutdown.
- enhanced natural-language date parsing with multi-format fallbacks and clearer error messages.

## 2025-07-29

### FastMCP migration and strict typing

- migrated from the legacy `Server` to the modern FastMCP framework.
- replaced `Any` types with strict Pydantic validation models and added return-type annotations throughout.

### Authentication, logging, and full API coverage

- added secure session handling with MFA support and structured logging via `structlog`.
- completed coverage of the Monarch Money API surface as individual tools.

### Batching, analytics, and stdio stability

- added intelligent batching tools and usage analytics, routed to Claude's MCP log with filterable markers.
- eliminated stdout contamination and AsyncIO runtime errors so the JSON-RPC stdio transport stays clean.
- fixed date serialization errors in `get_transactions`.

## 2025-06-24

### Initial release

- first version of the Monarch Money MCP server.
