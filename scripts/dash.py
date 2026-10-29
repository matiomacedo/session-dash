#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13"]
# ///
"""Live table of Claude Code sessions, read from the files hook.py writes."""
import argparse
import json
import os
import select
import sys
import termios
import time
import tty
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

STATE_DIR = Path(os.environ.get("CLAUDE_DASH_DIR", Path.home() / ".cache" / "claude-dash"))
PRUNE_AFTER = 24 * 3600  # delete files untouched this long, alive or not
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
RED, AMBER, SAGE, GREY = "#E5484D", "#E8B04B", "#7FA287", "#8A8F98"
STATES = {  # state -> (label, style, sort rank)
    "needs_you": ("needs you", f"bold {RED}", 0),
    "working": ("working", AMBER, 1),
    "idle": ("idle", f"dim {SAGE}", 2),
}


def is_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else
    return True


def load_sessions(state_dir, now, alive=is_alive):
    """Read session files, deleting those whose process is gone and anything older than a day."""
    sessions = []
    for path in list(state_dir.glob("*.json")) + list(state_dir.glob("*.ended")):
        try:
            if now - path.stat().st_mtime > PRUNE_AFTER:
                path.unlink()
                continue
            if path.suffix != ".json":
                continue
            session = json.loads(path.read_text())
            if not isinstance(session, dict):
                continue  # not one of ours
            pid = session.get("pid")
            if isinstance(pid, int) and not alive(pid):
                path.unlink()
                continue
            sessions.append(session)
        except (OSError, ValueError):
            continue  # being replaced or removed right now
    return sorted(sessions, key=lambda s: (STATES.get(s.get("state"), ("", "", 3))[2], -s.get("last_event_at", 0)))


def split_stale(sessions, now, stale_seconds):
    fresh = [s for s in sessions if now - s.get("last_event_at", 0) <= stale_seconds]
    return fresh, len(sessions) - len(fresh)


def elapsed(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def render(sessions, now, hidden=0, stale_minutes=30):
    hidden_note = f"{plural(hidden, 'session')} quiet for over {stale_minutes}m hidden. " if hidden else ""
    if not sessions:
        return Text(f"No active sessions. Start claude in any terminal and it shows up here. {hidden_note}".strip(), style=GREY)

    table = Table(box=None, pad_edge=False, padding=(0, 2, 0, 0), header_style="bold")
    table.add_column("Project", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("", justify="right", no_wrap=True)
    table.add_column("Last tool", no_wrap=True)
    # the only column that may shrink; its text truncates instead of wrapping
    table.add_column("Last reply", ratio=1)
    for s in sessions:
        state = s.get("state")
        label, style, _ = STATES.get(state, (state or "?", GREY, 3))
        glyph = SPINNER[int(now * 10) % len(SPINNER)] if state == "working" else " "
        blocked = state == "needs_you"
        # while blocked, the notification says what to approve; that beats the previous reply
        reply = (s.get("notification") or "Claude is waiting for you") if blocked else (s.get("summary") or "")
        table.add_row(
            Text(s.get("project") or "?", style=f"bold {RED}" if blocked else "bold"),
            Text(f"{glyph} {label}", style=style),
            Text(elapsed(now - s.get("state_since", now)), style=RED if blocked else GREY),
            Text(s.get("last_tool") or "", style=RED if blocked else GREY),
            Text(reply, style=RED if blocked else "", no_wrap=True, overflow="ellipsis"),
        )

    waiting = sum(s.get("state") == "needs_you" for s in sessions)
    count = plural(len(sessions), "session") + (f", {waiting} need{'s' if waiting == 1 else ''} you" if waiting else "")
    return Group(table, Text(), Text(f"{count}. {hidden_note}Press q to quit.", style=GREY))


def key_pressed(timeout):
    """Wait up to timeout seconds for a key; return it or None."""
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if ready else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stale-minutes", type=float, default=os.environ.get("CLAUDE_DASH_STALE_MIN", "30"),
                        help="hide sessions with no events for this long (default 30, env CLAUDE_DASH_STALE_MIN)")
    args = parser.parse_args()
    stale_minutes = f"{args.stale_minutes:g}"

    console = Console()
    old = termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
    sessions, hidden, loaded_at = [], 0, 0.0
    try:
        if old:
            tty.setcbreak(sys.stdin)
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                now = time.time()
                if now - loaded_at >= 1:  # read files once a second; redraw faster for the spinner
                    sessions, hidden = split_stale(load_sessions(STATE_DIR, now), now, args.stale_minutes * 60)
                    loaded_at = now
                live.update(render(sessions, now, hidden, stale_minutes), refresh=True)
                if old and key_pressed(0.1) in ("q", "Q"):
                    break
                if not old:
                    time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if old:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
