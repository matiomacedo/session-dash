import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import hook  # noqa: E402

REAL_CLAUDE_PROCESS = hook.claude_process
HOOK = Path(__file__).parent.parent / "scripts" / "hook.py"
SID = "abc-123"


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook, "claude_process", lambda: (4242, "ttys001"))
    return tmp_path


def send(event, now, **extra):
    hook.apply_event({"session_id": SID, "cwd": "/Users/me/api-server", "hook_event_name": event, **extra}, now=now)


def state(state_dir):
    return json.loads((state_dir / f"{SID}.json").read_text())


def test_session_start_is_idle(state_dir):
    send("SessionStart", 100)
    s = state(state_dir)
    assert (s["state"], s["project"], s["pid"], s["tty"]) == ("idle", "api-server", 4242, "ttys001")


def test_tool_use_is_working_and_keeps_state_since(state_dir):
    send("UserPromptSubmit", 100)
    send("PreToolUse", 110, tool_name="Bash")
    s = state(state_dir)
    assert (s["state"], s["last_tool"], s["state_since"], s["last_event_at"]) == ("working", "Bash", 100, 110)


def test_permission_notification_needs_you_then_cleared(state_dir):
    send("PreToolUse", 100, tool_name="Bash")
    send("Notification", 105, message="Claude needs your permission to use Bash", notification_type="permission_prompt")
    assert state(state_dir)["state"] == "needs_you"
    assert state(state_dir)["notification"] == "Claude needs your permission to use Bash"
    send("PostToolUse", 120, tool_name="Bash")
    s = state(state_dir)
    assert (s["state"], s["notification"], s["state_since"]) == ("working", None, 120)


def test_parallel_tool_finishing_keeps_needs_you(state_dir):
    send("PreToolUse", 100, tool_name="Bash")
    send("PreToolUse", 101, tool_name="Read")
    send("Notification", 102, message="Claude needs your permission to use Bash", notification_type="permission_prompt")
    send("PostToolUse", 103, tool_name="Read")
    assert state(state_dir)["state"] == "needs_you"
    send("PostToolUse", 110, tool_name="Bash")
    assert state(state_dir)["state"] == "working"


def test_parallel_same_tool_waits_for_the_blocked_call(state_dir):
    send("PreToolUse", 100, tool_name="Bash", tool_use_id="allowed")
    send("PreToolUse", 101, tool_name="Bash", tool_use_id="asks")
    send("Notification", 102, message="Claude needs your permission to use Bash", notification_type="permission_prompt")
    send("PostToolUse", 103, tool_name="Bash", tool_use_id="allowed")
    assert state(state_dir)["state"] == "needs_you"
    send("PostToolUse", 110, tool_name="Bash", tool_use_id="asks")
    assert state(state_dir)["state"] == "working"


def test_display_name_mismatch_waits_for_all_in_flight(state_dir):
    send("PreToolUse", 100, tool_name="mcp__gh__create_issue", tool_use_id="a")
    send("PreToolUse", 101, tool_name="Read", tool_use_id="b")
    send("Notification", 102, message="Claude needs your permission to use gh - create_issue (MCP)")
    send("PostToolUse", 103, tool_name="Read", tool_use_id="b")
    assert state(state_dir)["state"] == "needs_you"
    send("PostToolUse", 110, tool_name="mcp__gh__create_issue", tool_use_id="a")
    s = state(state_dir)
    assert (s["state"], s["last_tool"], s["in_flight"]) == ("working", "mcp__gh__create_issue", {})


def test_failed_tool_counts_as_finished(state_dir):
    send("PreToolUse", 100, tool_name="Bash", tool_use_id="a")
    send("Notification", 102, message="Claude needs your permission to use Bash")
    send("PostToolUseFailure", 110, tool_name="Bash", tool_use_id="a")
    assert state(state_dir)["state"] == "working"


def test_notification_with_nothing_in_flight_ignores_tool_finishes(state_dir):
    send("Notification", 100, message="Claude needs your attention", notification_type="elicitation_dialog")
    send("PostToolUse", 101, tool_name="Read", tool_use_id="late")
    assert state(state_dir)["state"] == "needs_you"
    send("UserPromptSubmit", 110)
    assert state(state_dir)["state"] == "working"


