#!/usr/bin/env python3
"""MCP Log Analyzer — parses Monarch Money MCP server logs and generates optimization reports.

Supports 3 log formats:
  1. Claude Desktop wrapper: `Message from client: {"method":"tools/call",...}`
  2. Legacy markers: `[TOOL_CALL]`, `[ANALYTICS]`, `[RESULT_SIZE]`
  3. Structlog JSON: `{"event": "tool_success", ...}`

Usage:
  uv run scripts/analyze_logs.py                     # full report
  uv run scripts/analyze_logs.py --json              # JSON output
  uv run scripts/analyze_logs.py --since 2026-02-01  # filter by date
  uv run scripts/analyze_logs.py --log path/to/file  # custom log path
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

# Default log path for Claude Desktop MCP logs
DEFAULT_LOG_PATH = Path.home() / "Library" / "Logs" / "Claude" / "mcp-server-monarch-money.log"

# Token estimation: ~4 chars per token (conservative for JSON)
CHARS_PER_TOKEN = 4

# Thresholds for recommendations
LARGE_RESULT_KB = 100
HIGH_ITEM_COUNT = 500
LOW_VARIANCE_THRESHOLD = 0.1  # coefficient of variation


@dataclass
class ToolCall:
    """A single parsed tool call from the log."""

    timestamp: datetime
    tool_name: str
    arguments: dict[str, object]
    execution_time_s: float | None = None
    result_chars: int | None = None
    result_items: int | None = None
    status: str = "unknown"
    line_number: int = 0


@dataclass
class Session:
    """A group of tool calls belonging to one session (gap-delimited)."""

    session_id: str
    start_time: datetime
    end_time: datetime
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolStats:
    """Aggregated statistics for a single tool."""

    tool_name: str
    call_count: int = 0
    total_chars: int = 0
    total_items: int = 0
    sizes_kb: list[float] = field(default_factory=list)
    times_s: list[float] = field(default_factory=list)
    arg_patterns: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    error_count: int = 0


@dataclass
class SequencePattern:
    """A frequently observed tool call sequence."""

    tools: tuple[str, ...]
    count: int
    avg_total_kb: float = 0.0


@dataclass
class Recommendation:
    """An optimization recommendation with estimated savings."""

    priority: str  # "high", "medium", "low"
    category: str
    message: str
    estimated_savings_kb: float = 0.0
    estimated_savings_tokens: int = 0


# ---------------------------------------------------------------------------
# Log Parsers
# ---------------------------------------------------------------------------

# Claude Desktop wrapper format
_WRAPPER_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s+\[.*?\]\s+\[info\]\s+"
    r'Message from client:\s+(\{.*"method"\s*:\s*"tools/call".*\})\s',
)

# Legacy marker formats
_TOOL_CALL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+[\d:,]+)\s+.*\[TOOL_CALL\]\s+(\w+)\s+\|\s+args:\s+(.+)$")
_ANALYTICS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+[\d:,]+)\s+.*\[ANALYTICS\]\s+tool_(?:called|error):\s+(\w+)\s+\|\s+"
    r"time:\s+([\d.]+)s\s+\|\s+status:\s+(\w+)"
)
_ANALYTICS_ERROR_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+[\d:,]+)\s+.*\[ANALYTICS\]\s+tool_error:\s+(\w+)\s+\|\s+"
    r"time:\s+([\d.]+)s\s+\|\s+error:\s+(.+)$"
)
_RESULT_SIZE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+[\d:,]+)\s+.*\[RESULT_SIZE\]\s+(\w+)\s+\|\s+"
    r"chars:\s+([\d,]+)\s+\|\s+size:\s+([\d.]+)\s+KB"
)
_RESULT_ITEMS_RE = re.compile(r"\|\s+(?:transactions|categories|results):\s+(\d+)\s+items")


def parse_timestamp_wrapper(ts: str) -> datetime:
    """Parse ISO timestamp from Claude Desktop wrapper lines."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))


def parse_timestamp_legacy(ts: str) -> datetime:
    """Parse timestamp from legacy log lines (2025-10-19 10:13:44,068)."""
    return datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S,%f")


def parse_wrapper_line(line: str) -> ToolCall | None:
    """Parse a Claude Desktop wrapper log line into a ToolCall."""
    m = _WRAPPER_RE.match(line)
    if not m:
        return None
    ts_str, json_str = m.group(1), m.group(2)
    try:
        msg = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    params = msg.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})
    if not tool_name:
        return None

    return ToolCall(
        timestamp=parse_timestamp_wrapper(ts_str),
        tool_name=tool_name,
        arguments=arguments,
    )


