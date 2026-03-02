#!/usr/bin/env python3
"""MCP Session Evaluator — snapshot/analyze individual sessions for optimization insights.

Modes:
  snapshot   Mark current log position for later analysis
  analyze    Analyze only new log entries since last snapshot
  run        Run a prompt via `claude` CLI, snapshot before + analyze after

Usage:
  uv run scripts/eval_session.py snapshot              # mark current position
  # ... use Monarch tools in Claude Desktop ...
  uv run scripts/eval_session.py analyze               # analyze new entries only
  uv run scripts/eval_session.py analyze --json

  # Automated: run a prompt and analyze the session
  uv run scripts/eval_session.py run "analyze last month's transactions"
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from analyze_logs import (
    CHARS_PER_TOKEN,
    DEFAULT_LOG_PATH,
    Recommendation,
    SequencePattern,
    Session,
    ToolCall,
    ToolStats,
    compute_tool_stats,
    detect_sessions,
    find_consecutive_repeats,
    find_sequence_patterns,
    generate_recommendations,
    parse_log_file,
)

SNAPSHOT_FILE = Path(__file__).parent / ".eval_snapshot"


def _get_line_count(log_path: Path) -> int:
    """Count lines in a file efficiently."""
    with open(log_path) as f:
        return sum(1 for _ in f)


def cmd_snapshot(log_path: Path) -> None:
    """Record the current log file size as a snapshot marker."""
    if not log_path.exists():
        print(f"Error: Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    size = log_path.stat().st_size
    line_count = _get_line_count(log_path)

    snapshot = {"log_path": str(log_path), "byte_offset": size, "line_count": line_count}
    SNAPSHOT_FILE.write_text(json.dumps(snapshot))
    print(f"Snapshot saved: {line_count} lines, {size:,} bytes")
    print("Now use your MCP tools, then run: uv run scripts/eval_session.py analyze")


def _parse_new_lines(log_path: Path, prev_lines: int) -> list[ToolCall]:
    """Parse only lines added after prev_lines."""
    new_lines: list[str] = []
    with open(log_path) as f:
        for i, line in enumerate(f, 1):
            if i > prev_lines:
                new_lines.append(line)

    if not new_lines:
        return []

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tmp:
        tmp.writelines(new_lines)
        tmp_path = Path(tmp.name)

    try:
        return parse_log_file(tmp_path)
    finally:
        tmp_path.unlink()


def cmd_analyze(log_path: Path, json_output: bool = False) -> None:
    """Analyze log entries added since the last snapshot."""
    if not SNAPSHOT_FILE.exists():
        print("No snapshot found. Run 'snapshot' first.", file=sys.stderr)
        print("  uv run scripts/eval_session.py snapshot", file=sys.stderr)
        sys.exit(1)

    snapshot = json.loads(SNAPSHOT_FILE.read_text())
    prev_lines = snapshot["line_count"]

    if not log_path.exists():
        print(f"Error: Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    calls = _parse_new_lines(log_path, prev_lines)

    if not calls:
        print("No tool calls found in new log entries.")
        sys.exit(0)

    stats = compute_tool_stats(calls)
    sessions = detect_sessions(calls)
    sequences = find_sequence_patterns(calls)
    repeats = find_consecutive_repeats(calls)
    recommendations = generate_recommendations(stats, sequences, repeats)

    if json_output:
        _print_json_session_report(calls, stats, recommendations)
    else:
        _print_session_report(calls, stats, sessions, sequences, repeats, recommendations)

    # Clean up snapshot
    SNAPSHOT_FILE.unlink(missing_ok=True)


def cmd_run(prompt: str, log_path: Path, json_output: bool = False) -> None:
    """Run a prompt via `claude` CLI, snapshot before + analyze after.

    Requires `claude` CLI to be installed and configured.
    """
    if not log_path.exists():
        print(f"Error: Log file not found: {log_path}", file=sys.stderr)
        print("Make sure the MCP server has been started at least once.", file=sys.stderr)
        sys.exit(1)

    # Snapshot before
    prev_lines = _get_line_count(log_path)
    print(f"Snapshot: {prev_lines} lines")
    print(f"Running prompt: {prompt}")
    print("-" * 60)

    # Run claude CLI with the prompt (non-interactive mode)
    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        print("Error: `claude` CLI not found. Install it first:", file=sys.stderr)
        print("  npm install -g @anthropic-ai/claude-code", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: claude CLI timed out after 5 minutes", file=sys.stderr)
        sys.exit(1)

    # Print claude's response
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0 and result.stderr:
        print(f"claude stderr: {result.stderr}", file=sys.stderr)

    print("-" * 60)

    # Analyze new log entries
    calls = _parse_new_lines(log_path, prev_lines)

    if not calls:
        print("No MCP tool calls detected in this session.")
        return

    stats = compute_tool_stats(calls)
    sessions = detect_sessions(calls)
    sequences = find_sequence_patterns(calls)
    repeats = find_consecutive_repeats(calls)
    recommendations = generate_recommendations(stats, sequences, repeats)

    if json_output:
        _print_json_session_report(calls, stats, recommendations)
    else:
        _print_session_report(calls, stats, sessions, sequences, repeats, recommendations)


def _print_session_report(
    calls: list[ToolCall],
    stats: dict[str, ToolStats],
    sessions: list[Session],
    sequences: list[SequencePattern],
    repeats: list[tuple[str, int, int]],
    recommendations: list[Recommendation],
) -> None:
    """Print a focused session analysis report."""
    total_chars = sum(s.total_chars for s in stats.values())
    total_kb = total_chars / 1024
    total_tokens = int(total_chars / CHARS_PER_TOKEN)

    print("=" * 60)
    print("  SESSION ANALYSIS")
    print("=" * 60)
    print(f"\nNew tool calls: {len(calls)}")
    print(f"Total data: {total_kb:.1f} KB (~{total_tokens:,} tokens)")
    print(f"Duration: {(calls[-1].timestamp - calls[0].timestamp).total_seconds():.0f}s")

    # Call sequence
    print("\n  CALL SEQUENCE:")
    for i, call in enumerate(calls, 1):
        size_str = f"{call.result_chars / 1024:.1f} KB" if call.result_chars else "n/a"
        time_str = f"{call.execution_time_s:.2f}s" if call.execution_time_s else "n/a"
        print(f"  {i:3}. {call.tool_name:<35} {size_str:>10}  {time_str:>8}")

    # Per-tool summary
    print("\n  PER-TOOL SUMMARY:")
    for name, s in sorted(stats.items(), key=lambda x: -x[1].total_chars):
        avg_kb = s.total_chars / s.call_count / 1024 if s.call_count else 0
        print(f"    {name}: {s.call_count} calls, {s.total_chars / 1024:.1f} KB total, {avg_kb:.1f} KB avg")

    # Recommendations
    if recommendations:
        print("\n  RECOMMENDATIONS:")
        for rec in recommendations:
            priority_icon = {"high": "!!!", "medium": " !!", "low": "  !"}
            print(f"    {priority_icon.get(rec.priority, '  ?')} [{rec.priority.upper()}] {rec.message}")

    print("=" * 60)


def _print_json_session_report(
    calls: list[ToolCall],
    stats: dict[str, ToolStats],
    recommendations: list[Recommendation],
) -> None:
    """Print JSON session report."""
    report = {
        "call_count": len(calls),
        "calls": [
            {
                "tool": c.tool_name,
                "args": c.arguments,
                "result_kb": round(c.result_chars / 1024, 1) if c.result_chars else None,
                "time_s": c.execution_time_s,
                "status": c.status,
            }
            for c in calls
        ],
        "tools": {
            name: {
                "calls": s.call_count,
                "total_kb": round(s.total_chars / 1024, 1),
            }
            for name, s in stats.items()
        },
        "recommendations": [
            {"priority": r.priority, "category": r.category, "message": r.message} for r in recommendations
        ],
    }
    print(json.dumps(report, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate MCP session efficiency")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Mark current log position")
    snap.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)

    analyze = sub.add_parser("analyze", help="Analyze entries since snapshot")
    analyze.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    analyze.add_argument("--json", action="store_true", dest="json_output")

    run = sub.add_parser("run", help="Run prompt via claude CLI + analyze")
    run.add_argument("prompt", help="The prompt to send to claude")
    run.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    run.add_argument("--json", action="store_true", dest="json_output")

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        cmd_snapshot(args.log)
    elif args.command == "analyze":
        cmd_analyze(args.log, json_output=args.json_output)
    elif args.command == "run":
        cmd_run(args.prompt, args.log, json_output=args.json_output)


if __name__ == "__main__":
    main()