def test_subagent_tool_use_keeps_needs_you(state_dir):
    send("PreToolUse", 100, tool_name="Bash", tool_use_id="a")
    send("Notification", 102, message="Claude needs your permission to use Bash")
    send("PreToolUse", 103, tool_name="Grep", tool_use_id="s1", agent_id="sub-1")
    send("PostToolUse", 104, tool_name="Grep", tool_use_id="s1", agent_id="sub-1")
    assert state(state_dir)["state"] == "needs_you"
    assert state(state_dir)["last_tool"] == "Bash"  # still the call waiting on you
    send("PreToolUse", 110, tool_name="Read", tool_use_id="b")  # main agent moving on clears it
    assert state(state_dir)["state"] == "working"
    assert state(state_dir)["last_tool"] == "Read"


def test_stop_forgets_unfinished_calls(state_dir):
    send("PreToolUse", 100, tool_name="Bash", tool_use_id="denied")
    send("Stop", 110)
    assert state(state_dir)["in_flight"] == {}


@pytest.mark.parametrize("extra", [
    {"message": "Claude is waiting for your input", "notification_type": "idle_prompt"},
    {"message": "Claude is waiting for your input"},
    {"message": "Signed in", "notification_type": "auth_success"},
])
def test_info_notifications_do_not_need_you(state_dir, extra):
    send("Stop", 100)
    send("Notification", 160, **extra)
    assert state(state_dir)["state"] == "idle"


def test_notification_with_null_message(state_dir):
    send("Notification", 100, message=None)
    assert state(state_dir)["state"] == "needs_you"


def test_stop_is_idle(state_dir):
    send("UserPromptSubmit", 100)
    send("Stop", 130)
    assert state(state_dir)["state"] == "idle"


def test_compact_session_start_keeps_working(state_dir):
    send("PreToolUse", 100, tool_name="Bash")
    send("SessionStart", 120, source="compact")
    s = state(state_dir)
    assert (s["state"], s["state_since"]) == ("working", 100)


def test_session_start_refreshes_pid(state_dir, monkeypatch):
    send("SessionStart", 100, source="startup")
    monkeypatch.setattr(hook, "claude_process", lambda: (5555, "ttys009"))
    send("UserPromptSubmit", 110)
    assert state(state_dir)["pid"] == 4242  # only looked up when missing
    send("SessionStart", 200, source="resume")
    assert (state(state_dir)["pid"], state(state_dir)["tty"]) == (5555, "ttys009")


def test_ps_lookup_skipped_for_ignored_events(state_dir, monkeypatch):
    monkeypatch.setattr(hook, "claude_process", lambda: pytest.fail("ps should not run"))
    hook.apply_event({"session_id": SID, "cwd": "/x", "hook_event_name": "SubagentStop"}, now=1)
    assert list(state_dir.iterdir()) == []


def test_unknown_pid_is_looked_up_again(state_dir, monkeypatch):
    monkeypatch.setattr(hook, "claude_process", lambda: (None, None))
    send("UserPromptSubmit", 100)
    monkeypatch.setattr(hook, "claude_process", lambda: (777, "ttys002"))
    send("PreToolUse", 110, tool_name="Read")
    assert (state(state_dir)["pid"], state(state_dir)["tty"]) == (777, "ttys002")


def test_ps_failure_still_records_event(state_dir, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("ps", 1)
    monkeypatch.setattr(hook, "claude_process", REAL_CLAUDE_PROCESS)
    monkeypatch.setattr(hook.subprocess, "run", boom)
    send("UserPromptSubmit", 100)
    s = state(state_dir)
    assert (s["state"], s["pid"], s["tty"]) == ("working", None, None)  # unconfirmed pid is never pruned


def transcript(tmp_path, entries, junk_prefix=""):
    path = tmp_path / "t.jsonl"
    path.write_text(junk_prefix + "\n".join(json.dumps(e) for e in entries) + "\n")
    return str(path)


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def test_summary_from_transcript_tail(state_dir, tmp_path):
    path = transcript(tmp_path, [
        assistant({"type": "text", "text": "Earlier reply"}),
        {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
        assistant({"type": "thinking", "thinking": "hmm"}),
        assistant({"type": "text", "text": "\n## **Done**: migrated the `users` table\n\nDetails..."}),
        assistant({"type": "tool_use", "name": "Bash"}),
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "assistant"}]}},
    ], junk_prefix='{"partial line from the middle of a blob"\n')
    send("Stop", 100, transcript_path=path)
    assert state(state_dir)["summary"] == "Done: migrated the users table"


