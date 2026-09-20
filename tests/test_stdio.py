"""Exercise real stdio framing and shutdown without credentials or API calls."""

import asyncio
import json
import os
import signal
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def stdio_process(tmp_path: Path) -> AsyncIterator[asyncio.subprocess.Process]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MONARCH_")}
    env["MONARCH_SESSION_DIR"] = str(tmp_path / "session")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "server.py"),
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
