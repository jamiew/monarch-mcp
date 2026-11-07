# Repository guidance

## Financial data and secrets

Never put real financial data in code, tests, docs, or commits. This includes account and merchant names from a user's history, transaction/category/account IDs, amounts, and spending patterns. Use synthetic examples such as `Corner Deli`, `cat_001`, and `txn_123`. A generic brand example is fine; data copied from an account is not.

Do not read or commit credentials, `.env`, saved sessions, or financial logs. The tracked `.mcp.json` loads credentials from `.env` when it exists; keep secrets out of it. Logs can contain tool arguments and API error details. Review and redact them before sharing.

## Development

Use Python 3.10+ and `uv`. Dependency constraints live in `pyproject.toml`; commit `uv.lock` for reproducible source installs.

```bash
uv sync --locked
uv run python server.py
uv run python scripts/ci.py
```

The CI script runs ruff lint/format checks, mypy, and pytest. Individual checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy server.py
uv run pytest tests/ -v --tb=short
```

Use `uv run ruff format .` to format and `uv add` / `uv remove` to change dependencies. CI selects Python 3.10 through 3.15 explicitly and uses locked installs.

`.mcp.json` is the headless dev entry (`claude -p --mcp-config .mcp.json --strict-mcp-config`). It runs this checkout from any directory and starts without `.env`. `tests/test_stdio.py::test_mcp_json_launches_checkout` launches it in both protocol eras.

Live integration tests are opt-in and never load `.env` or saved sessions themselves:

```bash
MONARCH_RUN_INTEGRATION=true uv run --env-file .env pytest tests/test_integration.py -v
```

That command contacts Monarch. Ordinary tests use synthetic data and skip live calls. `scripts/health_check.py` is a separate live diagnostic. Run it with `uv run --env-file .env`; it uses the client's session defaults.

### Workflow

- Follow the user's task before the backlog. Make small changes and reuse existing patterns.
- Validate behavior, types, and formatting before committing.
- Commit only with explicit permission. Keep commits atomic, titles short and plain, and use the configured Git identity with `--no-gpg-sign`.
- Update this file for architecture changes, milestones, or new findings, not every commit. Put release history in `CHANGELOG.md` and user setup in `README.md`.

### Code standards

- Prefer straightforward code and comments that explain constraints, not statements.
- Type function parameters and returns. Do not add `Any` or type assertions; validate external input with Pydantic and narrow unions at runtime. Existing untyped library boundaries are not a reason to spread `Any`.
- Catch specific exceptions rather than adding broad `Exception` handlers. Validate before mutation and preserve useful errors.
- Test observable behavior and failure boundaries. Avoid assertions about wording, implementation details, or hardcoded tool counts.
- The `mock_api` fixture binds every call against the real `MonarchMoney` method signature. Bare `AsyncMock` clients accept anything, which hid broken `refresh_accounts` and `create_manual_account` calls. Add new tools to `TOOL_CALLS` in `tests/test_tool_coverage.py`.

## Architecture and contracts

`server.py` contains the MCP server, response models, authentication, tools, resources, and prompts. It uses MCP Python SDK 2.x `MCPServer` (`mcp>=2.3,<3`), JSON-RPC over stdio, and structlog on stderr. Reserve stdout for the protocol. The SDK serves both 2025-era clients and the 2026-07-28 protocol.

- Tools use `@mcp.tool()` and `@track_usage`, return Pydantic models, and have titles. The SDK emits structured content with a text fallback. The server also sends short `instructions` to clients.
- Every tool sets read-only and destructive hints. Destructive tools delete data, replace a whole set (splits, tags), or can rewrite past transactions (rules). Field edits that change only what the caller names are not destructive.
- SDK 2 shows the model only `ToolError` messages; any other exception becomes `Error executing tool <name>`. `@track_usage` converts known failures (ValueError and Pydantic validation, timeouts, session-file `OSError`, Monarch, gql, aiohttp) to `ToolError`, and `@resource_errors` does the same for resources. Bulk update items follow the same rule. Raise `ValueError` with a useful message for expected failures; don't put secrets in it. Validate input before `ensure_authenticated()` so bad input never triggers a login.
- Monarch responses are GraphQL envelopes, not usually bare lists. Use `extract_list(response, key)` or `extract_transactions_list()` before counting or processing rows.
- Use `JsonValue` for evolving upstream payloads and explicit models for shapes constructed here. Convert dates to ISO strings before serialization.
- Authentication uses one `mm_client` and a lock. It starts lazily on the first data call; loading a session does not validate it immediately. A new client is published only after login succeeds. Requests use a 30-second client timeout. Transaction create, update, and split tools wrap calls in a 60-second limit, so it fires only when a re-login and retry run long.
- `api_call_with_retry()` retries recognized authentication failures with reauthentication. A failed call remembers its client; under the lock it re-logs in only if that client is still current, so several failures from one expiry cause one login. `is_auth_error` uses HTTP status codes from gql/aiohttp errors and narrow text, never a bare "401"/"403" substring. It is not a general network retry or rate-limit policy.
- Four static resources expose accounts, categories, institutions, and account types (`accounts://types`). Two resource templates expose account holdings/history and reject blank IDs. There are four prompts. The prompt `category` argument and the template `account_id` complete from live data. Batch analysis reports progress through `Context`.
- POSIX pipe input uses a cancellable asyncio reader with SDK framing, so SIGINT works without EOF. SDK 2's `stdio_server` still reads stdin on a worker thread and hangs on SIGINT with stdin open (checked on 3.10 and 3.13 with 2.3.0), so keep the workaround. It runs through the private `mcp._lowlevel_server`, since `MCPServer` has no public way to serve custom streams. On POSIX, SIGINT cancels the main task instead of raising `KeyboardInterrupt`. On Python 3.10, an interrupt inside a loop callback could lose a task wakeup and freeze shutdown. Windows and regular-file input keep the SDK transport; Windows signal behavior is not verified.