def test_summary_prefers_payload_message(state_dir):
    send("Stop", 100, last_assistant_message="1. First step done\n2. Second", transcript_path="/nope")
    assert state(state_dir)["summary"] == "First step done"


def test_summary_never_shows_an_older_turn(state_dir, tmp_path):
    # the final reply isn't flushed yet: better empty than the previous turn's reply
    path = transcript(tmp_path, [
        assistant({"type": "text", "text": "Reply to the first prompt"}),
        {"type": "user", "message": {"content": "second prompt"}},
        assistant({"type": "tool_use", "name": "Bash"}),
    ])
    send("Stop", 100, transcript_path=path)
    assert state(state_dir)["summary"] == ""


def test_summary_missing_transcript_clears_previous(state_dir):
    send("Stop", 100, last_assistant_message="Previous turn")
    send("Stop", 200, transcript_path="/does/not/exist")
    assert state(state_dir)["summary"] == ""


@pytest.mark.parametrize("text,expected", [
    ("```bash\nrm -rf build\n```\nCleaned the build dir", "Cleaned the build dir"),
    ("-5 tests failing after the change", "-5 tests failing after the change"),
    ("- item one\n- item two", "item one"),
    ("> quoted note", "quoted note"),
    ("2) Second approach wins", "Second approach wins"),
])
def test_first_line_markdown(text, expected):
    assert hook.first_line(text) == expected


def test_summary_truncates_long_line():
    s = hook.first_line("word " * 40)
    assert len(s) == 80 and s.endswith("…")


def test_summary_tail_skips_partial_first_line(tmp_path):
    path = transcript(tmp_path, [assistant({"type": "text", "text": "x" * 50}), assistant({"type": "text", "text": "Last"})])
    last_line = len(Path(path).read_text().splitlines()[-1]) + 1
    assert hook.summarize({"transcript_path": path}, tail_bytes=last_line + 20) == "Last"


def test_session_end_removes_file(state_dir):
    send("SessionStart", 100)
    send("SessionEnd", 200)
    assert not (state_dir / f"{SID}.json").exists()


def test_late_hook_after_session_end_does_not_resurrect(state_dir):
    send("SessionStart", 100)
    send("SessionEnd", 200)
    send("Stop", 201)
    assert not (state_dir / f"{SID}.json").exists()
    send("SessionStart", 300, source="resume")
    assert state(state_dir)["state"] == "idle"


def test_hooks_wait_for_lock(state_dir):
    done = threading.Event()
    with hook.locked():
        t = threading.Thread(target=lambda: (send("UserPromptSubmit", 100), done.set()))
        t.start()
        assert not done.wait(0.2)  # blocked while another hook holds the lock
    t.join(2)
    assert done.is_set() and state(state_dir)["state"] == "working"


def test_failed_save_leaves_no_temp_file(state_dir):
    with pytest.raises(TypeError):
        hook.save(state_dir / "x.json", {"bad": object()})
    assert list(state_dir.iterdir()) == []


def test_session_id_cannot_escape_dir(state_dir):
    hook.apply_event({"session_id": "../../evil", "cwd": "/x", "hook_event_name": "SessionStart"}, now=1)
    assert [p.name for p in state_dir.glob("*.json")] == ["evil.json"]
    assert not list(state_dir.parent.glob("*.json"))


@pytest.mark.parametrize("stdin", [b"", b"not json", b'{"hook_event_name": "Stop"}'])
def test_script_is_silent_and_exits_zero_on_bad_input(tmp_path, stdin):
    r = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True,
                       env={"CLAUDE_DASH_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert (r.returncode, r.stdout, r.stderr) == (0, b"", b"")


def test_script_is_silent_when_dir_unwritable(tmp_path):
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("")
    payload = json.dumps({"session_id": SID, "cwd": "/x", "hook_event_name": "SessionStart"}).encode()
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                       env={"CLAUDE_DASH_DIR": str(blocked / "sub"), "PATH": "/usr/bin:/bin"})
    assert (r.returncode, r.stdout, r.stderr) == (0, b"", b"")


def test_script_writes_state_end_to_end(tmp_path):
    payload = json.dumps({"session_id": SID, "cwd": "/x/web", "hook_event_name": "UserPromptSubmit"}).encode()
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                       env={"CLAUDE_DASH_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.stdout == b""
    assert json.loads((tmp_path / f"{SID}.json").read_text())["state"] == "working"
