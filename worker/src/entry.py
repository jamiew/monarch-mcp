"""Cloudflare Python Worker entry point for the Monarch MCP server.

Workers have no persistent disk, and secrets arrive on `self.env`, not `os.environ`.
This file copies secrets into the environment that server.py reads, and keeps the
Monarch session token in KV so a new isolate does not log in again.
"""

import json
import os
import pickle

# server.py creates its session directory at import time. The in-memory filesystem
# is per isolate, so KV is the durable copy.
os.environ.setdefault("MONARCH_SESSION_DIR", "/tmp/monarch-mcp")

from workers import Response, WorkerEntrypoint, asgi

import server

SECRETS = ("MONARCH_EMAIL", "MONARCH_PASSWORD", "MONARCH_MFA_SECRET", server.HTTP_AUTH_TOKEN_ENV)
SESSION_KEY = "monarch-session"

_apps: dict[str, object] = {}
# Token most recently read from or written to KV in this isolate; None means unknown.
_kv_token: str | None = None
_kv_loaded = False


def _load_secrets(env: object) -> str:
    for name in SECRETS:
        value = getattr(env, name, None)
        if isinstance(value, str) and value:
            os.environ[name] = value
    return server.http_auth_token()


def _file_token() -> str | None:
    if not server.session_file.exists():
        return None
    try:
        with server.session_file.open("rb") as fh:
            data = pickle.load(fh)  # Written by the Monarch client in this isolate.
    except (OSError, pickle.UnpicklingError, EOFError):
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


async def _restore_session(kv: object) -> None:
    global _kv_token, _kv_loaded
    if _kv_loaded:
        return
    raw = await kv.get(SESSION_KEY)
    _kv_loaded = True
    if not isinstance(raw, str):
        return
    try:
        token = json.loads(raw).get("token")
    except (ValueError, AttributeError):
        return
    if not isinstance(token, str) or not token:
        return
    _kv_token = token
    if not server.session_file.exists():
        with server.session_file.open("wb") as fh:
            pickle.dump({"token": token, "auth_mode": "token"}, fh)
        server.session_file.chmod(0o600)


async def _persist_session(kv: object) -> None:
    """Mirror the session file to KV after a login, or remove it after a reset."""
    global _kv_token
    token = _file_token()
    if token == _kv_token:
        return
    if token is None:
        await kv.delete(SESSION_KEY)
    else:
        await kv.put(SESSION_KEY, json.dumps({"token": token}))
    _kv_token = token


class Default(WorkerEntrypoint):
    async def fetch(self, request: object) -> object:
        try:
            auth_token = _load_secrets(self.env)
        except ValueError:
            return Response(f"{server.HTTP_AUTH_TOKEN_ENV} secret is missing or too short", status=500)
        app = _apps.get(auth_token)
        if app is None:
            _apps.clear()
            app = _apps[auth_token] = server.create_http_app(auth_token)

        kv = getattr(self.env, "MONARCH_SESSIONS", None)
        if kv is None:
            return await asgi.fetch(app, request, self.env, self.ctx)

        await _restore_session(kv)
        try:
            return await asgi.fetch(app, request, self.env, self.ctx)
        finally:
            await _persist_session(kv)
