"""Exercise real stdio framing and shutdown without credentials or API calls."""

import asyncio
import json
import os
import signal
import sys
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path

import pytest
import pytest_asyncio
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform == "win32", reason=".mcp.json launches through sh")
@pytest.mark.parametrize(("mode", "protocol"), [("legacy", "2025-11-25"), ("auto", "2026-07-28")])
async def test_mcp_json_launches_checkout(tmp_path: Path, mode: str, protocol: str) -> None:
    """Launch every .mcp.json server exactly as configured; never contacts Monarch."""
    servers = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    # Keep UV_PYTHON etc. so uv reuses the venv under test instead of rebuilding it.
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME") or key.startswith("UV_")}
    env["MONARCH_SESSION_DIR"] = str(tmp_path / "session")
    for name, config in servers.items():
        args = [arg.replace("${CLAUDE_PROJECT_DIR:-.}", str(ROOT)) for arg in config["args"]]
        params = StdioServerParameters(command=config["command"], args=args, env=env, cwd=tmp_path)
        async with Client(params, mode=mode, read_timeout_seconds=30) as client:
            assert client.protocol_version == protocol, name
            assert client.server_info is not None
            assert client.server_info.version == version("monarch-mcp-jamiew")
            tools = (await client.list_tools()).tools
            assert tools and (await client.list_prompts()).prompts and (await client.list_resources()).resources
            for tool in tools:
                hints = tool.annotations
                assert hints is not None and hints.read_only_hint is not None, tool.name
                assert hints.read_only_hint or hints.destructive_hint is not None, tool.name
            # Rejected before authentication, so this proves tools/call without a Monarch request.
            result = await client.call_tool("get_transaction_rules", {"limit": 0})
            assert result.is_error
            assert isinstance(result.content[0], TextContent) and "limit" in result.content[0].text


@pytest_asyncio.fixture
async def stdio_process(tmp_path: Path) -> AsyncIterator[asyncio.subprocess.Process]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MONARCH_")}
    env["MONARCH_SESSION_DIR"] = str(tmp_path / "session")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "server.py"),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=512 * 1024,
    )
    assert process.stdin is not None and process.stdout is not None
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "offline-stdio-test", "version": "1"},
        },
    }
    try:
        process.stdin.write((json.dumps(initialize) + "\n").encode())
        await process.stdin.drain()
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), timeout=15))
        assert response["result"]["serverInfo"]["name"] == "monarch-money"
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await process.stdin.drain()
        yield process
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe and signal behavior")
@pytest.mark.parametrize("termination_signal", [signal.SIGINT, signal.SIGTERM])
async def test_signal_exits_with_partial_request_and_open_stdin(
    stdio_process: asyncio.subprocess.Process, termination_signal: signal.Signals
) -> None:
    assert stdio_process.stdin is not None
    stdio_process.stdin.write(b'{"jsonrpc":')
    await stdio_process.stdin.drain()
    stdio_process.send_signal(termination_signal)
    code = await asyncio.wait_for(stdio_process.wait(), timeout=5)
    assert code in (0, -termination_signal)


async def test_large_fragmented_utf8_message_and_eof(stdio_process: asyncio.subprocess.Process) -> None:
    assert stdio_process.stdin is not None and stdio_process.stdout is not None
    description = "Corner Deli caf\u00e9 " * 6000
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "prompts/get",
        "params": {"name": "transaction_categorization_help", "arguments": {"description": description}},
    }
    wire = (json.dumps(request, ensure_ascii=False) + "\n").encode()
    # Split a multibyte code point and exceed StreamReader's normal line limit.
    split = wire.index("\u00e9".encode()) + 1
    stdio_process.stdin.write(wire[:split])
    await stdio_process.stdin.drain()
    await asyncio.sleep(0)
    stdio_process.stdin.write(wire[split:])
    await stdio_process.stdin.drain()
    response = json.loads(await asyncio.wait_for(stdio_process.stdout.readline(), timeout=10))
    assert description in response["result"]["messages"][0]["content"]["text"]
    stdio_process.stdin.close()
    assert await asyncio.wait_for(stdio_process.wait(), timeout=5) == 0
