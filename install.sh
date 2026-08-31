#!/usr/bin/env bash
# Installs automation-racehorse into Claude Code for the current user:
#   1. registers the MCP server at user scope (all projects, all threads)
#   2. wires activity-capture hooks into ~/.claude/settings.json (idempotent)
# Re-running is safe. Nothing leaves your machine.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$REPO_DIR/racehorse"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi
chmod +x "$BIN"

echo "==> Registering MCP server (user scope)"
if command -v claude >/dev/null 2>&1; then
  claude mcp remove racehorse --scope user >/dev/null 2>&1 || true
  claude mcp add-json racehorse \
    "{\"type\":\"stdio\",\"command\":\"$BIN\",\"args\":[\"mcp\"]}" \
    --scope user
else
  python3 - "$CLAUDE_DIR" "$BIN" <<'PYEOF'
import json, sys
from pathlib import Path
claude_dir, bin_path = Path(sys.argv[1]), sys.argv[2]
claude_dir.mkdir(parents=True, exist_ok=True)
mcp_file = claude_dir / ".mcp.json"
cfg = {}
if mcp_file.exists():
    try:
        cfg = json.loads(mcp_file.read_text())
    except ValueError:
        cfg = {}
cfg.setdefault("mcpServers", {})["racehorse"] = {
    "type": "stdio", "command": bin_path, "args": ["mcp"],
}
mcp_file.write_text(json.dumps(cfg, indent=2) + "\n")
print(f"  wrote {mcp_file} (claude CLI not found; verify your client reads this path)")
PYEOF
fi

echo "==> Wiring hooks into $SETTINGS"
python3 - "$SETTINGS" "$BIN" <<'PYEOF'
import json, sys, time
from pathlib import Path

settings_path, bin_path = Path(sys.argv[1]), sys.argv[2]
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except ValueError:
        backup = settings_path.with_suffix(f".json.invalid.{int(time.time())}")
        settings_path.rename(backup)
        print(f"  warning: existing settings were invalid JSON; moved to {backup}")
        settings = {}
    else:
        backup = settings_path.with_suffix(f".json.bak.{int(time.time())}")
        backup.write_text(json.dumps(settings, indent=2) + "\n")
        print(f"  backup: {backup}")

# Every hook event Claude Code fires, so the dashboard sees the whole race:
# no matcher filtering — `racehorse hook` reads hook_event_name from the
# payload itself, so there's no per-event subcommand to pass either.
WIRING = [
    ("SessionStart", None),
    ("SessionEnd", None),
    ("UserPromptSubmit", None),
    ("PreToolUse", None),
    ("PostToolUse", None),
    ("PostToolUseFailure", None),
    ("SubagentStart", None),
    ("SubagentStop", None),
    ("TaskCreated", None),
    ("TaskCompleted", None),
    ("Stop", None),
    ("PreCompact", None),
    ("PostCompact", None),
]

hooks = settings.setdefault("hooks", {})
for event, matcher in WIRING:
    cmd = f"'{bin_path}' hook"
    entries = hooks.setdefault(event, [])
    already = any(
        bin_path in h.get("command", "")
        for e in entries if isinstance(e, dict)
        for h in e.get("hooks", []) if isinstance(h, dict)
    )
    if already:
        print(f"  {event}: already wired, skipping")
        continue
    entry = {"hooks": [{"type": "command", "command": cmd, "timeout": 15}]}
    if matcher:
        entry["matcher"] = matcher
    entries.append(entry)
    print(f"  {event}: wired")

settings_path.write_text(json.dumps(settings, indent=2) + "\n")
PYEOF

cat <<EOF

Done. New Claude Code sessions will now:
  - fire every hook event into '$BIN hook', which appends a normalized
    line to events.jsonl (SessionStart/End, UserPromptSubmit, Pre/PostToolUse,
    PostToolUseFailure, SubagentStart/Stop, TaskCreated/Completed, Stop,
    Pre/PostCompact)
  - expose get_active_agents / race_history / race_stats to any Claude Code
    session via the racehorse MCP server (registered at user scope)

Run the live dashboard any time with:
    $BIN serve
It opens at http://127.0.0.1:8790 and shows every skill, subagent, and tool
call as a horse racing across the board in real time.

Data lives in ~/.local/share/automation-racehorse/events.jsonl (override the
location with \$RACEHORSE_DIR). install.sh never deletes this data.
EOF
