"""Credentials, date serialization, and console entry-point signals."""

import os
import select
import signal
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import server


def test_convert_dates_to_strings_recurses_and_keeps_other_values() -> None:
    data = {
        "date": date(2024, 1, 15),
        "datetime": datetime(2024, 1, 15, 10, 30, 45),
        "nested": {"items": ["text", date(2024, 1, 15), 42, 3.14, False, None]},
    }

    assert server.convert_dates_to_strings(data) == {
        "date": "2024-01-15",
        "datetime": "2024-01-15T10:30:45",
        "nested": {"items": ["text", "2024-01-15", 42, 3.14, False, None]},
    }


@patch.dict("os.environ", {}, clear=True)
async def test_initialize_client_names_missing_credentials() -> None:
    with pytest.raises(ValueError, match="MONARCH_EMAIL"):
        await server.initialize_client()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal semantics")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_console_entrypoint_terminates_on_signal(signum: int, tmp_path: Path) -> None:
    code = (
        "import asyncio\n"
        "import server\n"
        "async def wait_for_signal():\n"
        "    print('ready', flush=True)\n"
        "    await asyncio.Event().wait()\n"
        "server.main = wait_for_signal\n"
        "server.run()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(server.__file__).parent,
        env={"MONARCH_SESSION_DIR": str(tmp_path)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 15)[0], "Child did not become ready"
        assert process.stdout.readline().strip() == "ready"
        process.send_signal(signum)
        assert process.wait(timeout=5) in (0, -signum)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
