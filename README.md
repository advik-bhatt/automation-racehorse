# automation-racehorse

A live dashboard of what Claude Code is doing right now, rendered as a horse
race. Every skill invocation, subagent, workflow run, and tool call Claude
Code fires becomes a horse tearing across the track — start a skill, watch it
gallop; finish a subagent, watch it cross the line. It's captured entirely
through Claude Code's hooks, kept as a plain append-only log on your own
disk, and served by a zero-dependency stdlib HTTP server. Nothing leaves
your machine, and nothing outside the Python standard library is required.

## How it works

```
 Claude Code hooks                racehorse hook
 (PreToolUse, PostToolUse,   -->  normalizes payload   -->  events.jsonl
  SubagentStart/Stop,              into one JSON line        (append-only,
  SessionStart/End, Stop, …)       + horse_id + label         local disk)
                                                                    |
                                                                    | tailed
                                                                    v
                                                          racehorse serve
                                                        (stdlib http.server)
                                                          GET /api/state  (JSON snapshot)
                                                          GET /api/events (SSE stream)
                                                          GET /, /app.js, /style.css
                                                                    |
                                                                    v
                                                          browser dashboard
                                                         (the race track)

 racehorse mcp  -->  MCP tools (get_active_agents, race_history, race_stats)
                      queryable from any other Claude Code session
```

`events.jsonl` is the single source of truth. There is no separate state
file — every reader (the dashboard server, `racehorse mcp`, `racehorse
status`) rebuilds the current race snapshot by folding over the event log.
That keeps concurrent writers (parallel subagents firing hooks at once)
simple — pure appends, no read-modify-write races — and keeps the on-disk
format trivially inspectable (`tail -f events.jsonl`) and rebuildable from
scratch.

A horse is either a **session** (a top-level Claude Code conversation) or a
**subagent** (spawned by one), identified by `horse_id` — the agent's
`agent_id` when it's a subagent, otherwise the session's `session_id`. A
horse's race ends when its finishing hook fires (`SubagentStop` /
`SessionEnd`), or gets called on account of a timeout — see limitations
below.

## Install

```sh
git clone https://github.com/advik-bhatt/automation-racehorse-public.git
cd automation-racehorse-public
./install.sh
```

`install.sh` is idempotent — safe to re-run any time — and does two things:

1. **Registers the MCP server at user scope**, so `get_active_agents`,
   `race_history`, and `race_stats` are available to every Claude Code
   session on the machine, not just the one you installed from.
2. **Wires every relevant hook event into `~/.claude/settings.json`**
   (`SessionStart`/`SessionEnd`, `UserPromptSubmit`, `PreToolUse`/
   `PostToolUse`/`PostToolUseFailure`, `SubagentStart`/`SubagentStop`,
   `TaskCreated`/`TaskCompleted`, `Stop`, `PreCompact`/`PostCompact`) so each
   one runs `racehorse hook`, appending one normalized line to
   `events.jsonl`. Existing settings are backed up before being rewritten,
   and already-wired hooks are left alone on re-run.

By default event data lives at `~/.local/share/automation-racehorse/events.jsonl`.
Set `RACEHORSE_DIR` to store it elsewhere — every command (`hook`, `serve`,
`mcp`, `status`) honors it. `install.sh` never deletes existing data.

Run `./uninstall.sh` to remove the hook wiring and MCP registration again.

## Try it without Claude Code

No hooks wired yet, or no Claude Code on this machine? Run the synthetic
race feed and watch the dashboard move:

```sh
python3 demo.py            # three simulated races into a separate demo log
python3 demo.py --forever  # keep racing until Ctrl-C
```

It prints the matching `racehorse serve` command to run in a second
terminal. When `RACEHORSE_DIR` is unset, the demo defaults to its own log
(`~/.local/share/automation-racehorse-demo`) so it stays out of your real
one; if you've exported `RACEHORSE_DIR`, both the demo and `serve` honor it.

## CLI reference

| Command | What it does |
|---|---|
| `racehorse hook` | Reads one Claude Code hook payload from stdin, normalizes it, appends it to `events.jsonl`. Internal — this is what the hooks `install.sh` wires actually invoke; you shouldn't need to run it by hand. |
| `racehorse serve [--port 8790] [--no-open] [--bind 127.0.0.1]` | Starts the dashboard: HTTP + SSE server at `http://<bind>:<port>` (default `http://127.0.0.1:8790`). Opens your browser automatically unless `--no-open` is passed. |
| `racehorse mcp` | Starts the MCP stdio server (JSON-RPC 2.0 over stdin/stdout, one message per line). This is what `install.sh` registers; other Claude Code sessions talk to it, you don't run it directly. |
| `racehorse status` | Prints the current race snapshot to the terminal — running horses and the last few history entries — for a quick check without opening the dashboard. |

## MCP tools

Exposed by `racehorse mcp`, read-only, backed by the same `events.jsonl`:

| Tool | Returns |
|---|---|
| `get_active_agents` | Every horse currently running, each annotated with `duration_seconds` (from `started_ts` to now). |
| `race_history` (`limit`, default 20) | The most recently finished or stalled horses, most recent first, each annotated with `duration_seconds` (`started_ts` to `finished_ts`). |
| `race_stats` | Aggregate tallies: most-common skills (counted over the full event log), plus history-scoped stats — most-common subagent types, average subagent duration in seconds, and how many history entries ran under `auto` permission mode. |

## Scope and honest limitations

- **Subagent nesting is flat.** A subagent's `parent_id` is the session that
  spawned it, one level deep. An agent spawning its own sub-subagent isn't
  tracked as a tree in this version — it shows up as another horse under the
  same top-level session, not nested under its immediate parent.
- **The idle-timeout heuristic is a blunt instrument.** A running horse with
  no new hook events for `IDLE_TIMEOUT_SECONDS` (currently 180s) gets
  demoted to history as `"stalled"`. That's what keeps a crashed or `kill
  -9`'d session from showing as "running" forever on the dashboard — but it
  also means a horse can occasionally get marked stalled while a genuinely
  slow tool call is just still working. Treat `"stalled"` as "no news," not
  a certain failure.
- **No auth, loopback-only by default.** `racehorse serve` binds to
  `127.0.0.1` and serves `/api/state` and `/api/events` with no
  authentication at all. That's fine on your own machine. Don't rebind
  `--bind` to `0.0.0.0` on a shared or multi-user box without putting your
  own access control in front of it — anyone who can reach the port can
  read everything in the log.
- **Label parsing depends on tool_input field names.** `activity_label`
  derivation looks for specific keys (`skill`, `subagent_type`, `name`, …)
  inside `tool_input` for `Skill`/`Task`/`Workflow` tool calls. If a future
  Claude Code version renames or restructures those fields, labels for
  those tool types will silently fall back to a generic placeholder rather
  than error — worth knowing if a dashboard label reads "?" more than
  expected.

## Development

```sh
python3 tests/run_tests.py
```

Runs the full stdlib `unittest` suite (`tests/test_hook.py`,
`tests/test_store.py`) and exits non-zero on any failure, so it composes
with CI or a pre-commit hook. No pip install step — the whole project,
tests included, uses only the Python standard library.

## License

MIT — see [LICENSE](LICENSE).
