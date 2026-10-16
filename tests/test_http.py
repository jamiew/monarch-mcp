"""Exercise the bearer-gated Streamable HTTP transport without credentials or API calls."""

import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

import server

TOKEN = "test-token-" + "x" * 32
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "offline-http-test", "version": "1"},
    },
}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=server.create_http_app(TOKEN))
    async with httpx.AsyncClient(transport=transport, base_url="http://monarch.example") as c:
        yield c


def auth(token: str = TOKEN) -> dict[str, str]:
    return {**MCP_HEADERS, "Authorization": f"Bearer {token}"}


async def test_health_needs_no_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [MCP_HEADERS, auth("wrong-token"), {**MCP_HEADERS, "Authorization": f"Basic {TOKEN}"}],
    ids=["missing", "wrong", "wrong-scheme"],
)
async def test_mcp_rejects_bad_credentials(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.post("/mcp", headers=headers, json=INITIALIZE)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unknown_path_requires_token_first(client: httpx.AsyncClient) -> None:
    assert (await client.get("/other")).status_code == 401
    assert (await client.get("/other", headers=auth())).status_code == 404


async def test_repeated_requests_serve_mcp(client: httpx.AsyncClient) -> None:
    # Any Host header is accepted; the token is the access control.
    initialized = await client.post("/mcp", headers=auth(), json=INITIALIZE)
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "monarch-money"

    for _ in range(2):
        listed = await client.post("/mcp/", headers=auth(), json=LIST_TOOLS)
        assert listed.status_code == 200
        assert listed.json()["result"]["tools"]


async def test_lifespan_completes() -> None:
    app = server.create_http_app(TOKEN)
    incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[str] = []

    async def receive() -> dict[str, str]:
        return incoming.pop(0)

    async def send(message: dict[str, str]) -> None:
        sent.append(message["type"])

    await asyncio.wait_for(app({"type": "lifespan"}, receive, send), timeout=5)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


@pytest.mark.parametrize("token", [None, "short"])
def test_http_requires_strong_token(monkeypatch: pytest.MonkeyPatch, token: str | None) -> None:
    if token is None:
        monkeypatch.delenv(server.HTTP_AUTH_TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(server.HTTP_AUTH_TOKEN_ENV, token)
    with pytest.raises(SystemExit) as exc:
        server.run(["--transport", "http"])
    assert exc.value.code == 2


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_console_server_over_http(tmp_path: Path) -> None:
    port = free_port()
    env = {key: value for key, value in os.environ.items() if not key.startswith("MONARCH_")}
    env.update({"MONARCH_SESSION_DIR": str(tmp_path), server.HTTP_AUTH_TOKEN_ENV: TOKEN})
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(server.__file__).resolve()),
        "--transport",
        "http",
        "--port",
        str(port),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            for _ in range(100):
                try:
                    response = await c.post("/mcp", headers=auth(), content=json.dumps(INITIALIZE))
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("HTTP server did not start")
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "monarch-money"
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)
