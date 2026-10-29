#!/usr/bin/env python3
"""Claude Code hook: record this session's state for claude-dash.

Reads the hook payload from stdin and writes ~/.cache/claude-dash/<session_id>.json.
Stdlib only, prints nothing, and always exits 0 so it can never disturb a session.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

STATE_DIR = Path(os.environ.get("CLAUDE_DASH_DIR", Path.home() / ".cache" / "claude-dash"))
SHELLS = {"sh", "bash", "zsh", "dash"}
TOOL_DONE = {"PostToolUse", "PostToolUseFailure"}
EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "Notification", "Stop", "SessionEnd"} | TOOL_DONE
# Notifications that don't mean Claude is blocked on the user
INFO_NOTIFICATIONS = {"idle_prompt", "auth_success"}


def state_path(session_id, suffix=".json"):
    # session ids are UUIDs; strip anything that could escape the directory
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return STATE_DIR / f"{safe}{suffix}"


@contextmanager
def locked():
    """Serialise read-modify-write across concurrent hooks (parallel tool calls)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(path, data):
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def claude_process():
    """Return (pid, tty) of the Claude process that ran this hook, skipping a wrapper shell."""
    pid, tty = os.getppid(), None
    try:
        ok = False
        for _ in range(2):
            out = subprocess.run(
                ["ps", "-o", "ppid=,tty=,comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=1,
            ).stdout.split(None, 2)
            if len(out) < 3:
                break
            ppid, tty, comm = out
            tty = None if tty in ("??", "-") else tty
            if os.path.basename(comm.strip()).lstrip("-") not in SHELLS:
                ok = True
                break
            pid = int(ppid)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # an unconfirmed pid could be a short-lived shell; the dashboard would prune a live session
    return (pid, tty) if ok else (None, tty)


MARKDOWN_PREFIX = re.compile(r"^(?:#+|>+|[-*+]|\d+[.)])\s+")


def first_line(text, limit=80):
    """Plain one-line summary: first non-empty prose line, markdown markers stripped."""
    in_code = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        line = MARKDOWN_PREFIX.sub("", line).replace("**", "").replace("`", "")
        line = " ".join(line.split())
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def is_prompt(entry):
    """A transcript entry for something the user typed (not a tool result)."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return any(isinstance(b, dict) and b.get("type") == "text" for b in content or [])


def summarize(payload, tail_bytes=512 * 1024):
    """One-line summary of Claude's reply this turn, from the payload or the transcript tail.

    Stops at the user's last prompt, so an unflushed reply gives "" rather than an older one.
    """
    if payload.get("last_assistant_message"):
        return first_line(payload["last_assistant_message"])
    try:
        with open(payload["transcript_path"], "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (KeyError, TypeError, OSError):
        return ""
    for raw in reversed(lines):
        if '"assistant"' not in raw and '"user"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue  # partial first line of the tail
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "user" and is_prompt(entry):
            break
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        texts = [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        summary = first_line("\n".join(texts))
        if summary:
            return summary
    return ""


def is_info_notification(payload):
    if payload.get("notification_type"):
        return payload["notification_type"] in INFO_NOTIFICATIONS
    return "waiting for your input" in (payload.get("message") or "")


def apply_event(payload, now=None):
    """Update the state file for one hook event."""
    now = now if now is not None else time.time()
    event = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    if not session_id or event not in EVENTS:
        return
    path, ended = state_path(session_id), state_path(session_id, ".ended")

    # slow work (ps, reading the transcript) happens outside the lock
    proc = None
    if event == "SessionStart" or (event != "SessionEnd" and load(path).get("pid") is None):
        proc = claude_process()
    summary = summarize(payload) if event == "Stop" else ""

    with locked():
        if event == "SessionEnd":
            path.unlink(missing_ok=True)
            ended.touch()  # stops a still-running hook from recreating the file
            return
        if event == "SessionStart":
            ended.unlink(missing_ok=True)  # resumed session
        elif ended.exists():
            return

        data = load(path)
        cwd = payload.get("cwd") or data.get("cwd") or ""
        data.update(
            session_id=session_id,
            cwd=cwd,
            project=os.path.basename(cwd.rstrip("/")) or cwd,
            last_event_at=now,
        )
        if proc:
            data["pid"], data["tty"] = proc

        # in_flight: tool calls started but not finished, {tool_use_id: tool_name}
        # blocked_on: the in-flight calls that might be waiting for permission
        in_flight = data.get("in_flight") or {}
        tool = payload.get("tool_name")
        call_id = payload.get("tool_use_id") or tool
        state = data.get("state", "idle")
        new_state = state

        def clear():
            data["notification"], data["blocked_on"] = None, []
            return "working"

        if event == "SessionStart":
            if payload.get("source") != "compact":  # compaction fires mid-turn
                new_state = "idle"
                in_flight = {}
        elif event == "UserPromptSubmit":
            new_state = clear()
            in_flight = {}
        elif event == "PreToolUse":
            in_flight[call_id] = tool
            data["last_tool"] = tool
            # a subagent's tool call (same session id) doesn't answer the main agent's prompt
            if state != "needs_you" or not payload.get("agent_id"):
                new_state = clear()
        elif event in TOOL_DONE:
            in_flight.pop(call_id, None)
            if state != "needs_you":
                new_state = "working"
                data["last_tool"] = tool
            elif data.get("blocked_on"):
                # cleared only once every call that could be the blocked one has finished
                data["blocked_on"] = [c for c in data["blocked_on"] if c in in_flight]
                if not data["blocked_on"]:
                    new_state = clear()
                    data["last_tool"] = tool
        elif event == "Notification":
            if not is_info_notification(payload):
                new_state = "needs_you"
                message = payload.get("message") or ""
                data["notification"] = message
                # "Claude needs your permission to use Bash" names the tool; MCP display names may not match
                named = message.rpartition("permission to use ")[2].strip()
                data["blocked_on"] = [c for c, t in in_flight.items() if t == named] or list(in_flight)
        elif event == "Stop":
            new_state = "idle"
            in_flight = {}
            data["summary"] = summary
        data["in_flight"] = in_flight

        if new_state != state or "state" not in data:
            data["state"] = new_state
            data["state_since"] = now
        save(path, data)


def main():
    try:
        apply_event(json.load(sys.stdin))
    except BaseException:
        pass  # never block or fail a Claude session
    os._exit(0)


if __name__ == "__main__":
    main()
