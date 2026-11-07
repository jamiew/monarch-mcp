"""Session loading, re-login recovery, and retry boundaries."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import server


async def test_other_errors_propagate_without_relogin(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_accounts = AsyncMock(side_effect=Exception("Network timeout error"))
    relogin = AsyncMock()
    monkeypatch.setattr(server, "mm_client", client)
    monkeypatch.setattr(server, "ensure_authenticated", relogin)

    with pytest.raises(Exception, match="Network timeout"):
        await server.api_call_with_retry("get_accounts")

    assert client.get_accounts.await_count == 1
    relogin.assert_not_awaited()
    assert server.mm_client is client


async def test_initialize_client_loads_session_without_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "auth_state", server.AuthState.NOT_INITIALIZED)
    with (
        patch.dict(os.environ, {"MONARCH_EMAIL": "test@example.com", "MONARCH_PASSWORD": "testpass"}, clear=True),
        patch("server.session_file") as mock_session_file,
        patch("server.MonarchMoney") as mock_mm_class,
    ):
        mock_session_file.exists.return_value = True
        mock_client = MagicMock()
        mock_client.login = AsyncMock()
        mock_mm_class.return_value = mock_client

        await server.initialize_client()

        assert mock_client.load_session.called
        assert not mock_client.login.called
        assert server.auth_state == server.AuthState.AUTHENTICATED


@pytest.mark.parametrize("reset_during", ["forced_login", "session_load", "login_retry"])
async def test_initialization_recovers_client_after_session_reset(reset_during: str, tmp_path: Path) -> None:
    initial_client = MagicMock()
    initial_client.login = AsyncMock(side_effect=ValueError("invalid credentials"))
    initial_client.load_session.side_effect = ValueError("session expired")
    fresh_client = MagicMock()
    fresh_client.login = AsyncMock()
    session_file = tmp_path / "session.pickle"
    if reset_during == "session_load":
        session_file.touch()

    with (
        patch.dict(
            "os.environ",
            {
                "MONARCH_EMAIL": "test@example.com",
                "MONARCH_PASSWORD": "testpass",
                "MONARCH_FORCE_LOGIN": "true" if reset_during == "forced_login" else "false",
            },
            clear=True,
        ),
        patch("server.session_file", session_file),
        patch("server.session_dir", tmp_path),
        patch("server.MonarchMoney", side_effect=[initial_client, fresh_client]),
        patch("server.asyncio.sleep", new_callable=AsyncMock),
    ):
        await server.initialize_client()

    assert server.auth_state == server.AuthState.AUTHENTICATED
    assert server.mm_client is fresh_client
    fresh_client.login.assert_awaited_once()


async def test_login_saves_only_the_private_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = server.MonarchMoney(token="synthetic-session-token")
    monkeypatch.setattr(client, "_login_user", AsyncMock())
    monkeypatch.setattr(server, "MonarchMoney", lambda **_: client)
    monkeypatch.chdir(tmp_path)
    private_dir = tmp_path / "private"
    session_file = private_dir / "session.pickle"
    monkeypatch.setattr(server, "session_dir", private_dir)
    monkeypatch.setattr(server, "session_file", session_file)
    monkeypatch.setenv("MONARCH_EMAIL", "example@example.invalid")
    monkeypatch.setenv("MONARCH_PASSWORD", "synthetic-password")
    monkeypatch.setenv("MONARCH_FORCE_LOGIN", "false")

    await server.initialize_client()

    assert server.auth_state == server.AuthState.AUTHENTICATED
    assert session_file.is_file()
    assert session_file.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".mm").exists()


def test_clear_session_removes_session_files_and_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_file = tmp_path / "session.pickle"
    library_session = tmp_path / "mm_session.pickle"
    session_file.write_bytes(b"synthetic")
    library_session.write_bytes(b"synthetic")
    monkeypatch.setattr(server, "session_file", session_file)
    monkeypatch.setattr(server, "session_dir", tmp_path)

    server.clear_session()

    assert not session_file.exists()
    assert not library_session.exists()
    assert server.mm_client is None
    assert server.auth_state is server.AuthState.NOT_INITIALIZED


async def test_concurrent_ensure_authenticated_calls_share_one_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "auth_state", server.AuthState.NOT_INITIALIZED)
    monkeypatch.setattr(server, "mm_client", None)
    monkeypatch.setattr(server, "auth_lock", None)
    init_count = 0

    async def slow_initialize_client() -> None:
        nonlocal init_count
        init_count += 1
        await asyncio.sleep(0.05)
        server.mm_client = MagicMock()
        server.auth_state = server.AuthState.AUTHENTICATED

    monkeypatch.setattr(server, "initialize_client", slow_initialize_client)

    await asyncio.gather(*(server.ensure_authenticated() for _ in range(10)))

    assert init_count == 1
    assert server.auth_state == server.AuthState.AUTHENTICATED
