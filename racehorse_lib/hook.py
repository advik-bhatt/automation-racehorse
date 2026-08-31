"""Normalizes a Claude Code hook payload (JSON on stdin) into one race event.

Every hook event Claude Code fires shares a common envelope
(session_id, agent_id, agent_type, cwd, permission_mode, hook_event_name,
transcript_path). Tool-related events additionally carry tool_name and
tool_input. This module turns that raw payload into a short, human-legible
"activity_label" plus a stable horse_id, and appends it to the event log.

We deliberately never read transcript_path / parse transcript files:
Claude Code's own docs mark that JSONL format as internal and unstable
across releases. Hooks are the documented, stable capture surface.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from . import store


def _first(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return default


def derive_activity_label(payload: dict[str, Any]) -> str:
    event_name = payload.get("hook_event_name", "")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if event_name == "SessionStart":
        return "session started"
    if event_name == "SessionEnd":
        return "session ended"
    if event_name == "UserPromptSubmit":
        return "prompt submitted"
    if event_name == "Stop":
        return "turn finished"
    if event_name in ("PreCompact", "PostCompact"):
        return "compacting context"
    if event_name == "SubagentStart":
        return f"subagent started: {payload.get('agent_type') or 'agent'}"
    if event_name == "SubagentStop":
        return f"subagent finished: {payload.get('agent_type') or 'agent'}"
    if event_name == "TaskCreated":
        return "task created"
    if event_name == "TaskCompleted":
        return "task completed"

    if event_name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        verb = {
            "PreToolUse": "running",
            "PostToolUse": "finished",
            "PostToolUseFailure": "failed",
        }[event_name]
        if tool_name == "Skill":
            skill = _first(tool_input, "skill", "command", "name", default="?")
            return f"{verb} skill: {skill}"
        if tool_name in ("Task", "Agent"):
            who = _first(tool_input, "subagent_type", "description", default="subagent")
            return f"{verb} subagent: {who}"
        if tool_name == "Workflow":
            wf = _first(tool_input, "name", "scriptPath", default="workflow")
            return f"{verb} workflow: {wf}"
        if tool_name:
            return f"{verb} tool: {tool_name}"
        return f"{verb} tool"

    return event_name.lower() or "activity"


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    agent_id = payload.get("agent_id")
    horse_id = agent_id or session_id or "unknown"

    return {
        "ts": store.now_iso(),
        "hook_event_name": payload.get("hook_event_name"),
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": payload.get("agent_type"),
        "horse_id": horse_id,
        "permission_mode": payload.get("permission_mode"),
        "cwd": payload.get("cwd"),
        "tool_name": payload.get("tool_name"),
        "activity_label": derive_activity_label(payload),
    }


def handle_stdin() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    store.append_event(normalize(payload))
