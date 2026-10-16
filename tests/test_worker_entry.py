"""Exercise the Cloudflare Worker entry with a stand-in Workers runtime.

The real runtime (Pyodide, KV, the Workers ASGI adapter) is not available in CI, so
this covers secret loading and KV session mirroring, not Pyodide compatibility.
"""

import importlib.util
import json
import pickle
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

import server

TOKEN = "worker-token-" + "x" * 32
ENTRY = Path(__file__).resolve().parents[1] / "worker" / "src" / "entry.py"
HEALTH = httpx.Request("GET", "http://worker.example/healthz")


class FakeKV:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data = dict(initial or {})
        self.writes = 0

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def put(self, key: str, value: str) -> None:
        self.writes += 1
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.writes += 1
        self.data.pop(key, None)


class FakeResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status_code = status


class FakeEntrypoint:
    def __init__(self, env: object) -> None:
        self.env = env
        self.ctx = None


def stub_workers(hooks: list[Callable[[], object]] | None = None) -> types.ModuleType:
    """Return a fake `workers` module; each hook runs inside the next request."""

    async def fetch(app: object, request: httpx.Request, env: object, ctx: object) -> httpx.Response:
        if hooks:
            hooks.pop(0)()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            return await client.send(request)

    module = types.ModuleType("workers")
    module.__dict__.update(
        Response=FakeResponse, WorkerEntrypoint=FakeEntrypoint, asgi=types.SimpleNamespace(fetch=fetch)
    )
    return module


@pytest.fixture(autouse=True)
def session_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # The entry copies secrets into os.environ; monkeypatch removes them afterward.
    for name in ("MONARCH_EMAIL", "MONARCH_PASSWORD", "MONARCH_MFA_SECRET", server.HTTP_AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "session.pickle"
    monkeypatch.setattr(server, "session_file", path)
    yield path


def load_entry(monkeypatch: pytest.MonkeyPatch, workers: types.ModuleType) -> types.ModuleType:
    monkeypatch.setitem(sys.modules, "workers", workers)
    spec = importlib.util.spec_from_file_location("worker_entry", ENTRY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env(kv: FakeKV | None, token: str = TOKEN) -> types.SimpleNamespace:
    values = {server.HTTP_AUTH_TOKEN_ENV: token}
    if kv is not None:
        values["MONARCH_SESSIONS"] = kv
    return types.SimpleNamespace(**values)


def write_session(path: Path, token: str) -> None:
    with path.open("wb") as fh:
        pickle.dump({"token": token, "auth_mode": "token"}, fh)


async def test_restores_kv_session_without_rewriting(monkeypatch: pytest.MonkeyPatch, session_file: Path) -> None:
    entry = load_entry(monkeypatch, stub_workers())
    kv = FakeKV({entry.SESSION_KEY: json.dumps({"token": "tok_saved"})})

    response = await entry.Default(env(kv)).fetch(HEALTH)

    assert response.status_code == 200
    assert entry._file_token() == "tok_saved"
    assert kv.writes == 0


async def test_persists_new_login_and_reset(monkeypatch: pytest.MonkeyPatch, session_file: Path) -> None:
    hooks: list[Callable[[], object]] = [lambda: write_session(session_file, "tok_new"), session_file.unlink]
    entry = load_entry(monkeypatch, stub_workers(hooks))
    kv = FakeKV()

    await entry.Default(env(kv)).fetch(HEALTH)
    assert json.loads(kv.data[entry.SESSION_KEY]) == {"token": "tok_new"}

    await entry.Default(env(kv)).fetch(HEALTH)
    assert entry.SESSION_KEY not in kv.data


async def test_requests_still_need_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = load_entry(monkeypatch, stub_workers())
    request = httpx.Request("POST", "http://worker.example/mcp", json={})

    response = await entry.Default(env(None)).fetch(request)

    assert response.status_code == 401


async def test_missing_auth_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = load_entry(monkeypatch, stub_workers())

    response = await entry.Default(env(FakeKV(), token="")).fetch(HEALTH)

    assert response.status_code == 500
