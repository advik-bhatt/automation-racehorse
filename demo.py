#!/usr/bin/env python3
"""Synthetic race feed, for trying the dashboard without Claude Code.

Appends realistic hook events (sessions, subagents, tool calls, skills)
to a demo event log on a human-watchable rhythm, so `racehorse serve`
has something to show in a room where nobody has hooks wired up yet.

Usage:
    python3 demo.py            # three simulated races, then exit
    python3 demo.py --forever  # keep racing until Ctrl-C

By default events go to ~/.local/share/automation-racehorse-demo (NOT the
real log), and the matching serve command is printed on start. Set
RACEHORSE_DIR yourself to point both this script and `racehorse serve`
somewhere else.
"""
from __future__ import annotations

import os
import random
import sys
import time
import uuid
from pathlib import Path

DEMO_DIR = Path.home() / ".local" / "share" / "automation-racehorse-demo"
if not os.environ.get("RACEHORSE_DIR"):
    os.environ["RACEHORSE_DIR"] = str(DEMO_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from racehorse_lib import hook, store  # noqa: E402

TOOLS = ["Read", "Grep", "Bash", "Edit", "Write", "Glob"]
SKILLS = ["code-review", "pdf", "brand-guidelines", "dataviz"]
AGENT_TYPES = ["Explore", "general-purpose", "Plan", "code-reviewer"]
PERMISSION_MODES = ["default", "acceptEdits", "auto"]


def emit(payload: dict) -> None:
    store.append_event(hook.normalize(payload))


def pause(lo: float = 0.3, hi: float = 1.2) -> None:
    time.sleep(random.uniform(lo, hi))


def tool_burst(base: dict, n: int) -> None:
    for _ in range(n):
        tool = random.choice(TOOLS)
        tool_input: dict = {}
        if tool == "Skill" or random.random() < 0.2:
            tool, tool_input = "Skill", {"skill": random.choice(SKILLS)}
        emit({**base, "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})
        pause(0.2, 0.7)
        done = "PostToolUseFailure" if random.random() < 0.08 else "PostToolUse"
        emit({**base, "hook_event_name": done, "tool_name": tool, "tool_input": tool_input})
        pause()


def run_subagent(session: dict) -> None:
    agent = {
        **session,
        "agent_id": f"demo-agent-{uuid.uuid4().hex[:8]}",
        "agent_type": random.choice(AGENT_TYPES),
    }
    emit({**agent, "hook_event_name": "SubagentStart"})
    tool_burst(agent, random.randint(2, 5))
    emit({**agent, "hook_event_name": "SubagentStop"})


def run_race(index: int) -> None:
    session = {
        "session_id": f"demo-session-{uuid.uuid4().hex[:8]}",
        "permission_mode": random.choice(PERMISSION_MODES),
        "cwd": "/home/demo/project",
    }
    print(f"race {index}: session {session['session_id']}", flush=True)
    emit({**session, "hook_event_name": "SessionStart"})
    pause()
    emit({**session, "hook_event_name": "UserPromptSubmit"})
    tool_burst(session, random.randint(2, 4))
    for _ in range(random.randint(1, 3)):
        run_subagent(session)
    tool_burst(session, random.randint(1, 3))
    emit({**session, "hook_event_name": "Stop"})
    pause()
    emit({**session, "hook_event_name": "SessionEnd"})


def main() -> int:
    forever = "--forever" in sys.argv[1:]
    log = store.events_path()
    print(f"demo feed -> {log}", flush=True)
    print("watch it live in another terminal with:", flush=True)
    print(f"    RACEHORSE_DIR='{store.racehorse_dir()}' ./racehorse serve", flush=True)
    index = 1
    try:
        while forever or index <= 3:
            run_race(index)
            index += 1
            pause(0.5, 1.5)
    except KeyboardInterrupt:
        print("\ndemo feed stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
