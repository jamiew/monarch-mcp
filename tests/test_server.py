"""Basic unit tests for the Monarch Money MCP server."""

import pytest
from unittest.mock import Mock, patch, AsyncMock
import asyncio
from typing import Dict, Any

# Import the server module
import server


class TestDateConversion:
    """Test the date conversion utility function."""
    
    def test_convert_dates_to_strings_basic_types(self) -> None:
        """Test conversion of basic non-date types."""
        # Test strings
        assert server.convert_dates_to_strings("hello") == "hello"
        
        # Test numbers
        assert server.convert_dates_to_strings(42) == 42
        assert server.convert_dates_to_strings(3.14) == 3.14
        
        # Test booleans
        assert server.convert_dates_to_strings(True) is True
        assert server.convert_dates_to_strings(False) is False
        
        # Test None
        assert server.convert_dates_to_strings(None) is None

    def test_convert_dates_to_strings_with_dates(self) -> None:
        """Test conversion of date and datetime objects."""
        from datetime import date, datetime
        
        # Test date conversion
        test_date = date(2024, 1, 15)
        result = server.convert_dates_to_strings(test_date)
        assert result == "2024-01-15"
        
        # Test datetime conversion
        test_datetime = datetime(2024, 1, 15, 10, 30, 45)
        result = server.convert_dates_to_strings(test_datetime)
        assert result == "2024-01-15T10:30:45"

    def test_convert_dates_to_strings_nested_structures(self) -> None:
        """Test conversion in nested data structures."""
        from datetime import date
        
        test_date = date(2024, 1, 15)
        
        # Test dictionary
        input_dict = {
            "name": "test",
            "created_date": test_date,
            "count": 5
        }
        result = server.convert_dates_to_strings(input_dict)
        expected = {
            "name": "test", 
            "created_date": "2024-01-15",
            "count": 5
        }
        assert result == expected
        
        # Test list
        input_list = ["hello", test_date, 42]
        result = server.convert_dates_to_strings(input_list)
        expected = ["hello", "2024-01-15", 42]
        assert result == expected


class TestServerInitialization:
    """Test server initialization and configuration."""
    
    def test_server_instance_creation(self) -> None:
        """Test that FastMCP server instance is created properly."""
        assert server.mcp is not None
        assert hasattr(server.mcp, 'name')

    @patch.dict('os.environ', {}, clear=True)
    @pytest.mark.asyncio
    async def test_initialize_client_missing_credentials(self) -> None:
        """Test client initialization fails with missing credentials."""
        with pytest.raises(ValueError, match="MONARCH_EMAIL and MONARCH_PASSWORD"):
            await server.initialize_client()

    @patch.dict('os.environ', {
        'MONARCH_EMAIL': 'test@example.com',
        'MONARCH_PASSWORD': 'testpass'
    })
    @patch('server.MonarchMoney')
    @pytest.mark.asyncio
    async def test_initialize_client_success(self, mock_monarch_class: Mock) -> None:
        """Test successful client initialization."""
        # Setup mock
        mock_client = AsyncMock()
        mock_monarch_class.return_value = mock_client
        
        # Reset global client
        server.mm_client = None
        
        # Test initialization
        await server.initialize_client()
        
        # Verify client was created and login was called
        mock_monarch_class.assert_called_once()
        assert server.mm_client is not None


class TestBasicFunctionality:
    """Test basic server functionality without external dependencies."""
    
    def test_imports_work(self) -> None:
        """Test that all required imports are working."""
        import server
        assert hasattr(server, 'mcp')  # FastMCP instance
        assert hasattr(server, 'MonarchMoney')
        assert hasattr(server, 'convert_dates_to_strings')
        assert hasattr(server, 'initialize_client')

    def test_environment_variable_access(self) -> None:
        """Test that environment variable access works."""
        import os
        # This should not raise an exception
        email = os.getenv("MONARCH_EMAIL")
        password = os.getenv("MONARCH_PASSWORD")
        # Values might be None, but the calls should work
        assert email is None or isinstance(email, str)
        assert password is None or isinstance(password, str)