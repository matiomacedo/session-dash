#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13"]
# ///
"""SwiftBar plugin: cycle through sessions in the menu bar, list them all in the dropdown."""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dash import AMBER, RED, STATE_DIR, STATES, elapsed, load_sessions, plural, split_stale  # noqa: E402

COLORS = {"needs_you": RED, "working": AMBER}
PERIOD = 3  # seconds each session stays in the title; matches the .3s. refresh in the plugin filename


def clean(text):
    """Keep session text from adding SwiftBar params (|) or lines, or reading as a separator (-)."""
    return " ".join(str(text).replace("|", "│").split()).lstrip("- ")


def params(state, **extra):
    if state in COLORS:
        extra = {"color": COLORS[state], **extra}
    return " | " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""


def status(s, now):
    state = s.get("state")
    label = STATES.get(state, (state or "?",))[0]
    # mcp__server__tool -> tool, so long MCP names don't crowd out the rest of the title
    tool = f" · {clean(s['last_tool'].rpartition('__')[2])}" if s.get("last_tool") else ""
    return f"{label} {elapsed(now - s.get('state_since', now))}{tool}"


def render_menu(sessions, now, hidden=0, stale_minutes="30"):
    hidden_note = f"{plural(hidden, 'session')} quiet for over {stale_minutes}m hidden." if hidden else ""
    if not sessions:
        lines = ["✳︎", "---", f"No active sessions. {hidden_note}".strip()]
    else:
        shown = sessions[int(now // PERIOD) % len(sessions)]
        waiting = sum(s.get("state") == "needs_you" for s in sessions)
        # keep a blocked session visible while the title shows another one
        prefix = f"({waiting}!) " if waiting and shown.get("state") != "needs_you" else ""
        lines = [f"{prefix}● {clean(shown.get('project') or '?')} · {status(shown, now)}"
                 + params(shown.get("state"), length=50), "---"]
        for s in sessions:
            blocked = s.get("state") == "needs_you"
            reply = (s.get("notification") or "Claude is waiting for you") if blocked else s.get("summary")
            lines.append(f"{clean(s.get('project') or '?')} — {status(s, now)}" + params(s.get("state")))
            if reply:
                lines.append(f"--{clean(reply)}" + params(None, length=80))
        count = plural(len(sessions), "session") + (f", {waiting} need{'s' if waiting == 1 else ''} you" if waiting else "")
        lines += ["---", f"{count}. {hidden_note}".strip()]
    lines += ["---", f'Open dashboard | bash="{HERE / "open-dash.sh"}" terminal=false', "Refresh | refresh=true"]
    return "\n".join(lines)


def main():
    now = time.time()
    stale_minutes = float(os.environ.get("CLAUDE_DASH_STALE_MIN", "30"))
    sessions, hidden = split_stale(load_sessions(STATE_DIR, now), now, stale_minutes * 60)
    print(render_menu(sessions, now, hidden, f"{stale_minutes:g}"))


if __name__ == "__main__":
    main()
