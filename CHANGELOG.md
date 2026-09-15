# Changelog

## Unreleased

### Documentation

- Simplified the README and website, with a checked comparison to the original and enhanced Python forks.

### Recurring transactions

- Added date ranges to `get_recurring_transactions`, with natural-language dates and calendar-month defaults.
- Added `update_recurring_transaction` for merchant-wide schedules. Omitted settings stay unchanged; rejected or malformed responses fail instead of reporting success.
- Clarified scheduled occurrences, recorded transactions, and payment status.

### Dependencies

- Updated `monarchmoneycommunity` to the September 9 dev commit and raised the published floor to 1.5.2. MCP tools still use released APIs.
- Refreshed compatible dependencies, including MCP 1.30, mypy 2.3, and ruff 0.16.
- Updated aiohttp and cryptography past the advisories found in the previous environment. The refreshed Python 3.13 environment passed `pip-audit`.

### Hardening

- Recreate the client after session resets and prevent the library's extra working-directory session file.
- Validate bulk updates per item without unsafe coercion or aborting valid siblings.
- Fix Python 3.10 startup: its logging handler cannot be parameterized at runtime.
- Restore normal SIGTERM and SIGINT handling.
- Require explicit opt-in for live tests; never load `.env` or saved sessions during test collection.
- Share one login across live integration tests to avoid rejected MFA codes and login throttling.
- Remove duplicate pytest settings, select CI interpreters explicitly, lock installs, and validate release tags before publishing.
- Update GitHub Actions and correct the coverage upload input.

## 2026-06-30

- added `get_transaction_splits` and `update_transaction_splits` in 0.4.0. Splits are full-replace; an empty list clears them. Thanks to [@caseypugh](https://github.com/caseypugh) for [#13](https://github.com/jamiew/monarch-mcp/pull/13), and welcome aboard as a committer!
- fixed session-directory startup in environments with a read-only working directory, also by [@caseypugh](https://github.com/caseypugh) in [#12](https://github.com/jamiew/monarch-mcp/pull/12).
- refreshed dependencies to MCP 1.28, structlog 26, cryptography 49, and starlette 1.3.
- pinned community dev 1.4.0 for cookie-auth fallback and receipt uploads; reviewed the upstream fork landscape.

## 2026-05-28

- fixed `uvx` startup in 0.3.2 by adding synchronous `run()`. Versions 0.3.0 and 0.3.1 incorrectly used async `main()` as the console entry point.
- set `monarchmoneycommunity>=1.3.2` for published installs, which do not use the source-only git pin. Rewrote setup around `uvx`.
- added typed structured output, titles, account holdings/history resource templates, argument completions, and batch progress reporting.
- required `account_id` for holdings and unwrapped GraphQL envelopes so list tools no longer returned empty results for valid responses.
- refreshed dependencies and adopted mypy 2.1. Typed `track_usage` with `ParamSpec` and `TypeVar` to preserve tool signatures.
- isolated test authentication state and added missing success/failure coverage.

## 2026-03-02

- added PyPI and MCP Registry publishing, initially as 0.3.0 and 0.3.1.
- added read/write annotations, account/category/institution resources, guided prompts, and a log analyzer.

## 2026-02-02

- added CI tests, lint, formatting, types, and coverage for Python 3.10 through 3.13.
- resolved mypy errors.

## 2026-02-01

- switched to `monarchmoneycommunity` for the API domain fix, released as 0.2.0.
- added live integration tests and credential/security documentation.

## 2025-10-20

- clarified transaction parameters, editable fields, logging, and documentation.

## 2025-10-19

- auto-fill missing date bounds in `get_transactions` and `search_transactions`.

## 2025-10-15

- added parallel `update_transactions_bulk`.
- fixed API retries to use the new client after reauthentication and routed tools through the retry wrapper.

## 2025-10-13

- moved authentication from startup to the first data call.

## 2025-10-10

- fixed transaction tools, removed redundant tools including `get_transactions_batch`, and aligned date parsing.

## 2025-10-08

- added Claude PR assistance, code review, and website-regeneration workflows.

## 2025-10-06

- added compact transaction output with a `verbose` option.
- clear stale sessions on authentication errors.

## 2025-07-30

- improved broken-pipe handling, shutdown, and date parsing errors.

## 2025-07-29

- migrated to FastMCP and added Pydantic response models and return annotations.
- added MFA/session handling, structlog, API tools, batching, and usage analytics.
- fixed stdout contamination, asyncio startup, and transaction date serialization.

## 2025-06-24

- initial release.