See README for the tool catalog. Keep these less-obvious contracts intact:

- `get_account_holdings` requires an account ID. `get_all_holdings` fetches brokerage accounts only, then their holdings concurrently; one failure fails the call.
- Transaction splits are full-replace; an empty list removes all splits.
- Bulk updates validate each item independently. Invalid types and unknown fields fail that item; omitted/null fields stay unchanged, while valid false and empty-string updates are preserved.
- `owner_user_id` assigns a household member; "" explicitly sets Shared, while omitted/null leaves ownership unchanged. Ownership updates override inheritance. Single/bulk mutations validate nested errors and require a returned transaction.
- Account-history dates filter snapshots locally before paging; upstream accepts only account_id. Rules and history return `count`, `total_count`, and `next_offset`. Transaction/budget requests need ISO strings, not Python dates.
- Transaction accounts use `displayName`, not `name`; count distinct accounts by ID. Cashflow `summary` is an aggregate list with one nested `summary`; budgets use `budgetData.totalsByMonth`.
- Overview and spending analysis default to compact sections, with `verbose=True` for full payloads. Their 500/2,000-transaction samples report `batch_metadata.transactions_truncated`; a missing upstream count yields null. Spending analysis exposes failed sections in `errors`.
- Spending summary, overview, and analysis exclude transfers by category group type (`expense`, `income`, `transfer`), read from `get_transaction_categories` because transactions don't carry it. They report `transfers_excluded`, null when categories can't be read. Upstream's default visibility already excludes hide-from-reports rows (checked live 2026-11-02); `hidden_from_reports=True` alone returns them.
- Dates: a month, year, or named period covers the whole period. As a start it means the first day, as an end the last day (`parse_date_period`, `parse_flexible_end_date`, `build_date_filter`).
- Recurring reads return scheduled occurrences for a date range. Missing date bounds use the supplied date's month; no dates means the current month. `get_recurring_streams` is the full stream inventory (community `get_recurring_transaction_streams`, live-checked 2026-11-02) and supplies the merchant ID and name for `update_recurring_transaction`.
- `update_recurring_transaction` changes a merchant-wide schedule through upstream `update_reoccuring`. Use the merchant ID and current name, not a stream or transaction ID. Check nested mutation errors before reporting success.
- Rule mutations call Monarch's GraphQL directly (`createTransactionRuleV2`, `updateTransactionRuleV2`, `deleteTransactionRule`), because the community client lacks them. The opt-in round trip in `tests/test_integration.py` (`MONARCH_RUN_RULE_WRITES=true`) passed live on 2026-09-21. It only tested category and review-status actions. Re-sent splits, goals, and owners have not been tested live.
  - Update deletes any action left out of the input. So edits read the rule, apply the changes, and send the whole rule back.
  - Update reportedly ignores rules without a merchant, statement, or amount criterion. Create and update both require one.
  - `setMerchantAction` takes a merchant name, not an ID.
  - An `errors` object whose fields are all null still means Monarch rejected the change.
  - Delete returns `deleted: false` even on success, so the tool confirms by reading the rule list again.
