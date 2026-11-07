"""Tests for the MCP log analyzer."""

import json
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from analyze_logs import (
    ToolCall,
    ToolStats,
    compute_tool_stats,
    detect_sessions,
    find_consecutive_repeats,
    find_sequence_patterns,
    generate_recommendations,
    main,
    parse_analytics_line,
    parse_log_file,
    parse_result_size_line,
    parse_structlog_line,
    parse_tool_call_line,
    parse_wrapper_line,
)

# ---------------------------------------------------------------------------
# Log line parsing tests
# ---------------------------------------------------------------------------


class TestParseWrapperLine:
    """Tests for Claude Desktop wrapper format parsing."""

    def test_basic_tools_call(self) -> None:
        line = (
            "2025-10-19T14:13:44.066Z [monarch-money] [info] "
            'Message from client: {"method":"tools/call","params":{"name":"get_transaction_categories","arguments":{}},"jsonrpc":"2.0","id":4} '
            "{ metadata: undefined }"
        )
        call = parse_wrapper_line(line)
        assert call is not None
        assert call.tool_name == "get_transaction_categories"
        assert call.arguments == {}

    def test_tools_call_with_args(self) -> None:
        line = (
            "2025-10-19T14:15:22.090Z [monarch-money] [info] "
            'Message from client: {"method":"tools/call","params":{"name":"search_transactions","arguments":{"query":"restaurant","limit":1000,"verbose":false}},"jsonrpc":"2.0","id":5} '
            "{ metadata: undefined }"
        )
        call = parse_wrapper_line(line)
        assert call is not None
        assert call.tool_name == "search_transactions"
        assert call.arguments["query"] == "restaurant"
        assert call.arguments["limit"] == 1000
        assert call.arguments["verbose"] is False

    def test_non_tools_call_ignored(self) -> None:
        line = (
            "2025-10-19T14:11:49.479Z [monarch-money] [info] "
            'Message from client: {"method":"initialize","params":{},"jsonrpc":"2.0","id":0} '
            "{ metadata: undefined }"
        )
        assert parse_wrapper_line(line) is None

    def test_non_matching_line(self) -> None:
        assert parse_wrapper_line("some random log line") is None


class TestParseToolCallLine:
    """Tests for [TOOL_CALL] legacy marker parsing."""

    def test_basic_tool_call(self) -> None:
        line = "2025-10-19 10:13:44,068 - __main__ - INFO - [TOOL_CALL] get_transaction_categories | args: {}"
        call = parse_tool_call_line(line)
        assert call is not None
        assert call.tool_name == "get_transaction_categories"
        assert call.arguments == {}

    def test_tool_call_with_args(self) -> None:
        line = (
            "2025-10-19 10:15:22,092 - __main__ - INFO - [TOOL_CALL] search_transactions | args: "
            "{'query': 'restaurant', 'limit': 1000, 'offset': 0, 'start_date': None, 'end_date': None, "
            "'account_id': None, 'category_id': None, 'verbose': False}"
        )
        call = parse_tool_call_line(line)
        assert call is not None
        assert call.tool_name == "search_transactions"
        assert call.arguments["query"] == "restaurant"
        assert call.arguments["limit"] == 1000
        assert call.arguments["verbose"] is False
        assert call.arguments["start_date"] is None

    def test_non_matching_line(self) -> None:
        assert parse_tool_call_line("not a tool call") is None


class TestParseAnalyticsLine:
    """Tests for [ANALYTICS] marker parsing."""

    def test_success(self) -> None:
        line = "2025-10-19 10:13:44,255 - __main__ - INFO - [ANALYTICS] tool_called: get_transaction_categories | time: 0.187s | status: success"
        result = parse_analytics_line(line)
        assert result is not None
        ts_str, tool, time_s, status = result
        assert tool == "get_transaction_categories"
        assert time_s == 0.187
        assert status == "success"

    def test_error(self) -> None:
        line = (
            "2025-10-19 11:24:14,775 - __main__ - ERROR - [ANALYTICS] tool_error: update_transactions_bulk | "
            "time: 0.000s | error: Authentication previously failed"
        )
        result = parse_analytics_line(line)
        assert result is not None
        ts_str, tool, time_s, status = result
        assert tool == "update_transactions_bulk"
        assert status == "error"

    def test_non_matching(self) -> None:
        assert parse_analytics_line("random line") is None