def parse_tool_call_line(line: str) -> ToolCall | None:
    """Parse a [TOOL_CALL] legacy marker line."""
    m = _TOOL_CALL_RE.match(line)
    if not m:
        return None
    ts_str, tool_name, args_str = m.group(1), m.group(2), m.group(3)

    # args_str looks like Python dict repr: {'key': 'val', ...}
    # Use a safe eval approach: convert to JSON-like
    try:
        # Replace Python None/True/False with JSON equivalents
        json_str = args_str.replace("None", "null").replace("True", "true").replace("False", "false")
        json_str = json_str.replace("'", '"')
        arguments = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        arguments = {"_raw": args_str}

    return ToolCall(
        timestamp=parse_timestamp_legacy(ts_str),
        tool_name=tool_name,
        arguments=arguments,
    )


def parse_analytics_line(line: str) -> tuple[str, str, float, str] | None:
    """Parse a [ANALYTICS] line → (timestamp_str, tool_name, time_s, status)."""
    m = _ANALYTICS_ERROR_RE.match(line)
    if m:
        return (m.group(1), m.group(2), float(m.group(3)), "error")
    m = _ANALYTICS_RE.match(line)
    if m:
        return (m.group(1), m.group(2), float(m.group(3)), m.group(4))
    return None


def parse_result_size_line(line: str) -> tuple[str, str, int, float, int | None] | None:
    """Parse a [RESULT_SIZE] line → (timestamp_str, tool_name, chars, kb, items?)."""
    m = _RESULT_SIZE_RE.match(line)
    if not m:
        return None
    ts_str = m.group(1)
    tool_name = m.group(2)
    chars = int(m.group(3).replace(",", ""))
    kb = float(m.group(4))
    items_m = _RESULT_ITEMS_RE.search(line)
    items = int(items_m.group(1)) if items_m else None
    return (ts_str, tool_name, chars, kb, items)


