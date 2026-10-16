# Cloudflare Python Worker

Experimental. This runs `server.py` on [Python Workers](https://developers.cloudflare.com/workers/languages/python/) (Pyodide) behind the same bearer token as the local HTTP mode. `src/server.py` is a symlink to the root `server.py`.

Not yet verified on Cloudflare:

- That every dependency resolves for Pyodide and that the bundle fits your plan's size limit. The Free plan's 3 MB limit is probably too small.
- That Monarch login and GraphQL calls work through Pyodide's aiohttp, and that Monarch accepts requests from Cloudflare's network.

Offline tests in `tests/test_worker_entry.py` cover the entry's secret handling and session mirroring with a stand-in runtime.

## How it works

- `src/entry.py` copies Worker secrets into the environment that `server.py` reads, then serves `server.create_http_app()` through the Workers ASGI adapter.
- Workers have no persistent disk. The Monarch session token is mirrored to the `MONARCH_SESSIONS` KV namespace, so a new isolate reuses it instead of logging in again. A reset or failed login removes it from KV.
- Without the KV binding, each new isolate logs in again. Frequent TOTP logins can be throttled.

## Deploy

Requires `uv` 0.12.3 or later and a Cloudflare account. Run these in this directory:

```bash
uv sync
uv run pywrangler login
uv run pywrangler kv namespace create MONARCH_SESSIONS   # paste the id into wrangler.jsonc
uv run pywrangler secret put MONARCH_EMAIL
uv run pywrangler secret put MONARCH_PASSWORD
uv run pywrangler secret put MONARCH_MFA_SECRET
uv run pywrangler secret put MONARCH_MCP_AUTH_TOKEN   # python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
uv run pywrangler deploy
```

Then check it with a read-only tool before you rely on it:

```bash
claude mcp add --transport http monarch https://monarch-mcp.<subdomain>.workers.dev/mcp \
  --header "Authorization: Bearer $MONARCH_MCP_AUTH_TOKEN"
```

To run it locally, put the same names in a git-ignored `.dev.vars` file and run `uv run pywrangler dev`. Local runs contact Monarch.

## Security

- Anyone with the bearer token has full read and write access to your Monarch account. Rotate it with `pywrangler secret put` if it leaks.
- For a second layer, put the `workers.dev` route or a custom domain behind [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) with a service token, and send its `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers from the client.
- Workers Logs are off in `wrangler.jsonc`, because tool logs include financial arguments. Turn them on only while debugging.
- The KV value is the Monarch session token, stored unencrypted in your Cloudflare account.
