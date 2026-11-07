# Changelog

## Unreleased

### Fixes

- `update_transactions_bulk` runs at most five updates at once. Before, a batch sent every request at the same time.
- `update_transactions_bulk` rejects batches over 100 items before logging in, with an error that names the limit.

### MCP

- `update_transactions_bulk` accepts a JSON array as well as the JSON string it took before.

## 0.7.0 (2026-11-07)

### Fixes

- `create_transaction` now fails when Monarch rejects the transaction or returns none. Before, it reported success.
- `analyze_spending_patterns` rejects a `lookback_months` below 1.
- A bulk update item that fails with an unexpected internal error reports "Unexpected error", like a whole tool call does, instead of the internal error text.
- Invalid dates, queries, and bulk payloads fail before the server logs in to Monarch.
- Single-value amounts (`create_transaction`, `update_transaction`, `set_budget_amount`, `create_manual_account` balance, and split legs) reject NaN and infinity, as bulk items already did.
- `scripts/analyze_logs.py` now reads the server's current log lines and Claude Desktop request lines in log files. It skipped both before. It also counts each call once.

### MCP

- `update_transaction_splits` and `set_transaction_tags` are marked destructive, since they replace the whole set. Read-only tools now state that they are not destructive, and `refresh_accounts` is marked idempotent.
- The server sends short instructions to clients: amounts are signed, IDs come from lookup tools, and destructive changes need the user's confirmation.

### Development

- Dropped the unused `mcp[cli]` extra, so installs no longer pull in `typer`, `rich`, or `python-dotenv`.
- `scripts/health_check.py` no longer reads `.env` itself. Run it with `uv run --env-file .env`.
- CI cancels superseded runs, publishing never runs twice at once, and workflows no longer keep the checkout token.
- `server.json` lists `MONARCH_SESSION_DIR` and drops the `tools` and `securityConsiderations` fields, which the registry schema doesn't define.
- The publish workflow pins `mcp-publisher` v1.8.8 and checks its SHA-256.
- `scripts/analyze_logs.py` no longer reads the `[TOOL_CALL]`, `[ANALYTICS]`, and `[RESULT_SIZE]` markers that releases before 0.3.0 wrote. Nothing writes them now.

### Website

- Rebuilt the project website as a shorter product overview, with a new original hero illustration and links to the README for setup, tools, and security details.

## 0.6.0 (2026-11-02)

### New tools

- Added 13 tools for features in `monarchmoneycommunity` 1.7: `get_recurring_streams`, `get_merchants`, `delete_merchant`, `get_transaction_tags`, `create_transaction_tag`, `set_transaction_tags`, `delete_transaction`, `find_duplicate_transactions`, `update_account`, `get_net_worth_history`, `get_credit_score_history`, `get_savings_goals`, and `set_savings_goal_budget_amount`. Reads were checked against a live account on 2026-11-02; writes are tested offline only.
- Added the `accounts://types` resource, which lists valid types and subtypes for `create_manual_account`.
- `get_transactions` and `search_transactions` take a `needs_review` filter and return `total_count` and `next_offset`. Compact rows include tag names when a transaction has tags.
- `set_budget_amount` takes `month` and `apply_to_future`, and fails when Monarch returns no budget item.

### Fixes

- Spending summaries, the overview, and spending analysis no longer count transfers, such as card payments, as income and spending. Results report `transfers_excluded`.
- A month or year, such as `2024-02`, `January 2024` or `last month`, now covers the whole period. Before, `2024-02` meant February 2 when today was the 2nd, and `last month` as an end date meant its first day.
- The spending forecast now averages the last three complete months. It used the oldest ones. Category averages count only expenses.
- `update_transaction` and bulk updates reject `amount=0`, which Monarch's client silently dropped.
- Split updates fail when Monarch returns no transaction.
- Authentication fixes:
  - A cancelled call during login no longer blocks every later call.
  - An expired session now causes one login, even when several calls fail at once. Repeated logins could reuse an MFA code and get rejected.
  - Errors that only mention "401" or "403" in their text, such as an ID or amount, no longer trigger a re-login.
  - A rejected MFA code gets an accurate message and retries after the cooldown instead of failing until restart.
- Monarch requests now time out after 30 seconds instead of 10. Larger reads hit the old limit.
- Create timeouts no longer say "try again", since the transaction may already exist.
- The server no longer keeps every tool call's arguments in memory.

### MCP SDK 2

- Moved to MCP Python SDK 2.3 (`MCPServer`). It serves both the 2025 and 2026-07-28 protocol versions.
- Tool errors now start with `Error executing tool <name>:`. Validation, Monarch, and network error messages still reach the model; other internal errors stay in the server log.
- Resource templates reject a blank account ID.

### Dependencies

- PyPI installs now need `monarchmoneycommunity` 1.7.1 or later, since the new tools use its 1.7 methods. Source installs keep the same pinned commit.
- CI and package metadata now cover Python 3.10 through 3.15.

## 0.5.4 (2026-11-01)

- Source installs now use `monarchmoneycommunity` 1.7.1 (and `gql` 4.4.0). It only adds new client methods, so no tool behavior changes. PyPI installs still accept 1.6.0 or later.

## 0.5.3 (2026-11-01)

Thanks to [@mrtweak77](https://github.com/mrtweak77) for diagnosing the `refresh_accounts` bug ([#25](https://github.com/jamiew/monarch-mcp/issues/25)).

- Fixed `refresh_accounts`, which always failed because it never passed account IDs to Monarch. It now requests a refresh for every account.
- Fixed `create_manual_account`, which always failed. It now requires `account_sub_type` (for example `mortgage` for a `loan`), takes an optional `include_in_net_worth`, sends the starting balance, and fails when Monarch does not return the new account.
- Tests now check every mocked Monarch call against the client library's real method signatures.
- Added `get_transaction_details` to the MCP Registry tool list in `server.json`.

## 0.5.2 (2026-09-28)

Thanks to [@prerak-proof](https://github.com/prerak-proof) for transaction details ([#23](https://github.com/jamiew/monarch-mcp/pull/23)).

- Fixed Ctrl-C sometimes freezing the server on Python 3.10.
- Added `get_transaction_details` to resolve split legs to their parent IDs before reading or replacing splits. Returns split flags, split transaction IDs, category ID, and the resolved transaction ID (which can change when a pending transaction redirects to its posted replacement).
- Transaction split reads now reject missing or malformed transactions instead of reporting them as unsplit.

## 0.5.1 (2026-09-27)

Thanks to [@samyk](https://github.com/samyk) for the rule tools ([#22](https://github.com/jamiew/monarch-mcp/pull/22)).

- Added tools to make, change, and delete transaction rules: `create_transaction_rule`, `update_transaction_rule`, and `delete_transaction_rule`. Create and update can also apply the rule to past transactions.
- Every rule must look for a merchant name, statement text, or amount. Monarch ignores edits to rules that don't.
- When you edit a rule, the server sends Monarch the whole rule with your changes. Monarch deletes any setting that is left out, so this keeps the rest of the rule safe.
- The server checks that a deleted rule is really gone, because Monarch's reply always says it wasn't deleted.
- Added `gql` as a dependency, because the server now uses it directly.

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
