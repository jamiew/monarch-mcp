"""Tests for authentication error handling and retry logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path


class TestAuthenticationRetry:
    """Test authentication error handling and automatic session clearing."""

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_401(self):
        """Test that 401 errors trigger session clear and retry."""
        from server import api_call_with_retry, clear_session, initialize_client

        # Mock the function to fail first, then succeed
        mock_func = AsyncMock()
        mock_func.side_effect = [
            Exception("401 Unauthorized"),
            {"success": True}
        ]

        with patch('server.clear_session') as mock_clear, \
             patch('server.initialize_client') as mock_init:

            result = await api_call_with_retry(mock_func)

            # Verify session was cleared and client re-initialized
            assert mock_clear.called
            assert mock_init.called
            assert result == {"success": True}
            assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_bad_credentials(self):
        """Test that bad credentials error triggers session clear and retry."""
        from server import api_call_with_retry

        mock_func = AsyncMock()
        mock_func.side_effect = [
            Exception("bad credentials provided"),
            {"success": True}
        ]

        with patch('server.clear_session') as mock_clear, \
             patch('server.initialize_client') as mock_init:

            result = await api_call_with_retry(mock_func)

            assert mock_clear.called
            assert mock_init.called
            assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_unauthorized(self):
        """Test that 'unauthorized' error triggers session clear and retry."""
        from server import api_call_with_retry

        mock_func = AsyncMock()
        mock_func.side_effect = [
            Exception("Request unauthorized - invalid session"),
            {"success": True}
        ]

        with patch('server.clear_session') as mock_clear, \
             patch('server.initialize_client') as mock_init:

            result = await api_call_with_retry(mock_func)

            assert mock_clear.called
            assert mock_init.called

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_403(self):
        """Test that 403 Forbidden errors trigger session clear and retry."""
        from server import api_call_with_retry

        mock_func = AsyncMock()
        mock_func.side_effect = [
            Exception("403 Forbidden - not authenticated"),
            {"success": True}
        ]

        with patch('server.clear_session') as mock_clear, \
             patch('server.initialize_client') as mock_init:

            result = await api_call_with_retry(mock_func)

            assert mock_clear.called
            assert mock_init.called

    @pytest.mark.asyncio
    async def test_api_call_with_retry_handles_session_expired(self):
        """Test that 'session' errors trigger session clear and retry."""
        from server import api_call_with_retry

        mock_func = AsyncMock()
        mock_func.side_effect = [
            Exception("Session has expired, please login again"),
            {"success": True}
        ]

        with patch('server.clear_session') as mock_clear, \
             patch('server.initialize_client') as mock_init:

            result = await api_call_with_retry(mock_func)

            assert mock_clear.called
            assert mock_init.called

    @pytest.mark.asyncio
    async def test_api_call_with_retry_ignores_other_errors(self):
        """Test that non-auth errors are re-raised without retry."""
        from server import api_call_with_retry

        mock_func = AsyncMock()
        mock_func.side_effect = Exception("Network timeout error")

        with patch('server.clear_session') as mock_clear, \
             patch('server.initialize_client') as mock_init:

            with pytest.raises(Exception, match="Network timeout"):
                await api_call_with_retry(mock_func)

            # Should not clear session or re-initialize for non-auth errors
            assert not mock_clear.called
            assert not mock_init.called

    @pytest.mark.asyncio
    async def test_initialize_client_clears_session_on_auth_error(self):
        """Test that initialize_client clears session on authentication errors during session load."""
        from server import initialize_client, mm_client
        import os

        # Mock environment variables
        with patch.dict(os.environ, {
            'MONARCH_EMAIL': 'test@example.com',
            'MONARCH_PASSWORD': 'testpass'
        }), \
        patch('server.session_file') as mock_session_file, \
        patch('server.MonarchMoney') as mock_mm_class, \
        patch('server.clear_session') as mock_clear:

            # Setup: session file exists
            mock_session_file.exists.return_value = True

            # Mock the client
            mock_client = AsyncMock()
            mock_mm_class.return_value = mock_client

            # First call to get_accounts (session validation) fails with 401
            mock_client.get_accounts.side_effect = [
                Exception("401 Unauthorized"),
                [{"id": "acc1"}]  # Success after login
            ]

            # Call initialize_client
            await initialize_client()

            # Verify that clear_session was called due to auth error
            assert mock_clear.called
            # Verify that login was called after clearing
            assert mock_client.login.called

    def test_clear_session_removes_both_session_files(self):
        """Test that clear_session removes both custom and monarchmoney session files."""
        from server import clear_session

        with patch('server.session_file') as mock_custom_session, \
             patch('server.session_dir') as mock_session_dir:

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
            "Not authenticated - please login"
        ]

        for error_msg in auth_error_messages:
            mock_func = AsyncMock()
            mock_func.side_effect = [
                Exception(error_msg),
                {"success": True}
            ]

            with patch('server.clear_session') as mock_clear, \
                 patch('server.initialize_client') as mock_init:

                result = await api_call_with_retry(mock_func)

                # All these should trigger session clear
                assert mock_clear.called, f"Failed to detect auth error: {error_msg}"
                assert mock_init.called, f"Failed to reinitialize after: {error_msg}"
                assert result == {"success": True}