- Recurring `isPast` is not proof of payment. Forecasts and posted transactions must not be double-counted.
- Upstream 1.7 mutations often don't raise on rejection. Tag, account, goal, merchant, and budget tools treat a non-empty `errors` list, an `errors` object with any set field, `success: false`, or a missing returned object as failure. `set_budget_amount` fails on a null `budgetItem`.
- Upstream `update_transaction` drops a falsy amount, so `amount=0` is rejected. Upstream `set_savings_goal_budget_amount` defaults `apply_to_future=True`; the tool sends False unless asked. `delete_merchant` is irreversible; passing `move_to_merchant_id` merges into that merchant.
- `get_merchants`' `total_count` comes from `merchantCount`, which can count a different set than the list. `find_duplicate_transactions` scans at most 10 pages of 500 and reports `scan_truncated`; upstream skips hidden rows and rows without a statement name. `get_aggregate_snapshots` and `get_account_snapshots_by_type` need an explicit ISO start; upstream defaults to 150 years ago.
- Writes added on 2026-11-02 (tags, account update, transaction and merchant deletion, goal budgets, budget month/future) are tested offline only, not against a live account.

### Sessions and troubleshooting

Credentials are `MONARCH_EMAIL`, `MONARCH_PASSWORD`, and optional `MONARCH_MFA_SECRET`. Sessions default to `~/.monarch-mcp/session.pickle`; `MONARCH_SESSION_DIR` overrides the directory. New directories use mode 0700 and saved files are chmodded to 0600. Keep an existing or custom directory private too.

`MONARCH_FORCE_LOGIN=true` bypasses the cache on authentication. `clear_session()` also sets `mm_client=None`, so recreate the client before login after any reset. Pass both `use_saved_session=False` and `save_session=False` to upstream login, then save only to the server's configured path. Otherwise the library writes an additional `.mm/mm_session.pickle`.

For expired sessions, force a fresh login or remove the configured cached session. TOTP requires an accurate system clock. Preserve third-party output suppression and normal SIGTERM/SIGINT handling when changing startup.

`@track_usage` records tool calls, elapsed time, and result sizes. Use `scripts/analyze_logs.py` or `scripts/eval_session.py` for reports. There is no analytics MCP tool. Logs use structured `tool_call`, `tool_success`, and `tool_error` events after a stdlib prefix; the analyzer pairs them into one call and also reads Claude Desktop's request lines.

### Releases

PyPI package: `monarch-mcp-jamiew`. MCP Registry name: `io.github.jamiew/monarch-mcp`.

The console entry point must remain `server:run`, a synchronous wrapper around async `main()`. Pointing it directly at `main()` breaks `uvx` startup.

After approval, bump `pyproject.toml`, commit, tag `vX.Y.Z`, push, and publish a GitHub release. The release workflow checks that the stable tag matches the project version, builds the package, and publishes through OIDC. It fills `server.json` versions from the validated tag. Do not run publishing steps as local validation.

Source changes marked **Unreleased** are not available through PyPI until published. `[tool.uv.sources]` is also source-install-only; exported wheels use the declared PyPI dependency floor.

## Upstream library and forks

**Community, parent, and forks rechecked 2026-11-02.** Keep `monarchmoneycommunity`; no inspected sibling or new client is a safer replacement.