class TestParseResultSizeLine:
    """Tests for [RESULT_SIZE] marker parsing."""

    def test_with_items(self) -> None:
        line = "2025-10-19 10:13:44,255 - __main__ - INFO - [RESULT_SIZE] get_transaction_categories | chars: 33,123 | size: 32.35 KB | categories: 67 items"
        result = parse_result_size_line(line)
        assert result is not None
        ts_str, tool, chars, kb, items = result
        assert tool == "get_transaction_categories"
        assert chars == 33123
        assert kb == 32.35
        assert items == 67

    def test_without_items(self) -> None:
        line = "2025-10-19 10:16:01,467 - __main__ - INFO - [RESULT_SIZE] get_transactions | chars: 57,090 | size: 55.75 KB"
        result = parse_result_size_line(line)
        assert result is not None
        ts_str, tool, chars, kb, items = result
        assert tool == "get_transactions"
        assert chars == 57090
        assert items is None

    def test_non_matching(self) -> None:
        assert parse_result_size_line("not a result size line") is None


class TestParseStructlogLine:
    """Tests for structlog JSON format parsing."""

    def test_tool_success(self) -> None:
        line = json.dumps(
            {
                "event": "tool_success",
                "tool": "get_accounts",
                "timestamp": "2025-10-19T14:00:00",
                "time_s": 0.5,
            }
        )
        call = parse_structlog_line(line)
        assert call is not None
        assert call.tool_name == "get_accounts"
        assert call.execution_time_s == 0.5

    def test_tool_error(self) -> None:
        line = json.dumps(
            {
                "event": "tool_error",
                "tool": "get_budgets",
                "timestamp": "2025-10-19T14:00:00",
            }
        )
        call = parse_structlog_line(line)
        assert call is not None
        assert call.status == "error"

    def test_non_tool_event(self) -> None:
        line = json.dumps({"event": "auth_success", "user": "test"})
        assert parse_structlog_line(line) is None

    def test_non_json(self) -> None:
        assert parse_structlog_line("not json") is None

    def test_ts_key_sets_timestamp(self) -> None:
        line = json.dumps({"event": "tool_success", "tool": "get_accounts", "ts": "2025-10-19T14:00:00"})
        call = parse_structlog_line(line)
        assert call is not None
        assert call.timestamp == datetime(2025, 10, 19, 14, 0, 0)


# ---------------------------------------------------------------------------
# Integration: parse_log_file
# ---------------------------------------------------------------------------


