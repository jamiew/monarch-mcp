"""Shared pytest fixtures for the test suite."""

import inspect
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from monarchmoney import MonarchMoney

import server


@pytest.fixture(autouse=True)
def _baseline_auth_state() -> Iterator[None]:
    """Isolate tests with an authenticated baseline and restore globals afterward.

    ``ensure_authenticated()`` returns immediately when ``auth_state`` is
    AUTHENTICATED and ``mm_client`` is set. This avoids test-order dependencies.
    Auth tests override these globals to exercise initialization and failures.
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
    """Keep tool calls offline by patching authentication and API dispatch.

    Returns the ``api_call_with_retry`` mock. Set ``return_value`` for one
    response or ``side_effect`` for an exception or method-based dispatch.
    Each call must name a real client method with arguments its signature
    accepts, so a mock cannot hide a call that would fail against Monarch.
    """
    monkeypatch.setattr(server, "ensure_authenticated", AsyncMock())
    api = AsyncMock()

    async def checked_call(method_name: str, *args: Any, **kwargs: Any) -> Any:
        inspect.signature(getattr(MonarchMoney, method_name)).bind(None, *args, **kwargs)
        return await api(method_name, *args, **kwargs)

    monkeypatch.setattr(server, "api_call_with_retry", checked_call)
    return api
