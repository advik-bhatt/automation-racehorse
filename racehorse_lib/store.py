"""Event storage and race-state derivation.

Storage model (one append-only source of truth, everything else rebuilt
from it):

  $RACEHORSE_DIR/events.jsonl   append-only, one normalized event per line

There is no separate "state" file. Every reader (the SSE server, the MCP
server, `racehorse status`) rebuilds the current race snapshot by folding
over events.jsonl. That keeps concurrent hook writers simple (pure
append, no read-modify-write) and makes the on-disk format trivially
inspectable and rebuildable.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DIR = Path.home() / ".local" / "share" / "automation-racehorse"

# A "running" horse with no new events in this long is presumed dead
# (crashed session, killed process, missed SessionEnd/SubagentStop) and is
# folded into history as "stalled" rather than shown as running forever.
IDLE_TIMEOUT_SECONDS = 180

# Hook events that end a horse's race outright, keyed by hook_event_name.
FINISHING_EVENTS = {"SubagentStop", "SessionEnd"}

HISTORY_LIMIT = 200


def racehorse_dir() -> Path:
    d = Path(os.environ.get("RACEHORSE_DIR", "")) if os.environ.get("RACEHORSE_DIR") else DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def events_path() -> Path:
    return racehorse_dir() / "events.jsonl"


def append_event(event: dict[str, Any]) -> None:
    """Append one JSON line. O_APPEND is atomic for writes under PIPE_BUF
    on POSIX, which every event here is well within, so concurrent hook
    invocations (parallel subagents firing hooks at once) never interleave
    or clobber each other without needing a lock file."""
    line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    path = events_path()
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def iter_events(path: Path | None = None) -> Iterator[dict[str, Any]]:
    p = path or events_path()
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _new_horse(event: dict[str, Any]) -> dict[str, Any]:
    horse_id = event["horse_id"]
    is_subagent = bool(event.get("agent_id"))
    return {
        "horse_id": horse_id,
        "session_id": event.get("session_id"),
        "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "parent_id": event.get("session_id") if is_subagent else None,
        "kind": "subagent" if is_subagent else "session",
        "activity_label": event.get("activity_label", ""),
        "permission_mode": event.get("permission_mode"),
        "cwd": event.get("cwd"),
        "started_ts": event.get("ts"),
        "last_update_ts": event.get("ts"),
        "event_count": 0,
        "status": "running",
    }


def build_snapshot(now: float | None = None, path: Path | None = None) -> dict[str, Any]:
    """Fold events.jsonl into {horses: {running now}, history: [finished,
    most recent first]}. Pure function of the event log plus wall-clock
    time (for idle-timeout detection), so any reader gets the same view."""
    now = time.time() if now is None else now
    horses: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []

    for event in iter_events(path):
        horse_id = event.get("horse_id")
        if not horse_id:
            continue
        horse = horses.get(horse_id)
        if horse is None:
            horse = _new_horse(event)
            horses[horse_id] = horse
        horse["activity_label"] = event.get("activity_label") or horse["activity_label"]
        horse["last_update_ts"] = event.get("ts", horse["last_update_ts"])
        horse["event_count"] += 1
        if event.get("permission_mode"):
            horse["permission_mode"] = event["permission_mode"]

        if event.get("hook_event_name") in FINISHING_EVENTS:
            horse["status"] = "finished"
            horse["finished_ts"] = event.get("ts")
            history.append(horse)
            del horses[horse_id]

    # Idle horses that never got a proper finishing event (crash, kill -9,
    # a hook that failed to fire) are demoted to history so the dashboard
    # doesn't show a phantom horse running forever.
    stalled_ids = []
    for horse_id, horse in horses.items():
        last_ts = _parse_ts(horse["last_update_ts"])
        if last_ts is not None and (now - last_ts) > IDLE_TIMEOUT_SECONDS:
            horse["status"] = "stalled"
            horse["finished_ts"] = horse["last_update_ts"]
            history.append(horse)
            stalled_ids.append(horse_id)
    for horse_id in stalled_ids:
        del horses[horse_id]

    history.sort(key=lambda h: h.get("finished_ts") or "", reverse=True)
    return {
        "horses": list(horses.values()),
        "history": history[:HISTORY_LIMIT],
        "generated_ts": _iso(now),
    }


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        import datetime

        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    return _iso(time.time())
