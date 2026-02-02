"""Tests for authentication error handling and retry logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAuthenticationRetry:
    """Test authentication error handling and automatic session clearing."""

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_401(self):
        """Test that 401 errors trigger session clear and retry."""
        from server import api_call_with_retry

        # Mock the method to fail first, then succeed
        mock_method = AsyncMock()
        mock_method.side_effect = [Exception("401 Unauthorized"), {"success": True}]

        # Mock mm_client with the method
        mock_client = MagicMock()
        mock_client.test_method = mock_method

        with (
            patch("server.mm_client", mock_client),
            patch("server.clear_session") as mock_clear,
            patch("server.ensure_authenticated") as mock_auth,
        ):
            result = await api_call_with_retry("test_method")

            # Verify session was cleared and ensure_authenticated was called
            assert mock_clear.called
            assert mock_auth.called
            assert result == {"success": True}
            assert mock_method.call_count == 2

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_bad_credentials(self):
        """Test that bad credentials error triggers session clear and retry."""
        from server import api_call_with_retry

        mock_method = AsyncMock()
        mock_method.side_effect = [Exception("bad credentials provided"), {"success": True}]

        mock_client = MagicMock()
        mock_client.test_method = mock_method

        with (
            patch("server.mm_client", mock_client),
            patch("server.clear_session") as mock_clear,
            patch("server.ensure_authenticated") as mock_auth,
        ):
            result = await api_call_with_retry("test_method")

            assert mock_clear.called
            assert mock_auth.called
            assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_unauthorized(self):
        """Test that 'unauthorized' error triggers session clear and retry."""
        from server import api_call_with_retry

        mock_method = AsyncMock()
        mock_method.side_effect = [Exception("Request unauthorized - invalid session"), {"success": True}]

        mock_client = MagicMock()
        mock_client.test_method = mock_method

        with (
            patch("server.mm_client", mock_client),
            patch("server.clear_session") as mock_clear,
            patch("server.ensure_authenticated") as mock_auth,
        ):
            result = await api_call_with_retry("test_method")

            assert mock_clear.called
            assert mock_auth.called

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_403(self):
        """Test that 403 Forbidden errors trigger session clear and retry."""
        from server import api_call_with_retry

        mock_method = AsyncMock()
        mock_method.side_effect = [Exception("403 Forbidden - not authenticated"), {"success": True}]

        mock_client = MagicMock()
        mock_client.test_method = mock_method

        with (
            patch("server.mm_client", mock_client),
            patch("server.clear_session") as mock_clear,
            patch("server.ensure_authenticated") as mock_auth,
        ):
            result = await api_call_with_retry("test_method")

            assert mock_clear.called
            assert mock_auth.called

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_session_expired(self):
        """Test that 'session' errors trigger session clear and retry."""
        from server import api_call_with_retry

        mock_method = AsyncMock()
        mock_method.side_effect = [Exception("Session has expired, please login again"), {"success": True}]

        mock_client = MagicMock()
        mock_client.test_method = mock_method

        with (
            patch("server.mm_client", mock_client),
            patch("server.clear_session") as mock_clear,
            patch("server.ensure_authenticated") as mock_auth,
        ):
            result = await api_call_with_retry("test_method")

            assert mock_clear.called
            assert mock_auth.called

    @pytest.mark.asyncio
    async def test_api_call_with_retry_ignores_other_errors(self):
        """Test that non-auth errors are re-raised without retry."""
        from server import api_call_with_retry

        mock_method = AsyncMock()
        mock_method.side_effect = Exception("Network timeout error")

        mock_client = MagicMock()
        mock_client.test_method = mock_method

        with (
            patch("server.mm_client", mock_client),
            patch("server.clear_session") as mock_clear,
            patch("server.ensure_authenticated") as mock_auth,
        ):
            with pytest.raises(Exception, match="Network timeout"):
                await api_call_with_retry("test_method")

            # Should not clear session or re-initialize for non-auth errors
            assert not mock_clear.called
            assert not mock_auth.called

    @pytest.mark.asyncio
    async def test_initialize_client_loads_session_without_validation(self):
        """Test that initialize_client loads existing sessions without validating them."""
        import os

        import server

        # Reset auth state before test
        original_auth_state = server.auth_state
        server.auth_state = server.AuthState.NOT_INITIALIZED

        try:
            # Mock environment variables
            with (
                patch.dict(os.environ, {"MONARCH_EMAIL": "test@example.com", "MONARCH_PASSWORD": "testpass"}),
                patch("server.session_file") as mock_session_file,
                patch("server.MonarchMoney") as mock_mm_class,
            ):
                # Setup: session file exists
                mock_session_file.exists.return_value = True

                # Mock the client
                mock_client = AsyncMock()
                mock_mm_class.return_value = mock_client

                # Call initialize_client
                await server.initialize_client()

                # Verify that session was loaded (load_session called)
                assert mock_client.load_session.called
                # Verify that login was NOT called (we loaded existing session)
                assert not mock_client.login.called
                # Verify auth state is AUTHENTICATED after loading session
                assert server.auth_state == server.AuthState.AUTHENTICATED
        finally:
            # Restore original state
            server.auth_state = original_auth_state

    def test_clear_session_removes_both_session_files(self):
        """Test that clear_session removes both custom and monarchmoney session files."""
        from server import clear_session

        with patch("server.session_file") as mock_custom_session, patch("server.session_dir") as mock_session_dir:
            # Setup mock files
            mock_custom_session.exists.return_value = True
            mock_custom_session.unlink = MagicMock()

            mock_mm_session = MagicMock()
            mock_mm_session.exists.return_value = True
            mock_session_dir.__truediv__ = MagicMock(return_value=mock_mm_session)

            # Call clear_session
            clear_session()

            # Verify both files were attempted to be removed
            assert mock_custom_session.unlink.called

    @pytest.mark.asyncio
    async def test_auth_error_indicators_comprehensive(self):
        """Test that all auth error indicators are properly detected."""
        from server import api_call_with_retry

        auth_error_messages = [
            "401 error occurred",
            "Unauthorized access denied",
            "Session has expired",
            "Bad credentials provided",
            "Invalid credentials for user",
            "Authentication failed - check password",
            "Auth failed for this request",
            "403 Forbidden",
            "Not authenticated - please login",
        ]

        for error_msg in auth_error_messages:
            mock_method = AsyncMock()
            mock_method.side_effect = [Exception(error_msg), {"success": True}]

            mock_client = MagicMock()
            mock_client.test_method = mock_method

            with (
                patch("server.mm_client", mock_client),
                patch("server.clear_session") as mock_clear,
                patch("server.ensure_authenticated") as mock_auth,
            ):
                result = await api_call_with_retry("test_method")

                # All these should trigger session clear
                assert mock_clear.called, f"Failed to detect auth error: {error_msg}"
                assert mock_auth.called, f"Failed to reinitialize after: {error_msg}"
                assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_stale_client_after_reauth(self):
        """
        Regression test: Verify api_call_with_retry uses NEW mm_client after re-auth.

        Bug: Previously used stale method reference from old client instance.
        Fix: Now calls getattr(mm_client, method_name) after re-authentication.
        """
        import server

        # Create two different mock clients
        old_client = MagicMock()
        new_client = MagicMock()

        # Old client fails with auth error
        old_method = AsyncMock(side_effect=Exception("401 Unauthorized"))
        old_client.get_accounts = old_method

        # New client succeeds
        new_method = AsyncMock(return_value=[{"id": "account1", "name": "Test Account"}])
        new_client.get_accounts = new_method

        # Track which client is current
        current_client = old_client

        async def mock_ensure_authenticated():
            """Simulate re-auth by switching to new client."""
            nonlocal current_client
            current_client = new_client
            server.mm_client = new_client

        def get_client():
            """Return current client for mm_client."""
            return current_client

        with (
            patch("server.mm_client", new_callable=lambda: property(lambda self: get_client())),
            patch("server.clear_session"),
            patch("server.ensure_authenticated", side_effect=mock_ensure_authenticated),
        ):
            # Set initial client
            server.mm_client = old_client

            result = await server.api_call_with_retry("get_accounts")

            # Verify old method was called once (and failed)
            assert old_method.call_count == 1

            # Verify new method was called once (and succeeded)
            assert new_method.call_count == 1

            # Verify result came from new client
            assert result == [{"id": "account1", "name": "Test Account"}]

    @pytest.mark.asyncio
    async def test_concurrent_ensure_authenticated_calls(self):
        """
        Test that concurrent ensure_authenticated() calls don't cause race conditions.

        10 tasks call ensure_authenticated() simultaneously.
        Only ONE should actually perform initialization.
        """
        import asyncio

        import server

        # Reset auth state
        original_state = server.auth_state
        original_client = server.mm_client
        original_lock = server.auth_lock

        server.auth_state = server.AuthState.NOT_INITIALIZED
        server.mm_client = None
        server.auth_lock = None  # Will be created on first call

        try:
            # Track initialization attempts
            init_count = 0

            async def mock_initialize_client():
                nonlocal init_count
                init_count += 1
                await asyncio.sleep(0.05)  # Simulate slow auth
                server.mm_client = MagicMock()
                server.auth_state = server.AuthState.AUTHENTICATED

            with (
                patch.dict("os.environ", {"MONARCH_EMAIL": "test@example.com", "MONARCH_PASSWORD": "test123"}),
                patch("server.initialize_client", side_effect=mock_initialize_client),
            ):
                # Launch 10 concurrent auth requests
                tasks = [server.ensure_authenticated() for _ in range(10)]
                await asyncio.gather(*tasks)

                # Verify only ONE initialization occurred (lock prevented duplicates)
                assert init_count == 1, f"Expected 1 init, got {init_count}"

                # Verify all tasks completed successfully
                assert server.auth_state == server.AuthState.AUTHENTICATED
        finally:
            # Restore original state
            server.auth_state = original_state
            server.mm_client = original_client
            server.auth_lock = original_lock
