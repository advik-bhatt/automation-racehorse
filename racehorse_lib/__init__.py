"""automation-racehorse: a live activity dashboard for Claude Code.

Captures Claude Code hook events (skills, subagents, workflows, tool calls,
auto mode) into an append-only JSONL log, derives a live "race" snapshot
from it, and serves that snapshot to a browser dashboard (SSE) and to
other Claude sessions (MCP).
"""
