"""Unit tests for racehorse_lib.hook — normalize() and derive_activity_label()
as pure functions. No stdin, no file IO: every test builds a payload dict by
hand and inspects the return value directly.
"""
from __future__ import annotations

import datetime
import unittest

from racehorse_lib import hook


class DeriveActivityLabelSkillTests(unittest.TestCase):
    def test_pretooluse_skill_uses_skill_field(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "pptx"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running skill: pptx")

    def test_pretooluse_skill_falls_back_to_command(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"command": "brand-guidelines"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running skill: brand-guidelines")

    def test_pretooluse_skill_falls_back_to_name(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"name": "xlsx"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running skill: xlsx")

    def test_pretooluse_skill_missing_input_uses_placeholder(self) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {}}
        self.assertEqual(hook.derive_activity_label(payload), "running skill: ?")


class DeriveActivityLabelSubagentTests(unittest.TestCase):
    def test_pretooluse_task_uses_subagent_type(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running subagent: Explore")

    def test_pretooluse_agent_tool_name_also_treated_as_subagent(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {"description": "researching prices"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running subagent: researching prices")

    def test_pretooluse_task_missing_input_uses_placeholder(self) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Task", "tool_input": {}}
        self.assertEqual(hook.derive_activity_label(payload), "running subagent: subagent")


class DeriveActivityLabelWorkflowTests(unittest.TestCase):
    def test_pretooluse_workflow_uses_name(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Workflow",
            "tool_input": {"name": "nightly-sync"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running workflow: nightly-sync")

    def test_pretooluse_workflow_falls_back_to_scriptpath(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Workflow",
            "tool_input": {"scriptPath": "/scripts/deploy.py"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "running workflow: /scripts/deploy.py")


class DeriveActivityLabelGenericToolTests(unittest.TestCase):
    def test_pretooluse_generic_tool(self) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.assertEqual(hook.derive_activity_label(payload), "running tool: Bash")

    def test_pretooluse_missing_tool_name(self) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_input": {}}
        self.assertEqual(hook.derive_activity_label(payload), "running tool")

    def test_posttooluse_uses_finished_verb(self) -> None:
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}}
        self.assertEqual(hook.derive_activity_label(payload), "finished tool: Bash")

    def test_posttoolusefailure_uses_failed_verb(self) -> None:
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {}}
        self.assertEqual(hook.derive_activity_label(payload), "failed tool: Bash")

    def test_posttoolusefailure_skill_variant(self) -> None:
        payload = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Skill",
            "tool_input": {"skill": "pdf"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "failed skill: pdf")

    def test_posttooluse_workflow_variant(self) -> None:
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Workflow",
            "tool_input": {"name": "deploy"},
        }
        self.assertEqual(hook.derive_activity_label(payload), "finished workflow: deploy")


class DeriveActivityLabelLifecycleTests(unittest.TestCase):
    def test_subagent_start_includes_agent_type(self) -> None:
        payload = {"hook_event_name": "SubagentStart", "agent_type": "Explore"}
        self.assertEqual(hook.derive_activity_label(payload), "subagent started: Explore")

    def test_subagent_start_missing_agent_type_uses_placeholder(self) -> None:
        payload = {"hook_event_name": "SubagentStart"}
        self.assertEqual(hook.derive_activity_label(payload), "subagent started: agent")

    def test_subagent_stop_includes_agent_type(self) -> None:
        payload = {"hook_event_name": "SubagentStop", "agent_type": "Explore"}
        self.assertEqual(hook.derive_activity_label(payload), "subagent finished: Explore")

    def test_session_start(self) -> None:
        payload = {"hook_event_name": "SessionStart"}
        self.assertEqual(hook.derive_activity_label(payload), "session started")

    def test_session_end(self) -> None:
        payload = {"hook_event_name": "SessionEnd"}
        self.assertEqual(hook.derive_activity_label(payload), "session ended")

    def test_stop(self) -> None:
        payload = {"hook_event_name": "Stop"}
        self.assertEqual(hook.derive_activity_label(payload), "turn finished")

    def test_user_prompt_submit(self) -> None:
        payload = {"hook_event_name": "UserPromptSubmit"}
        self.assertEqual(hook.derive_activity_label(payload), "prompt submitted")

    def test_pre_compact(self) -> None:
        payload = {"hook_event_name": "PreCompact"}
        self.assertEqual(hook.derive_activity_label(payload), "compacting context")

    def test_post_compact(self) -> None:
        payload = {"hook_event_name": "PostCompact"}
        self.assertEqual(hook.derive_activity_label(payload), "compacting context")

    def test_task_created(self) -> None:
        payload = {"hook_event_name": "TaskCreated"}
        self.assertEqual(hook.derive_activity_label(payload), "task created")

    def test_task_completed(self) -> None:
        payload = {"hook_event_name": "TaskCompleted"}
        self.assertEqual(hook.derive_activity_label(payload), "task completed")

    def test_unknown_event_name_is_lowercased(self) -> None:
        payload = {"hook_event_name": "SomethingNew"}
        self.assertEqual(hook.derive_activity_label(payload), "somethingnew")

    def test_missing_event_name_falls_back_to_activity(self) -> None:
        self.assertEqual(hook.derive_activity_label({}), "activity")


class NormalizeTests(unittest.TestCase):
    def test_horse_id_uses_agent_id_when_present(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "agent_id": "agent-9",
            "agent_type": "Explore",
            "tool_name": "Bash",
        }
        event = hook.normalize(payload)
        self.assertEqual(event["horse_id"], "agent-9")
        self.assertEqual(event["session_id"], "session-1")
        self.assertEqual(event["agent_id"], "agent-9")
        self.assertEqual(event["agent_type"], "Explore")

    def test_horse_id_falls_back_to_session_id_when_no_agent_id(self) -> None:
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
        }
        event = hook.normalize(payload)
        self.assertEqual(event["horse_id"], "session-1")
        self.assertIsNone(event["agent_id"])

    def test_horse_id_falls_back_to_unknown_when_neither_present(self) -> None:
        event = hook.normalize({"hook_event_name": "SessionStart"})
        self.assertEqual(event["horse_id"], "unknown")

    def test_normalize_carries_through_common_envelope_fields(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "agent_id": None,
            "agent_type": None,
            "permission_mode": "auto",
            "cwd": "/home/user/project",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        event = hook.normalize(payload)
        self.assertEqual(event["hook_event_name"], "PreToolUse")
        self.assertEqual(event["permission_mode"], "auto")
        self.assertEqual(event["cwd"], "/home/user/project")
        self.assertEqual(event["tool_name"], "Bash")
        self.assertEqual(event["activity_label"], "running tool: Bash")

    def test_normalize_sets_a_valid_iso_timestamp(self) -> None:
        event = hook.normalize({"hook_event_name": "SessionStart", "session_id": "s1"})
        # Should parse cleanly as an ISO-8601 UTC timestamp ending in "Z".
        self.assertTrue(event["ts"].endswith("Z"))
        datetime.datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))

    def test_normalize_derives_skill_label_end_to_end(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_name": "Skill",
            "tool_input": {"skill": "pptx"},
        }
        event = hook.normalize(payload)
        self.assertIn("pptx", event["activity_label"])
        self.assertEqual(event["activity_label"], "running skill: pptx")

    def test_normalize_derives_subagent_label_end_to_end(self) -> None:
        payload = {
            "hook_event_name": "SubagentStart",
            "session_id": "session-1",
            "agent_id": "agent-2",
            "agent_type": "Explore",
        }
        event = hook.normalize(payload)
        self.assertEqual(event["activity_label"], "subagent started: Explore")
        self.assertEqual(event["horse_id"], "agent-2")


if __name__ == "__main__":
    unittest.main()
