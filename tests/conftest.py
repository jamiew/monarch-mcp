"""Shared pytest fixtures for the test suite."""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

import server


@pytest.fixture(autouse=True)
def _baseline_auth_state() -> Iterator[None]:
    """Give every test a clean, authenticated baseline and restore it afterward.

    Tools call ``ensure_authenticated()``, whose fast path returns immediately when
    ``auth_state`` is AUTHENTICATED and ``mm_client`` is set. Without this fixture
    tests passed only because an earlier test happened to leave that global state
    behind — running a single file in isolation would fail. Tests that exercise auth
    init/failure paths override these globals in their own body and still work.
    """
    saved = (server.mm_client, server.auth_state, server.auth_error, server.auth_failed_at)
    server.mm_client = AsyncMock()
    server.auth_state = server.AuthState.AUTHENTICATED
    server.auth_error = None
    server.auth_failed_at = None
    try:
        yield
    finally:
        server.mm_client, server.auth_state, server.auth_error, server.auth_failed_at = saved


@pytest.fixture
def mock_api(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch auth and the API dispatcher so tools run without real auth or network.

    Every tool calls ``ensure_authenticated()`` then routes its API access through
    ``api_call_with_retry(method_name, ...)``. Patching both here makes tests
    independent of global auth state (no reliance on test ordering) and keeps them
    fully offline.

    Returns the AsyncMock standing in for ``api_call_with_retry``. Set
    ``.return_value`` for single-call tools, ``.side_effect = <exc>`` to simulate a
    failing call, or ``.side_effect = dispatch({...})`` for tools that make several
    calls keyed by method name.
    """
    monkeypatch.setattr(server, "ensure_authenticated", AsyncMock())
    api = AsyncMock()
    monkeypatch.setattr(server, "api_call_with_retry", api)
    return api