def parse_structlog_line(line: str) -> ToolCall | None:
    """Parse a structlog JSON line."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    event = data.get("event", "")
    if event not in ("tool_called", "tool_success", "tool_error", "tool_call"):
        return None

    tool_name = data.get("tool", "")
    if not tool_name:
        return None

    ts_str = data.get("timestamp", data.get("ts", ""))
    try:
        ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
    except (ValueError, TypeError):
        ts = datetime.now()

    return ToolCall(
        timestamp=ts,
        tool_name=tool_name,
        arguments=data.get("args", {}),
        execution_time_s=data.get("time_s"),
        result_chars=data.get("result_chars"),
        status="error" if "error" in event else "success",
    )


# ---------------------------------------------------------------------------
# Log file parser — merges all formats
# ---------------------------------------------------------------------------


def parse_log_file(path: Path, since: datetime | None = None) -> list[ToolCall]:
    """Parse a log file and return a list of ToolCall objects, merging all formats.

    Handles deduplication when both wrapper and legacy formats log the same call:
    wrapper lines create the call, legacy [TOOL_CALL] lines within 2 seconds are
    treated as duplicates and merged rather than creating new entries.
    """
    calls: list[ToolCall] = []
    # Most recent ToolCall per tool_name, for attaching analytics/size data
    pending_calls: dict[str, ToolCall] = {}

    def _is_recent_dup(tool_name: str, ts: datetime) -> bool:
        """Check if a call is a duplicate of the most recent pending call for this tool."""
        prev = pending_calls.get(tool_name)
        if prev is None:
            return False
        gap = abs((ts - prev.timestamp).total_seconds())
        return gap < 2.0

    with open(path) as f:
        for line_num, line in enumerate(f, 1):
            # Try wrapper format first (has tool name + args from JSON-RPC)
            call = parse_wrapper_line(line)
            if call:
                call.line_number = line_num
                if since and call.timestamp < since:
                    continue
                calls.append(call)
                pending_calls[call.tool_name] = call
                continue

            # Try [TOOL_CALL] marker (has tool name + args)
            call = parse_tool_call_line(line)
            if call:
                call.line_number = line_num
                if since and call.timestamp < since:
                    continue
                # Skip if this is a duplicate of a recent wrapper line
                if _is_recent_dup(call.tool_name, call.timestamp):
                    # Update pending with richer args from legacy format if available
                    prev = pending_calls[call.tool_name]
                    if not prev.arguments and call.arguments:
                        prev.arguments = call.arguments
                    continue
                calls.append(call)
                pending_calls[call.tool_name] = call
                continue

            # Try [ANALYTICS] marker (has timing + status)
            analytics = parse_analytics_line(line)
            if analytics:
                ts_str, tool_name, time_s, status = analytics
                if tool_name in pending_calls:
                    pending_calls[tool_name].execution_time_s = time_s
                    pending_calls[tool_name].status = status
                continue

            # Try [RESULT_SIZE] marker (has result size info)
            size_info = parse_result_size_line(line)
            if size_info:
                ts_str, tool_name, chars, kb, items = size_info
                if tool_name in pending_calls:
                    pending_calls[tool_name].result_chars = chars
                    pending_calls[tool_name].result_items = items
                continue

            # Try structlog JSON
            call = parse_structlog_line(line)
            if call:
                call.line_number = line_num
                if since and call.timestamp < since:
                    continue
                calls.append(call)
                pending_calls[call.tool_name] = call

    return calls


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------

SESSION_GAP_SECONDS = 300  # 5 minutes of silence = new session


def detect_sessions(calls: list[ToolCall]) -> list[Session]:
    """Group tool calls into sessions based on time gaps."""
    if not calls:
        return []

    sorted_calls = sorted(calls, key=lambda c: c.timestamp)
    sessions: list[Session] = []
    current_calls: list[ToolCall] = [sorted_calls[0]]

    for call in sorted_calls[1:]:
        gap = (call.timestamp - current_calls[-1].timestamp).total_seconds()
        if gap > SESSION_GAP_SECONDS:
            sessions.append(
                Session(
                    session_id=f"session_{len(sessions) + 1}",
                    start_time=current_calls[0].timestamp,
                    end_time=current_calls[-1].timestamp,
                    calls=current_calls,
                )
            )
            current_calls = [call]
        else:
            current_calls.append(call)

    # last session
    sessions.append(
        Session(
            session_id=f"session_{len(sessions) + 1}",
            start_time=current_calls[0].timestamp,
            end_time=current_calls[-1].timestamp,
            calls=current_calls,
        )
    )

    return sessions


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------


def compute_tool_stats(calls: list[ToolCall]) -> dict[str, ToolStats]:
    """Compute aggregated statistics per tool."""
    stats: dict[str, ToolStats] = {}

    for call in calls:
        if call.tool_name not in stats:
            stats[call.tool_name] = ToolStats(tool_name=call.tool_name)
        s = stats[call.tool_name]
        s.call_count += 1

        if call.result_chars is not None:
            s.total_chars += call.result_chars
            s.sizes_kb.append(call.result_chars / 1024)
            if call.result_items is not None:
                s.total_items += call.result_items

        if call.execution_time_s is not None:
            s.times_s.append(call.execution_time_s)

        if call.status == "error":
            s.error_count += 1

        # Track argument patterns
        for key, value in call.arguments.items():
            s.arg_patterns[key][str(value)] += 1

    return stats


# ---------------------------------------------------------------------------
# Sequence analysis
# ---------------------------------------------------------------------------


def find_sequence_patterns(calls: list[ToolCall], window: int = 2) -> list[SequencePattern]:
    """Find frequently repeated tool call sequences."""
    sorted_calls = sorted(calls, key=lambda c: c.timestamp)
    sequences: Counter[tuple[str, ...]] = Counter()
    sizes: dict[tuple[str, ...], list[float]] = defaultdict(list)

    for i in range(len(sorted_calls) - window + 1):
        window_calls = sorted_calls[i : i + window]
        # Only count sequences within 30 seconds
        gap = (window_calls[-1].timestamp - window_calls[0].timestamp).total_seconds()
        if gap > 30:
            continue
        seq = tuple(c.tool_name for c in window_calls)
        sequences[seq] += 1
        total_kb = sum((c.result_chars or 0) / 1024 for c in window_calls)
        sizes[seq].append(total_kb)

    # Also detect consecutive same-tool calls
    consecutive: Counter[str] = Counter()
    max_streak: dict[str, int] = {}
    i = 0
    while i < len(sorted_calls):
        tool = sorted_calls[i].tool_name
        streak = 1
        while i + streak < len(sorted_calls) and sorted_calls[i + streak].tool_name == tool:
            streak += 1
        if streak >= 2:
            consecutive[tool] += 1
            max_streak[tool] = max(max_streak.get(tool, 0), streak)
        i += streak

    patterns = []
    for seq, count in sequences.most_common(20):
        if count >= 2:
            avg_kb = mean(sizes[seq]) if sizes[seq] else 0.0
            patterns.append(SequencePattern(tools=seq, count=count, avg_total_kb=avg_kb))

    return patterns


def find_consecutive_repeats(calls: list[ToolCall]) -> list[tuple[str, int, int]]:
    """Find runs of consecutive calls to the same tool. Returns (tool, max_streak, total_runs)."""
    sorted_calls = sorted(calls, key=lambda c: c.timestamp)
    repeats: dict[str, tuple[int, int]] = {}  # tool → (max_streak, run_count)

    i = 0
    while i < len(sorted_calls):
        tool = sorted_calls[i].tool_name
        streak = 1
        while i + streak < len(sorted_calls) and sorted_calls[i + streak].tool_name == tool:
            streak += 1
        if streak >= 2:
            prev_max, prev_count = repeats.get(tool, (0, 0))
            repeats[tool] = (max(prev_max, streak), prev_count + 1)
        i += streak

    return [(tool, max_s, count) for tool, (max_s, count) in sorted(repeats.items(), key=lambda x: -x[1][0])]


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------


def generate_recommendations(
    stats: dict[str, ToolStats],
    sequences: list[SequencePattern],
    repeats: list[tuple[str, int, int]],
) -> list[Recommendation]:
    """Generate optimization recommendations based on usage patterns."""
    recs: list[Recommendation] = []

    for tool_name, s in stats.items():
        # Rule 1: Caching candidates — high call count + low size variance
        if s.call_count >= 3 and len(s.sizes_kb) >= 2:
            avg_kb = mean(s.sizes_kb)
            if avg_kb > 0:
                cv = stdev(s.sizes_kb) / avg_kb if len(s.sizes_kb) > 1 else 0
                if cv < LOW_VARIANCE_THRESHOLD:
                    savings = s.total_chars * (s.call_count - 1) / s.call_count
                    recs.append(
                        Recommendation(
                            priority="high",
                            category="caching",
                            message=(
                                f"`{tool_name}` called {s.call_count}x with nearly identical results "
                                f"(avg {avg_kb:.1f} KB, CV={cv:.2f}). "
                                f"Cache this response to save ~{savings / 1024:.0f} KB / "
                                f"~{int(savings / CHARS_PER_TOKEN):,} tokens."
                            ),
                            estimated_savings_kb=savings / 1024,
                            estimated_savings_tokens=int(savings / CHARS_PER_TOKEN),
                        )
                    )

        # Rule 2: Oversized responses
        if s.sizes_kb:
            max_kb = max(s.sizes_kb)
            if max_kb > LARGE_RESULT_KB:
                recs.append(
                    Recommendation(
                        priority="high",
                        category="oversized_response",
                        message=(
                            f"`{tool_name}` returned {max_kb:.0f} KB in a single call. "
                            f"Consider adding compact mode, reducing limits, or trimming fields."
                        ),
                        estimated_savings_kb=max_kb * 0.5,
                        estimated_savings_tokens=int(max_kb * 1024 * 0.5 / CHARS_PER_TOKEN),
                    )
                )

        # Rule 3: Missing limits — high item counts
        if s.total_items > 0:
            avg_items = s.total_items / s.call_count
            if avg_items >= HIGH_ITEM_COUNT:
                recs.append(
                    Recommendation(
                        priority="medium",
                        category="missing_limits",
                        message=(
                            f"`{tool_name}` averages {avg_items:.0f} items per call. "
                            f"Consider lower default limits or pagination."
                        ),
                    )
                )

        # Rule 4: Verbose mode usage
        verbose_counts = s.arg_patterns.get("verbose", Counter())
        verbose_true = verbose_counts.get("True", 0) + verbose_counts.get("true", 0)
        verbose_false = verbose_counts.get("False", 0) + verbose_counts.get("false", 0)
        if verbose_true > 0 and s.sizes_kb:
            recs.append(
                Recommendation(
                    priority="low",
                    category="format_waste",
                    message=(
                        f"`{tool_name}` used verbose=True {verbose_true}x vs compact {verbose_false}x. "
                        f"Verbose mode may return unnecessary fields."
                    ),
                )
            )

    # Rule 5: Repeated consecutive calls (pagination/refinement loops)
    for tool_name, max_streak, run_count in repeats:
        recs.append(
            Recommendation(
                priority="medium",
                category="repeated_calls",
                message=(
                    f"`{tool_name}` called consecutively {run_count}x "
                    f"(max streak: {max_streak}). "
                    f"May indicate pagination or refinement loops — "
                    f"consider batch operations or broader queries."
                ),
            )
        )

    # Rule 6: Common fetch→update patterns
    for seq_pattern in sequences:
        if len(seq_pattern.tools) == 2:
            a, b = seq_pattern.tools
            if "categories" in a and "update" in b and seq_pattern.count >= 2:
                recs.append(
                    Recommendation(
                        priority="medium",
                        category="redundant_lookup",
                        message=(
                            f"Pattern `{a}` → `{b}` seen {seq_pattern.count}x. "
                            f"Categories are static data — cache the first response."
                        ),
                    )
                )

    # Rule 7: Bulk update response bloat
    bulk_stats = stats.get("update_transactions_bulk")
    if bulk_stats and bulk_stats.sizes_kb:
        avg_kb = mean(bulk_stats.sizes_kb)
        avg_items = bulk_stats.total_items / bulk_stats.call_count if bulk_stats.call_count else 0
        if avg_items > 0:
            per_item_kb = avg_kb / avg_items
            if per_item_kb > 0.2:  # more than 200 bytes per item is bloated for a status response
                compact_kb = avg_items * 0.05  # ~50 bytes for {id, status}
                savings = (avg_kb - compact_kb) * bulk_stats.call_count
                recs.append(
                    Recommendation(
                        priority="high",
                        category="response_bloat",
                        message=(
                            f"`update_transactions_bulk` echoes full transaction objects "
                            f"(~{per_item_kb:.1f} KB/item, avg {avg_items:.0f} items). "
                            f"Compact responses ({'{'}id, status{'}'}) would save ~{savings:.0f} KB / "
                            f"~{int(savings * 1024 / CHARS_PER_TOKEN):,} tokens."
                        ),
                        estimated_savings_kb=savings,
                        estimated_savings_tokens=int(savings * 1024 / CHARS_PER_TOKEN),
                    )
                )

    # Sort: high > medium > low
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: (priority_order.get(r.priority, 3), -r.estimated_savings_tokens))
    return recs


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_report(
    stats: dict[str, ToolStats],
    sessions: list[Session],
    sequences: list[SequencePattern],
    repeats: list[tuple[str, int, int]],
    recommendations: list[Recommendation],
    calls: list[ToolCall],
) -> str:
    """Format a human-readable report."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  MONARCH MCP LOG ANALYSIS REPORT")
    lines.append("=" * 70)

    total_calls = len(calls)
    total_kb = sum(s.total_chars for s in stats.values()) / 1024
    total_tokens = int(sum(s.total_chars for s in stats.values()) / CHARS_PER_TOKEN)
    total_errors = sum(s.error_count for s in stats.values())

    lines.append(f"\nTotal tool calls: {total_calls}")
    lines.append(f"Total data returned: {total_kb:.0f} KB (~{total_tokens:,} tokens)")
    lines.append(f"Sessions detected: {len(sessions)}")
    lines.append(f"Errors: {total_errors}")

    # Tool usage table
    lines.append("\n" + "-" * 70)
    lines.append("  TOOL USAGE STATS")
    lines.append("-" * 70)
    lines.append(f"{'Tool':<35} {'Calls':>6} {'Total KB':>10} {'Avg KB':>8} {'Max KB':>8} {'Avg Time':>9}")
    lines.append("-" * 70)

    sorted_stats = sorted(stats.values(), key=lambda s: s.total_chars, reverse=True)
    for s in sorted_stats:
        avg_kb = mean(s.sizes_kb) if s.sizes_kb else 0
        max_kb = max(s.sizes_kb) if s.sizes_kb else 0
        avg_time = f"{mean(s.times_s):.2f}s" if s.times_s else "n/a"
        lines.append(
            f"{s.tool_name:<35} {s.call_count:>6} {s.total_chars / 1024:>10.1f} {avg_kb:>8.1f} {max_kb:>8.1f} {avg_time:>9}"
        )

    # Argument patterns
    lines.append("\n" + "-" * 70)
    lines.append("  ARGUMENT PATTERNS")
    lines.append("-" * 70)

    for s in sorted_stats:
        if not s.arg_patterns:
            continue
        lines.append(f"\n  {s.tool_name}:")
        for param, values in sorted(s.arg_patterns.items()):
            top_values = values.most_common(5)
            values_str = ", ".join(f"{v}({c})" for v, c in top_values)
            lines.append(f"    {param}: {values_str}")

    # Consecutive repeats
    if repeats:
        lines.append("\n" + "-" * 70)
        lines.append("  CONSECUTIVE REPEAT PATTERNS")
        lines.append("-" * 70)
        for tool_name, max_streak, run_count in repeats:
            lines.append(f"  {tool_name}: {run_count} runs, max streak {max_streak}")

    # Common sequences
    if sequences:
        lines.append("\n" + "-" * 70)
        lines.append("  COMMON TOOL SEQUENCES (within 30s)")
        lines.append("-" * 70)
        for sp in sequences[:15]:
            seq_str = " -> ".join(sp.tools)
            lines.append(f"  {seq_str}: {sp.count}x (avg {sp.avg_total_kb:.1f} KB)")

    # Recommendations
    lines.append("\n" + "=" * 70)
    lines.append("  RECOMMENDATIONS")
    lines.append("=" * 70)

    if not recommendations:
        lines.append("  No specific optimizations identified.")
    else:
        for rec in recommendations:
            priority_icon = {"high": "!!!", "medium": " !!", "low": "  !"}
            icon = priority_icon.get(rec.priority, "  ?")
            lines.append(f"\n  {icon} [{rec.priority.upper()}] {rec.category}")
            lines.append(f"      {rec.message}")
            if rec.estimated_savings_tokens > 0:
                lines.append(
                    f"      Estimated savings: {rec.estimated_savings_kb:.0f} KB / ~{rec.estimated_savings_tokens:,} tokens"
                )

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def format_json_report(
    stats: dict[str, ToolStats],
    sessions: list[Session],
    sequences: list[SequencePattern],
    repeats: list[tuple[str, int, int]],
    recommendations: list[Recommendation],
    calls: list[ToolCall],
) -> str:
    """Format a JSON report for programmatic consumption."""
    report = {
        "summary": {
            "total_calls": len(calls),
            "total_kb": round(sum(s.total_chars for s in stats.values()) / 1024, 1),
            "total_estimated_tokens": int(sum(s.total_chars for s in stats.values()) / CHARS_PER_TOKEN),
            "total_errors": sum(s.error_count for s in stats.values()),
            "sessions": len(sessions),
        },
        "tools": {
            name: {
                "call_count": s.call_count,
                "total_kb": round(s.total_chars / 1024, 1),
                "avg_kb": round(mean(s.sizes_kb), 1) if s.sizes_kb else 0,
                "max_kb": round(max(s.sizes_kb), 1) if s.sizes_kb else 0,
                "avg_time_s": round(mean(s.times_s), 3) if s.times_s else None,
                "errors": s.error_count,
                "estimated_tokens": int(s.total_chars / CHARS_PER_TOKEN),
            }
            for name, s in sorted(stats.items(), key=lambda x: -x[1].total_chars)
        },
        "sequences": [
            {"tools": list(sp.tools), "count": sp.count, "avg_total_kb": round(sp.avg_total_kb, 1)}
            for sp in sequences[:15]
        ],
        "consecutive_repeats": [
            {"tool": tool, "max_streak": max_s, "run_count": count} for tool, max_s, count in repeats
        ],
        "recommendations": [
            {
                "priority": r.priority,
                "category": r.category,
                "message": r.message,
                "estimated_savings_kb": round(r.estimated_savings_kb, 1),
                "estimated_savings_tokens": r.estimated_savings_tokens,
            }
            for r in recommendations
        ],
    }
    return json.dumps(report, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Analyze Monarch MCP server logs for optimization opportunities")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH, help="Path to MCP log file")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output in JSON format")
    parser.add_argument("--since", type=str, default=None, help="Only analyze entries after this date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    if not args.log.exists():
        print(f"Error: Log file not found: {args.log}", file=sys.stderr)
        sys.exit(1)

    since = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print(f"Error: Invalid date format: {args.since} (use YYYY-MM-DD)", file=sys.stderr)
            sys.exit(1)

    calls = parse_log_file(args.log, since=since)
    if not calls:
        print("No tool calls found in log file.", file=sys.stderr)
        sys.exit(0)

    stats = compute_tool_stats(calls)
    sessions = detect_sessions(calls)
    sequences = find_sequence_patterns(calls)
    repeats = find_consecutive_repeats(calls)
    recommendations = generate_recommendations(stats, sequences, repeats)

    if args.json_output:
        print(format_json_report(stats, sessions, sequences, repeats, recommendations, calls))
    else:
        print(format_report(stats, sessions, sequences, repeats, recommendations, calls))


if __name__ == "__main__":
    main()