@pytest.fixture
def new_york_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the local zone: wrapper lines carry UTC times, server lines local ones."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("new_york_time")
class TestParseLogFile:
    """Test the full log file parser with mixed formats."""

    def test_mixed_format_log(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        log.write_text(
            # Wrapper format
            "2025-10-19T14:13:44.066Z [monarch-money] [info] Message from client: "
            '{"method":"tools/call","params":{"name":"get_categories","arguments":{}},"jsonrpc":"2.0","id":4} '
            "{ metadata: undefined }\n"
            # Legacy TOOL_CALL
            "2025-10-19 10:13:44,068 - __main__ - INFO - [TOOL_CALL] get_categories | args: {}\n"
            # Analytics
            "2025-10-19 10:13:44,255 - __main__ - INFO - [ANALYTICS] tool_called: get_categories | time: 0.187s | status: success\n"
            # Result size
            "2025-10-19 10:13:44,255 - __main__ - INFO - [RESULT_SIZE] get_categories | chars: 33,123 | size: 32.35 KB | categories: 67 items\n"
        )
        calls = parse_log_file(log)
        # Wrapper and legacy for same tool should not duplicate
        assert len(calls) == 1
        call = calls[0]
        assert call.tool_name == "get_categories"
        assert call.execution_time_s == 0.187
        assert call.status == "success"
        assert call.result_chars == 33123

    def test_server_log_lines_pair_into_calls(self, tmp_path: Path) -> None:
        # The server's stdlib handler prefixes each structlog event with asctime.
        log = tmp_path / "test.log"
        log.write_text(
            '2026-11-07 10:00:00,000 - server - INFO - {"tool": "get_accounts", "args": {"verbose": false}, "event": "tool_call"}\n'
            '2026-11-07 10:00:00,500 - server - INFO - {"tool": "get_accounts", "time_s": 0.5, "result_chars": 1200, "event": "tool_success"}\n'
            '2026-11-07 10:00:01,000 - server - INFO - {"tool": "get_accounts", "args": {}, "event": "tool_call"}\n'
            '2026-11-07 10:00:01,200 - server - ERROR - {"tool": "get_accounts", "time_s": 0.2, "error": "boom", "event": "tool_error"}\n'
        )
        calls = parse_log_file(log)
        assert [(c.status, c.execution_time_s, c.result_chars) for c in calls] == [
            ("success", 0.5, 1200),
            ("error", 0.2, None),
        ]
        assert calls[0].arguments == {"verbose": False}
        assert calls[0].timestamp == datetime(2026, 11, 7, 10, 0, 0)

    def test_claude_desktop_log_counts_each_call_once(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        log.write_text(
            "2026-11-07T15:00:00.000Z [monarch-money] [info] Message from client: "
            '{"method":"tools/call","params":{"name":"get_accounts","arguments":{}},"jsonrpc":"2.0","id":4} '
            "{ metadata: undefined }\n"
            "2026-11-07T15:00:00.010Z [monarch-money] [info] 2026-11-07 10:00:00,010 - server - INFO - "
            '{"tool": "get_accounts", "args": {}, "event": "tool_call"} { metadata: undefined }\n'
            "2026-11-07T15:00:00.400Z [monarch-money] [info] 2026-11-07 10:00:00,400 - server - INFO - "
            '{"tool": "get_accounts", "time_s": 0.39, "result_chars": 900, "event": "tool_success"} '
            "{ metadata: undefined }\n"
        )
        calls = parse_log_file(log)
        assert len(calls) == 1
        assert (calls[0].status, calls[0].execution_time_s, calls[0].result_chars) == ("success", 0.39, 900)

    def test_wrapper_request_and_unwrapped_server_lines_count_once(self, tmp_path: Path) -> None:
        # 15:00Z is 10:00 in New York on this date.
        log = tmp_path / "test.log"
        log.write_text(
            "2026-11-07T15:00:00.000Z [monarch-money] [info] Message from client: "
            '{"method":"tools/call","params":{"name":"get_accounts","arguments":{"verbose":true}},"jsonrpc":"2.0","id":4} '
            "{ metadata: undefined }\n"
            '2026-11-07 10:00:00,010 - server - INFO - {"tool": "get_accounts", "args": {"verbose": true}, "event": "tool_call"}\n'
            '2026-11-07 10:00:00,400 - server - INFO - {"tool": "get_accounts", "time_s": 0.39, "event": "tool_success"}\n'
        )
        calls = parse_log_file(log)
        assert [(c.arguments, c.status, c.execution_time_s) for c in calls] == [({"verbose": True}, "success", 0.39)]

    def test_overlapping_calls_to_one_tool_stay_separate(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        log.write_text(
            '2026-11-07 10:00:00,000 - server - INFO - {"tool": "search_transactions", "args": {"query": "a"}, "event": "tool_call"}\n'
            '2026-11-07 10:00:00,100 - server - INFO - {"tool": "search_transactions", "args": {"query": "b"}, "event": "tool_call"}\n'
            '2026-11-07 10:00:00,500 - server - INFO - {"tool": "search_transactions", "time_s": 0.5, "event": "tool_success"}\n'
            '2026-11-07 10:00:00,600 - server - INFO - {"tool": "search_transactions", "time_s": 0.5, "event": "tool_success"}\n'
        )
        calls = parse_log_file(log)
        assert [(c.arguments, c.status) for c in calls] == [({"query": "a"}, "success"), ({"query": "b"}, "success")]

    def test_since_filter(self, tmp_path: Path) -> None:
        log = tmp_path / "test.log"
        log.write_text(
            "2025-10-18 09:00:00,000 - __main__ - INFO - [TOOL_CALL] old_tool | args: {}\n"
            "2025-10-19 10:00:00,000 - __main__ - INFO - [TOOL_CALL] new_tool | args: {}\n"
        )
        since = datetime(2025, 10, 19)
        calls = parse_log_file(log, since=since)
        assert len(calls) == 1
        assert calls[0].tool_name == "new_tool"


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------


class TestDetectSessions:
    def test_single_session(self) -> None:
        calls = [
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 0, 0), tool_name="a", arguments={}),
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 1, 0), tool_name="b", arguments={}),
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 2, 0), tool_name="c", arguments={}),
        ]
        sessions = detect_sessions(calls)
        assert len(sessions) == 1
        assert len(sessions[0].calls) == 3

    def test_two_sessions(self) -> None:
        calls = [
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 0, 0), tool_name="a", arguments={}),
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 1, 0), tool_name="b", arguments={}),
            # 10 minute gap
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 11, 0), tool_name="c", arguments={}),
            ToolCall(timestamp=datetime(2025, 10, 19, 10, 12, 0), tool_name="d", arguments={}),
        ]
        sessions = detect_sessions(calls)
        assert len(sessions) == 2
        assert len(sessions[0].calls) == 2
        assert len(sessions[1].calls) == 2

    def test_empty_calls(self) -> None:
        assert detect_sessions([]) == []


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------


