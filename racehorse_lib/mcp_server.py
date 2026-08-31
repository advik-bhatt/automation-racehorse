"""`racehorse mcp`: a hand-rolled MCP stdio server, stdlib only.

No `mcp` pip package (matches this repo's zero-dependency philosophy). The
Model Context Protocol over stdio is just JSON-RPC 2.0 with one message per
line on stdin/stdout (NOT LSP-style Content-Length framing) — so this is a
plain read-a-line, dispatch, write-a-line loop.

Exposes three read-only tools over the same event log the dashboard and
`racehorse status` read, via racehorse_lib.store:
  - get_active_agents  currently running horses
  - race_history       recently finished/stalled horses
  - race_stats         simple aggregate tallies

stdout is reserved for protocol messages only; anything diagnostic goes to
stderr.
"""
from __future__ import annotations

import datetime
import json
import sys
from collections import Counter
from typing import Any

from . import store

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "racehorse", "version": "0.1.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_active_agents",
        "description": (
            "List the horses (Claude Code sessions and subagents) currently running, "
            "each annotated with duration_seconds computed from started_ts to now."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "race_history",
        "description": (
            "List recently finished or stalled horses, most recent first, each "
            "annotated with duration_seconds computed from started_ts to finished_ts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "max entries to return",
                    "default": 20,
                }
            },
        },
    },
    {
        "name": "race_stats",
        "description": (
            "Aggregate stats: most-frequently-run skills (counted over the full "
            "event log), plus history-scoped tallies — most-frequently-seen "
            "subagent types, average subagent duration in seconds, and count of "
            "history entries run under auto permission mode."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _parse_ts(ts: str | None) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tool_get_active_agents(_arguments: dict[str, Any]) -> Any:
    snapshot = store.build_snapshot()
    now = datetime.datetime.now(datetime.timezone.utc)
    horses = []
    for horse in snapshot["horses"]:
        horse = dict(horse)
        started = _parse_ts(horse.get("started_ts"))
        horse["duration_seconds"] = (now - started).total_seconds() if started else None
        horses.append(horse)
    return horses


def _tool_race_history(arguments: dict[str, Any]) -> Any:
    limit = arguments.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if limit < 0:
        limit = 0

    snapshot = store.build_snapshot()
    entries = []
    for horse in snapshot["history"][:limit]:
        horse = dict(horse)
        started = _parse_ts(horse.get("started_ts"))
        finished = _parse_ts(horse.get("finished_ts"))
        horse["duration_seconds"] = (finished - started).total_seconds() if started and finished else None
        entries.append(horse)
    return entries


def _tool_race_stats(_arguments: dict[str, Any]) -> Any:
    snapshot = store.build_snapshot()
    history = snapshot["history"]

    subagent_type_counter: Counter[str] = Counter()
    auto_count = 0
    durations: list[float] = []

    for horse in history:
        if horse.get("permission_mode") == "auto":
            auto_count += 1
        if horse.get("kind") == "subagent":
            if horse.get("agent_type"):
                subagent_type_counter[horse["agent_type"]] += 1
            started = _parse_ts(horse.get("started_ts"))
            finished = _parse_ts(horse.get("finished_ts"))
            if started and finished:
                durations.append((finished - started).total_seconds())

    skill_counter: Counter[str] = Counter()
    for event in store.iter_events():
        label = event.get("activity_label") or ""
        if label.startswith("running skill:"):
            skill = label.split(":", 1)[1].strip()
            if skill:
                skill_counter[skill] += 1

    avg_subagent_duration_seconds = (sum(durations) / len(durations)) if durations else None

    return {
        "most_common_skills": skill_counter.most_common(10),
        "most_common_subagent_types": subagent_type_counter.most_common(10),
        "avg_subagent_duration_seconds": avg_subagent_duration_seconds,
        "auto_permission_count": auto_count,
        "history_entries_considered": len(history),
    }


TOOL_HANDLERS = {
    "get_active_agents": _tool_get_active_agents,
    "race_history": _tool_race_history,
    "race_stats": _tool_race_stats,
}


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _send_result(msg_id: Any, result: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _send_error(msg_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _handle_tools_call(msg_id: Any, params: dict[str, Any]) -> None:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        _send_result(
            msg_id,
            {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True},
        )
        return
    try:
        data = handler(arguments)
        text = json.dumps(data, indent=2, default=str)
        _send_result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
    except Exception as exc:  # noqa: BLE001 - report to the caller, keep the loop alive
        _send_result(
            msg_id,
            {
                "content": [{"type": "text", "text": f"Error running tool {name!r}: {exc}"}],
                "isError": True,
            },
        )


def _handle_message(msg: dict[str, Any]) -> None:
    if not isinstance(msg, dict):
        return
    method = msg.get("method")
    has_id = "id" in msg
    msg_id = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        protocol_version = params.get("protocolVersion") or PROTOCOL_VERSION
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
        if has_id:
            _send_result(msg_id, result)
        return

    if method == "notifications/initialized":
        return  # notification, no response

    if method == "tools/list":
        if has_id:
            _send_result(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        if has_id:
            params = msg.get("params") or {}
            _handle_tools_call(msg_id, params)
        return

    if has_id:
        _send_error(msg_id, -32601, f"Method not found: {method}")
    # unknown notification (no id): nothing to do


def run() -> None:
    """Blocking stdio loop: one JSON-RPC message per line in, one per line out."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # malformed line: skip silently
            _handle_message(msg)
        except Exception as exc:  # noqa: BLE001 - never let one bad message kill the loop
            print(f"racehorse mcp: error handling message: {exc}", file=sys.stderr)
            continue
    # stdin closed (EOF): exit cleanly