| Repository | Verified revision/activity | Assessment |
|---|---|---|
| [hammem/monarchmoney](https://github.com/hammem/monarchmoney) | `98a6e0d`, 2025-11-03 | No new merged fixes. Domain and cookie-auth PRs remain open. |
| [bradleyseanf/monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity) | `dev` `2f9730ea`; PyPI 1.7.1, 2026-11-01 | 1.7.x adds goals, merchant deletion, category-only rule methods, and read/export getters. `main` has the same tree. Unreleased `refactor/2.0.0` (2026-10-31) splits modules but keeps `monarchmoney.py` signatures; not live-tested upstream. Draft [#100](https://github.com/bradleyseanf/monarchmoneycommunity/pull/100) moves sessions to JSON and stops leaking the token to the attachment host. |
| [keithah/monarchmoney-enhanced](https://github.com/keithah/monarchmoney-enhanced) | `3c7d442`, 2026-08-13; PyPI 0.11.0 | Dependency maintenance, but feature-stale. Old API domain and divergent service/public queries. |
| [tommyzed/monarchmoney-i18n-transactions](https://github.com/tommyzed/monarchmoney-i18n-transactions) | `9b5acb9`, 2026-09-14 | Active currency/import app, not a replacement client. Ordinary recurring getter. |
| [jribnik/monarchmoney-enhanced](https://github.com/jribnik/monarchmoney-enhanced) | PR branches, 2026-07-28 | Proposed rules/transaction schema fixes, not a maintained release. |
| [amaten69/monarchmoney-maten](https://github.com/amaten69/monarchmoney-maten) | `09bae1e`, 2026-08-17 | Transaction changes; no new recurring capability established. |

Discovery covered the parent's 15 most-starred and 15 newest forks, 30 community children, and nine enhanced children (2026-09-14), plus forks of the parent and community pushed after that (2026-11-02). Push dates alone can reflect inherited commits. New PyPI clients `monarch-money-mcp-cli` (macOS-only server) and `monarch-api` (sync, Python 3.11+) are not replacements.

Monarch relaunched an [official MCP connector](https://www.monarch.com/blog/monarch-mcp) on 2026-10-21 (`https://api.monarch.com/mcp`, OAuth, weekly usage limits, no Finicity-connected accounts). It doesn't change the client choice here; the README points users to it.

**Pin policy:** track verified community `dev` HEAD by exact SHA. Current pin: `2f9730ea46150e544dec9684ee180dfec7675ca4`, identical in source to v1.7.1. Update its comment date when changing it. Published floor: `>=1.7.1`, because the 2026-11-02 tools use 1.7 methods. It requires `gql==4.4.0`. Its rule methods only set categories and refuse goals, splits, and review status, so keep the direct rule mutations here. None fixes history date arguments or transaction/budget date serialization.

**Avoid for now:** `upload_attachment` sends the Monarch token to Cloudinary in token-login mode (until #100 lands). `create_transaction_category` computes its default rollover month at import, so always pass one. `delete_transaction_category` never sends its move-to category. `get_all_merchants` timed out at the 10-second client timeout. Enhanced's paid/late recurring summaries and review/disable mutations still need validation before adoption. A larger method list is not evidence of working coverage.

To refresh this assessment:

1. Compare the pinned SHA with community `dev` and PyPI. Inspect changes, not only version strings.
2. Check parent issues/PRs and popular/recent forks for concrete fixes.
3. Verify signatures, response shapes, nested errors, and release availability.
4. Update the pin, lockfile, dependency floor if needed, and this dated assessment.

## Backlog

These are candidates, not promised features. Verify the current implementation before starting.

- **Resilience:** network backoff, rate-limit handling, specific API errors, and clearer MCP errors.
- **Sessions:** atomic persistence, safer session serialization, expiry-aware refresh, and recovery monitoring.
- **Performance:** account/category caching with TTL and invalidation; connection reuse. Consider Redis only if multiple instances need shared state.
- **Observability:** request correlation, health reporting, optional metrics/alerts, and reducing sensitive log content.
- **Analysis:** anomaly detection, categorization, budget variance, investment performance, and goals/savings.
- **Tools:** import/export, fuzzy search, and bill detection.
- **Organization:** split modules or add settings/plugin discovery only when the current layout becomes limiting.
- **Developer workflow:** schema-generated docs, optional reload mode, and focused profiling/debugging tools.
- **MCP:** financial exports, sampling, and multi-server workflows when a concrete client use case needs them. Resources and guided prompts already exist.

## References

- [MCP specification](https://modelcontextprotocol.io/llms-full.txt)
- [Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Community client](https://github.com/bradleyseanf/monarchmoneycommunity)
- [MCP server examples](https://github.com/modelcontextprotocol/servers)
