# Repository guidance

## Financial data and secrets

Never put real financial data in code, tests, docs, or commits. This includes account and merchant names from a user's history, transaction/category/account IDs, amounts, and spending patterns. Use synthetic examples such as `Corner Deli`, `cat_001`, and `txn_123`. A generic brand example is fine; data copied from an account is not.

Do not read or commit credentials, `.env`, `.mcp.json`, saved sessions, or financial logs. Logs can contain tool arguments and API error details. Review and redact them before sharing.

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

Use `uv run ruff format .` to format and `uv add` / `uv remove` to change dependencies. CI selects Python 3.10 through 3.13 explicitly and uses locked installs.

Live integration tests are opt-in and never load `.env` or saved sessions themselves:

```bash
MONARCH_RUN_INTEGRATION=true uv run --env-file .env pytest tests/test_integration.py -v
```

That command contacts Monarch. Ordinary tests use synthetic data and skip live calls. `scripts/health_check.py` is a separate live diagnostic that loads `.env` and uses the client's session defaults.

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

## Architecture and contracts

`server.py` contains the FastMCP server, response models, authentication, tools, resources, and prompts. It uses `mcp.server.fastmcp.FastMCP`, JSON-RPC over stdio, and structlog on stderr. Reserve stdout for the protocol.

- Tools use `@mcp.tool()` and `@track_usage`, return Pydantic models, and advertise read/write annotations and titles. FastMCP emits structured content with a text fallback.
- Monarch responses are GraphQL envelopes, not usually bare lists. Use `extract_list(response, key)` or `extract_transactions_list()` before counting or processing rows.
- Use `JsonValue` for evolving upstream payloads and explicit models for shapes constructed here. Convert dates to ISO strings before serialization.
- Authentication uses one `mm_client` and a lock. It starts lazily on the first data call; loading a session does not validate it immediately.
- `api_call_with_retry()` retries recognized authentication failures with reauthentication. It is not a general network retry or rate-limit policy.
- Three static resources expose accounts, categories, and institutions. Two resource templates expose account holdings/history. Four prompts support completions; batch analysis reports progress through `Context`.
- POSIX pipe input uses a cancellable asyncio reader with SDK framing so SIGINT works without EOF. Windows and regular-file input retain the SDK transport; Windows signal behavior is not verified.

See README for the tool catalog. Keep these less-obvious contracts intact:

- `get_account_holdings` requires an account ID. `get_all_holdings` fetches brokerage accounts only, then their holdings concurrently; one failure fails the call.
- Transaction splits are full-replace; an empty list removes all splits.
- Bulk updates validate each item independently. Invalid types and unknown fields fail that item; omitted/null fields stay unchanged, while valid false and empty-string updates are preserved.
- `owner_user_id` assigns a household member; "" explicitly sets Shared, while omitted/null leaves ownership unchanged. Ownership updates override inheritance. Single/bulk mutations validate nested errors and require a returned transaction.
- Account-history dates filter snapshots locally before paging; upstream accepts only account_id. Rules and history return `count`, `total_count`, and `next_offset`. Transaction/budget requests need ISO strings, not Python dates.
- Transaction accounts use `displayName`, not `name`; count distinct accounts by ID. Cashflow `summary` is an aggregate list with one nested `summary`; budgets use `budgetData.totalsByMonth`.
- Overview and spending analysis default to compact sections, with `verbose=True` for full payloads. Their 500/2,000-transaction samples report `batch_metadata.transactions_truncated`; a missing upstream count yields null. Spending analysis exposes failed sections in `errors`.
- Recurring reads return scheduled occurrences for a date range, not a complete stream inventory. Missing date bounds use the supplied date's month; no dates means the current month.
- `update_recurring_transaction` changes a merchant-wide schedule through upstream `update_reoccuring`. Use the merchant ID and current name, not a stream or transaction ID. Check nested mutation errors before reporting success.
- Rule mutations use server-side GraphQL (`createTransactionRuleV2`, `updateTransactionRuleV2`, `deleteTransactionRule`) because the community client lacks them. The opt-in round trip in `tests/test_integration.py` (`MONARCH_RUN_RULE_WRITES=true`) passed live on 2026-09-21. Update clears omitted actions and reportedly ignores input without a merchant, statement, or amount criterion, so edits reread the rule, resend it with changes merged, and reject criteria-less results. `setMerchantAction` is written as a merchant name, not an ID. An all-null `errors` object is a rejection. `deleted: false` is returned even on success, so deletion is confirmed by rereading.
- Recurring `isPast` is not proof of payment. Forecasts and posted transactions must not be double-counted.