class TestComputeToolStats:
    def test_basic_stats(self) -> None:
        calls = [
            ToolCall(
                timestamp=datetime(2025, 10, 19, 10, 0),
                tool_name="get_tx",
                arguments={"limit": 100},
                execution_time_s=0.5,
                result_chars=10240,
                result_items=50,
                status="success",
            ),
            ToolCall(
                timestamp=datetime(2025, 10, 19, 10, 1),
                tool_name="get_tx",
                arguments={"limit": 200},
                execution_time_s=1.0,
                result_chars=20480,
                result_items=100,
                status="success",
            ),
        ]
        stats = compute_tool_stats(calls)
        assert "get_tx" in stats
        s = stats["get_tx"]
        assert s.call_count == 2
        assert s.total_chars == 30720
        assert s.total_items == 150
        assert len(s.sizes_kb) == 2
        assert len(s.times_s) == 2
        assert s.error_count == 0

    def test_error_counting(self) -> None:
        calls = [
            ToolCall(timestamp=datetime(2025, 1, 1), tool_name="t", arguments={}, status="error"),
            ToolCall(timestamp=datetime(2025, 1, 1), tool_name="t", arguments={}, status="success"),
        ]
        stats = compute_tool_stats(calls)
        assert stats["t"].error_count == 1

    def test_arg_patterns(self) -> None:
        calls = [
            ToolCall(timestamp=datetime(2025, 1, 1), tool_name="t", arguments={"verbose": "false"}),
            ToolCall(timestamp=datetime(2025, 1, 1), tool_name="t", arguments={"verbose": "false"}),
            ToolCall(timestamp=datetime(2025, 1, 1), tool_name="t", arguments={"verbose": "true"}),
        ]
        stats = compute_tool_stats(calls)
        assert stats["t"].arg_patterns["verbose"]["false"] == 2
        assert stats["t"].arg_patterns["verbose"]["true"] == 1


# ---------------------------------------------------------------------------
# Sequence analysis
# ---------------------------------------------------------------------------


