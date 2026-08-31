"""Unit tests for racehorse_lib.store.build_snapshot() (and, incidentally,
iter_events()). Each test writes a hand-built events.jsonl to a temp file
and passes it via the `path=` override so real $RACEHORSE_DIR / wall-clock
state is never touched. `now=` is always passed explicitly to build_snapshot()
so idle-timeout behavior is deterministic, not dependent on real sleeps.
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path

from racehorse_lib import store

BASE = datetime.datetime(2026, 7, 18, 22, 0, 0, tzinfo=datetime.timezone.utc)


def iso(offset_seconds: float = 0.0) -> str:
    dt = BASE + datetime.timedelta(seconds=offset_seconds)
    return dt.isoformat().replace("+00:00", "Z")


def epoch(offset_seconds: float = 0.0) -> float:
    return (BASE + datetime.timedelta(seconds=offset_seconds)).timestamp()


def make_event(
    *,
    ts_offset: float = 0.0,
    hook_event_name: str = "PreToolUse",
    session_id: str = "session-1",
    agent_id: str | None = None,
    agent_type: str | None = None,
    permission_mode: str | None = None,
    cwd: str | None = "/home/user/project",
    tool_name: str | None = "Bash",
    activity_label: str = "running tool: Bash",
) -> dict:
    horse_id = agent_id or session_id
    return {
        "ts": iso(ts_offset),
        "hook_event_name": hook_event_name,
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "horse_id": horse_id,
        "permission_mode": permission_mode,
        "cwd": cwd,
        "tool_name": tool_name,
        "activity_label": activity_label,
    }


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, name = tempfile.mkstemp(suffix=".jsonl", prefix="racehorse-test-")
        os.close(fd)
        self.tmp_path = Path(name)
        self.addCleanup(self.tmp_path.unlink, missing_ok=True)

    def write_events(self, *events: dict) -> None:
        with open(self.tmp_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

    def horses_by_id(self, snapshot: dict) -> dict:
        return {h["horse_id"]: h for h in snapshot["horses"]}

    def history_by_id(self, snapshot: dict) -> dict:
        return {h["horse_id"]: h for h in snapshot["history"]}


class RunningHorseTests(StoreTestCase):
    def test_single_start_event_appears_running(self) -> None:
        self.write_events(
            make_event(hook_event_name="SessionStart", session_id="s1", tool_name=None,
                       activity_label="session started")
        )
        snapshot = store.build_snapshot(now=epoch(1), path=self.tmp_path)
        self.assertEqual(len(snapshot["horses"]), 1)
        self.assertEqual(len(snapshot["history"]), 0)
        horse = snapshot["horses"][0]
        self.assertEqual(horse["horse_id"], "s1")
        self.assertEqual(horse["status"], "running")
        self.assertEqual(horse["kind"], "session")
        self.assertIsNone(horse["parent_id"])

    def test_subagent_horse_has_session_as_parent(self) -> None:
        self.write_events(
            make_event(
                hook_event_name="SubagentStart",
                session_id="s1",
                agent_id="a1",
                agent_type="Explore",
                tool_name=None,
                activity_label="subagent started: Explore",
            )
        )
        snapshot = store.build_snapshot(now=epoch(1), path=self.tmp_path)
        horses = self.horses_by_id(snapshot)
        self.assertIn("a1", horses)
        horse = horses["a1"]
        self.assertEqual(horse["kind"], "subagent")
        self.assertEqual(horse["parent_id"], "s1")
        self.assertEqual(horse["agent_type"], "Explore")

    def test_multiple_events_update_activity_label_and_event_count(self) -> None:
        self.write_events(
            make_event(ts_offset=0, session_id="s1", tool_name="Bash",
                       activity_label="running tool: Bash"),
            make_event(ts_offset=1, session_id="s1", tool_name="Skill",
                       hook_event_name="PostToolUse", activity_label="finished skill: pptx"),
        )
        snapshot = store.build_snapshot(now=epoch(2), path=self.tmp_path)
        horse = self.horses_by_id(snapshot)["s1"]
        self.assertEqual(horse["event_count"], 2)
        self.assertEqual(horse["activity_label"], "finished skill: pptx")
        self.assertEqual(horse["last_update_ts"], iso(1))
        self.assertEqual(horse["started_ts"], iso(0))

    def test_permission_mode_updates_to_latest_non_empty_value(self) -> None:
        self.write_events(
            make_event(ts_offset=0, session_id="s1", permission_mode="default"),
            make_event(ts_offset=1, session_id="s1", permission_mode="auto"),
            make_event(ts_offset=2, session_id="s1", permission_mode=None),
        )
        snapshot = store.build_snapshot(now=epoch(3), path=self.tmp_path)
        horse = self.horses_by_id(snapshot)["s1"]
        # A later event with no permission_mode must not blank out "auto".
        self.assertEqual(horse["permission_mode"], "auto")

    def test_event_with_missing_horse_id_is_skipped(self) -> None:
        bad = make_event(session_id="s1")
        bad["horse_id"] = ""
        self.write_events(bad)
        snapshot = store.build_snapshot(now=epoch(1), path=self.tmp_path)
        self.assertEqual(snapshot["horses"], [])
        self.assertEqual(snapshot["history"], [])


class FinishingEventTests(StoreTestCase):
    def test_subagent_stop_moves_horse_to_history_as_finished(self) -> None:
        self.write_events(
            make_event(
                ts_offset=0,
                hook_event_name="SubagentStart",
                session_id="s1",
                agent_id="a1",
                agent_type="Explore",
                tool_name=None,
                activity_label="subagent started: Explore",
            ),
            make_event(
                ts_offset=5,
                hook_event_name="SubagentStop",
                session_id="s1",
                agent_id="a1",
                agent_type="Explore",
                tool_name=None,
                activity_label="subagent finished: Explore",
            ),
        )
        snapshot = store.build_snapshot(now=epoch(6), path=self.tmp_path)
        self.assertEqual(snapshot["horses"], [])
        self.assertEqual(len(snapshot["history"]), 1)
        horse = snapshot["history"][0]
        self.assertEqual(horse["horse_id"], "a1")
        self.assertEqual(horse["status"], "finished")
        self.assertEqual(horse["finished_ts"], iso(5))

    def test_session_end_moves_session_horse_to_history_as_finished(self) -> None:
        self.write_events(
            make_event(ts_offset=0, hook_event_name="SessionStart", session_id="s1",
                       tool_name=None, activity_label="session started"),
            make_event(ts_offset=10, hook_event_name="SessionEnd", session_id="s1",
                       tool_name=None, activity_label="session ended"),
        )
        snapshot = store.build_snapshot(now=epoch(11), path=self.tmp_path)
        self.assertEqual(snapshot["horses"], [])
        history = self.history_by_id(snapshot)
        self.assertIn("s1", history)
        self.assertEqual(history["s1"]["status"], "finished")

    def test_finished_history_is_sorted_most_recent_first(self) -> None:
        self.write_events(
            make_event(ts_offset=0, hook_event_name="SessionEnd", session_id="early",
                       tool_name=None, activity_label="session ended"),
            make_event(ts_offset=100, hook_event_name="SessionEnd", session_id="late",
                       tool_name=None, activity_label="session ended"),
        )
        snapshot = store.build_snapshot(now=epoch(200), path=self.tmp_path)
        ids_in_order = [h["horse_id"] for h in snapshot["history"]]
        self.assertEqual(ids_in_order, ["late", "early"])


class StalledHorseTests(StoreTestCase):
    def test_running_horse_beyond_idle_timeout_is_demoted_to_stalled(self) -> None:
        self.write_events(
            make_event(ts_offset=0, session_id="s1", activity_label="running tool: Bash"),
        )
        past_timeout = epoch(store.IDLE_TIMEOUT_SECONDS + 1)
        snapshot = store.build_snapshot(now=past_timeout, path=self.tmp_path)
        self.assertEqual(snapshot["horses"], [])
        history = self.history_by_id(snapshot)
        self.assertIn("s1", history)
        horse = history["s1"]
        self.assertEqual(horse["status"], "stalled")
        # A stalled horse's finished_ts is backfilled from its last update,
        # not from `now` (there was no real finishing event).
        self.assertEqual(horse["finished_ts"], iso(0))

    def test_running_horse_within_idle_timeout_stays_running(self) -> None:
        self.write_events(
            make_event(ts_offset=0, session_id="s1", activity_label="running tool: Bash"),
        )
        just_under_timeout = epoch(store.IDLE_TIMEOUT_SECONDS - 1)
        snapshot = store.build_snapshot(now=just_under_timeout, path=self.tmp_path)
        self.assertEqual(len(snapshot["horses"]), 1)
        self.assertEqual(snapshot["horses"][0]["status"], "running")
        self.assertEqual(snapshot["history"], [])

    def test_already_finished_horse_is_not_reevaluated_for_stall(self) -> None:
        # A horse that finished properly must stay "finished", never flip to
        # "stalled" just because `now` is far past its finished_ts.
        self.write_events(
            make_event(ts_offset=0, hook_event_name="SessionEnd", session_id="s1",
                       tool_name=None, activity_label="session ended"),
        )
        far_future = epoch(store.IDLE_TIMEOUT_SECONDS * 100)
        snapshot = store.build_snapshot(now=far_future, path=self.tmp_path)
        history = self.history_by_id(snapshot)
        self.assertEqual(history["s1"]["status"], "finished")


class EmptyAndMissingLogTests(StoreTestCase):
    def test_empty_file_produces_empty_snapshot(self) -> None:
        self.write_events()
        snapshot = store.build_snapshot(now=epoch(0), path=self.tmp_path)
        self.assertEqual(snapshot["horses"], [])
        self.assertEqual(snapshot["history"], [])
        self.assertIn("generated_ts", snapshot)

    def test_nonexistent_path_produces_empty_snapshot(self) -> None:
        missing = self.tmp_path.parent / "does-not-exist.jsonl"
        snapshot = store.build_snapshot(now=epoch(0), path=missing)
        self.assertEqual(snapshot["horses"], [])
        self.assertEqual(snapshot["history"], [])

    def test_blank_and_malformed_lines_are_skipped(self) -> None:
        with open(self.tmp_path, "w", encoding="utf-8") as f:
            f.write("\n")
            f.write("not json at all\n")
            f.write(json.dumps(make_event(session_id="s1")) + "\n")
        events = list(store.iter_events(path=self.tmp_path))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session_id"], "s1")


if __name__ == "__main__":
    unittest.main()
