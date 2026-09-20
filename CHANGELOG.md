# Changelog

## 0.5.0 (2026-09-20)

### Tools and analysis

- Added brokerage-wide holdings, transaction-rule inspection, and household-member lookup.
- Added pending/posted filters to transaction reads and search.
- Added owner assignment and Shared ownership to single and bulk updates. Rejected or malformed mutations fail instead of reporting success; successful responses may have null errors.
- Fixed date serialization in spending analysis and transaction updates. Partial analysis failures now expose errors alongside successful sections.
- Fixed account-history date bounds by filtering snapshots locally, with inclusive and open-ended ranges.
- Fixed account grouping to use Monarch's display names and distinct account IDs.
- Paginate rules and account history with counts and next-page offsets; apply history date filters before paging.
- Default overviews and spending analysis to compact summaries, retaining full sections with `verbose=True` and reporting truncated transaction samples.
- Validate cashflow's aggregate-list envelope and preserve budget/cashflow errors.

### Documentation

- Simplified the README and website, with a checked comparison to the original and enhanced Python forks.

### Recurring transactions

- Added date ranges to `get_recurring_transactions`, with natural-language dates and calendar-month defaults.
- Added `update_recurring_transaction` for merchant-wide schedules. Omitted settings stay unchanged; rejected or malformed responses fail instead of reporting success.
- Clarified scheduled occurrences, recorded transactions, and payment status.

### Dependencies

- Updated `monarchmoneycommunity` to verified dev `d30f2859`, identical to the published 1.6.0 source tree; raised the published floor to 1.6.0.
- Refreshed Ruff and compatible HTTP dependencies. Retained maintained MCP 1.30; MCP 2 requires a separate API migration and does not fix the stdin shutdown issue.
- Updated aiohttp and cryptography past the advisories found in the previous environment. The refreshed Python 3.13 environment passed `pip-audit`.

### Hardening

- Recreate the client after session resets and prevent the library's extra working-directory session file.
- Validate bulk updates per item without unsafe coercion or aborting valid siblings.
- Fix Python 3.10 startup: its logging handler cannot be parameterized at runtime.
- Fixed POSIX SIGINT shutdown with stdin still open, retaining SDK framing and normal SIGTERM handling.
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