class TestSequenceAnalysis:
    def test_find_patterns(self) -> None:
        base = datetime(2025, 10, 19, 10, 0, 0)
        calls = [
            ToolCall(timestamp=base, tool_name="get_categories", arguments={}),
            ToolCall(timestamp=base.replace(second=5), tool_name="update_bulk", arguments={}),
            ToolCall(timestamp=base.replace(second=10), tool_name="get_categories", arguments={}),
            ToolCall(timestamp=base.replace(second=15), tool_name="update_bulk", arguments={}),
        ]
        patterns = find_sequence_patterns(calls, window=2)
        tool_seqs = [p.tools for p in patterns]
        assert ("get_categories", "update_bulk") in tool_seqs

    def test_consecutive_repeats(self) -> None:
        base = datetime(2025, 10, 19, 10, 0, 0)
        calls = [
            ToolCall(timestamp=base, tool_name="search", arguments={}),
            ToolCall(timestamp=base.replace(second=1), tool_name="search", arguments={}),
            ToolCall(timestamp=base.replace(second=2), tool_name="search", arguments={}),
            ToolCall(timestamp=base.replace(second=3), tool_name="other", arguments={}),
        ]
        repeats = find_consecutive_repeats(calls)
        assert len(repeats) == 1
        tool, max_streak, count = repeats[0]
        assert tool == "search"
        assert max_streak == 3
        assert count == 1


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    def test_caching_recommendation(self) -> None:
        """Tools with identical result sizes should trigger caching recommendation."""
        stats = {
            "get_categories": ToolStats(
                tool_name="get_categories",
                call_count=5,
                total_chars=160000,
                sizes_kb=[32.0, 32.0, 32.0, 32.0, 32.0],
                times_s=[0.2, 0.2, 0.2, 0.2, 0.2],
            ),
        }
        recs = generate_recommendations(stats, [], [])
        caching_recs = [r for r in recs if r.category == "caching"]
        assert len(caching_recs) >= 1

    def test_oversized_response(self) -> None:
        """Large responses should trigger oversized recommendation."""
        stats = {
            "search_tx": ToolStats(
                tool_name="search_tx",
                call_count=1,
                total_chars=300000,
                sizes_kb=[300.0],
            ),
        }
        recs = generate_recommendations(stats, [], [])
        oversized = [r for r in recs if r.category == "oversized_response"]
        assert len(oversized) >= 1

    def test_repeated_calls_recommendation(self) -> None:
        """Consecutive repeats should trigger recommendation."""
        recs = generate_recommendations({}, [], [("search_transactions", 5, 3)])
        repeated = [r for r in recs if r.category == "repeated_calls"]
        assert len(repeated) >= 1

    def test_bulk_update_bloat(self) -> None:
        """Bulk update with large per-item responses should trigger recommendation."""
        stats = {
            "update_transactions_bulk": ToolStats(
                tool_name="update_transactions_bulk",
                call_count=3,
                total_chars=29031,
                total_items=68,
                sizes_kb=[3.71, 6.91, 17.73],
                times_s=[5.4, 0.37, 0.9],
            ),
        }
        recs = generate_recommendations(stats, [], [])
        bloat = [r for r in recs if r.category == "response_bloat"]
        assert len(bloat) >= 1
        assert "update_transactions_bulk" in bloat[0].message


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_main_with_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "test.log"
        log.write_text(
            "2025-10-19 10:13:44,068 - __main__ - INFO - [TOOL_CALL] get_accounts | args: {}\n"
            "2025-10-19 10:13:44,255 - __main__ - INFO - [ANALYTICS] tool_called: get_accounts | time: 0.2s | status: success\n"
            "2025-10-19 10:13:44,255 - __main__ - INFO - [RESULT_SIZE] get_accounts | chars: 5,000 | size: 4.88 KB\n"
        )
        main(["--log", str(log)])
        captured = capsys.readouterr()
        assert "get_accounts" in captured.out

    def test_main_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "test.log"
        log.write_text(
            "2025-10-19 10:13:44,068 - __main__ - INFO - [TOOL_CALL] get_accounts | args: {}\n"
            "2025-10-19 10:13:44,255 - __main__ - INFO - [ANALYTICS] tool_called: get_accounts | time: 0.2s | status: success\n"
        )
        main(["--log", str(log), "--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["summary"]["total_calls"] == 1

    def test_main_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            main(["--log", str(tmp_path / "nonexistent.log")])
