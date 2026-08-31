#!/usr/bin/env bash
# Removes automation-racehorse hooks and MCP registration. Event history is
# kept; delete ~/.local/share/automation-racehorse (or $RACEHORSE_DIR)
# yourself if you want the data gone too.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$REPO_DIR/racehorse"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

if command -v claude >/dev/null 2>&1; then
  claude mcp remove racehorse --scope user >/dev/null 2>&1 || true
fi

python3 - "$CLAUDE_DIR" "$BIN" <<'PYEOF'
import json, sys, time
from pathlib import Path

claude_dir, bin_path = Path(sys.argv[1]), sys.argv[2]

mcp_file = claude_dir / ".mcp.json"
if mcp_file.exists():
    try:
        cfg = json.loads(mcp_file.read_text())
        if cfg.get("mcpServers", {}).pop("racehorse", None) is not None:
            mcp_file.write_text(json.dumps(cfg, indent=2) + "\n")
            print("removed MCP registration from .mcp.json")
    except ValueError:
        pass

settings_path = claude_dir / "settings.json"
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except ValueError:
        sys.exit(0)

    backup = settings_path.with_suffix(f".json.bak.{int(time.time())}")
    backup.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"backup: {backup}")

    hooks = settings.get("hooks", {})
    removed = 0
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            cmds = [h.get("command", "") for h in entry.get("hooks", []) if isinstance(h, dict)]
            if any(bin_path in c for c in cmds):
                removed += 1
            else:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"removed {removed} hook entries from settings.json")
PYEOF

cat <<EOF

Done. Hooks and MCP registration removed.

Event history is preserved at ~/.local/share/automation-racehorse
(or \$RACEHORSE_DIR if you set it). To remove it too:
    rm -rf ~/.local/share/automation-racehorse
EOF