### Sessions and troubleshooting

Credentials are `MONARCH_EMAIL`, `MONARCH_PASSWORD`, and optional `MONARCH_MFA_SECRET`. Sessions default to `~/.monarch-mcp/session.pickle`; `MONARCH_SESSION_DIR` overrides the directory. New directories use mode 0700 and saved files are chmodded to 0600. Keep an existing or custom directory private too.

`MONARCH_FORCE_LOGIN=true` bypasses the cache on authentication. `clear_session()` also sets `mm_client=None`, so recreate the client before login after any reset. Pass both `use_saved_session=False` and `save_session=False` to upstream login, then save only to the server's configured path. Otherwise the library writes an additional `.mm/mm_session.pickle`.

For expired sessions, force a fresh login or remove the configured cached session. TOTP requires an accurate system clock. Preserve third-party output suppression and normal SIGTERM/SIGINT handling when changing startup.

`@track_usage` records tool calls, elapsed time, and result sizes. Use `scripts/analyze_logs.py` or `scripts/eval_session.py` for reports. There is no analytics MCP tool. Current logs use structured `tool_call`, `tool_success`, and `tool_error` events; the analyzer also accepts older marker formats.

### Releases

PyPI package: `monarch-mcp-jamiew`. MCP Registry name: `io.github.jamiew/monarch-mcp`.

The console entry point must remain `server:run`, a synchronous wrapper around async `main()`. Pointing it directly at `main()` breaks `uvx` startup.

After approval, bump `pyproject.toml`, commit, tag `vX.Y.Z`, push, and publish a GitHub release. The release workflow checks that the stable tag matches the project version, builds the package, and publishes through OIDC. It fills `server.json` versions from the validated tag. Do not run publishing steps as local validation.

Source changes marked **Unreleased** are not available through PyPI until published. `[tool.uv.sources]` is also source-install-only; exported wheels use the declared PyPI dependency floor.

## Upstream library and forks

**Community and parent rechecked 2026-09-20; sibling survey 2026-09-14.** Keep `monarchmoneycommunity`; no inspected sibling is a safer replacement.

| Repository | Verified revision/activity | Assessment |
|---|---|---|
| [hammem/monarchmoney](https://github.com/hammem/monarchmoney) | `98a6e0d`, 2025-11-03 | No new merged fixes. Domain and cookie-auth PRs remain open. |
| [bradleyseanf/monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity) | `dev` `d30f2859`; PyPI 1.6.0, 2026-09-20 | Stable now includes all-holdings, expanded rules, household lookup/ownership updates, and proxy support. |
| [keithah/monarchmoney-enhanced](https://github.com/keithah/monarchmoney-enhanced) | `3c7d442`, 2026-08-13; PyPI 0.11.0 | Dependency maintenance, but feature-stale. Old API domain and divergent service/public queries. |
| [tommyzed/monarchmoney-i18n-transactions](https://github.com/tommyzed/monarchmoney-i18n-transactions) | `9b5acb9`, 2026-09-14 | Active currency/import app, not a replacement client. Ordinary recurring getter. |
| [jribnik/monarchmoney-enhanced](https://github.com/jribnik/monarchmoney-enhanced) | PR branches, 2026-07-28 | Proposed rules/transaction schema fixes, not a maintained release. |
| [amaten69/monarchmoney-maten](https://github.com/amaten69/monarchmoney-maten) | `09bae1e`, 2026-08-17 | Transaction changes; no new recurring capability established. |

Discovery covered the parent's 15 most-starred and 15 newest forks, 30 community children, and nine enhanced children. Push dates alone can reflect inherited commits.

**Pin policy:** track verified community `dev` HEAD by exact SHA. Current pin: `d30f285998a8a64db7f8e923f6742e095645c2b4`, identical in source to v1.6.0. Update its comment date when changing it. Published floor: `>=1.6.0`; tools must work with that release. Since the old pin, production changes add rule fields and bounded duplicate scans; typed budgets are optional. None fixes history date arguments or transaction/budget date serialization.

**Recurring candidates:** [parent PR #165](https://github.com/hammem/monarchmoney/pull/165) and enhanced's service implement `recurringTransactionStreams` with liability forecasts. These queries were not live-validated. Enhanced's paid/late summaries and review/disable mutations also need validation before adoption. A larger method list is not evidence of working coverage.

Other stable candidates include transaction details/tags, duplicate detection, cashflow summaries, and receipt uploads. Add them for a user need, not coverage alone.

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
