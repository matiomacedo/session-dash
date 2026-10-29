import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import dash  # noqa: E402

NOW = 1000
DASH = Path(__file__).parent.parent / "scripts" / "dash.py"


def render_text(sessions, now=1000, **kw):
    console = Console(record=True, width=160, color_system=None)
    console.print(dash.render(sessions, now, **kw))
    return console.export_text()


def write(path, data, mtime=None):
    path.write_text(json.dumps(data))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.mark.parametrize("seconds,label", [(-5, "0s"), (42, "42s"), (180, "3m"), (7200, "2h"), (90000, "1d")])
def test_elapsed(seconds, label):
    assert dash.elapsed(seconds) == label


def test_load_sessions_skips_bad_files(tmp_path):
    write(tmp_path / "a.json", {"project": "ok"})
    (tmp_path / "c.json").write_text("{half-written")
    (tmp_path / "d.ended").write_text("")
    (tmp_path / ".lock").write_text("")
    (tmp_path / "list.json").write_text("[1, 2]")
    (tmp_path / "null.json").write_text("null")
    assert [s["project"] for s in dash.load_sessions(tmp_path, NOW)] == ["ok"]


def test_sort_needs_you_first_then_working_then_idle_then_recent(tmp_path):
    for name, state, t in [("i-old", "idle", 1), ("w", "working", 2), ("i-new", "idle", 5), ("n", "needs_you", 0)]:
        write(tmp_path / f"{name}.json", {"project": name, "state": state, "last_event_at": t})
    assert [s["project"] for s in dash.load_sessions(tmp_path, NOW)] == ["n", "w", "i-new", "i-old"]


def test_split_stale():
    sessions = [{"last_event_at": 1000 - 60}, {"last_event_at": 1000 - 31 * 60}]
    fresh, hidden = dash.split_stale(sessions, 1000, 30 * 60)
    assert (fresh, hidden) == ([sessions[0]], 1)


def test_load_prunes_dead_pid_and_old_files(tmp_path):
    now = 10 * 86400
    write(tmp_path / "alive.json", {"pid": 1})
    write(tmp_path / "dead.json", {"pid": 2})
    write(tmp_path / "unknown-pid.json", {"pid": None})
    write(tmp_path / "ancient.json", {"pid": 1}, mtime=now - 25 * 3600)
    (tmp_path / "old.ended").write_text("")
    os.utime(tmp_path / "old.ended", (now - 25 * 3600,) * 2)
    (tmp_path / "new.ended").write_text("")
    os.utime(tmp_path / "new.ended", (now,) * 2)
    for p in ["alive.json", "dead.json", "unknown-pid.json"]:
        os.utime(tmp_path / p, (now,) * 2)
    loaded = dash.load_sessions(tmp_path, now, alive=lambda pid: pid == 1)
    assert sorted(s["pid"] or 0 for s in loaded) == [0, 1]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alive.json", "new.ended", "unknown-pid.json"]


def test_is_alive():
    assert dash.is_alive(os.getpid())
    assert not dash.is_alive(2**22 + 12345)


def test_render_rows():
    out = render_text([
        {"project": "api-server", "state": "needs_you", "state_since": 880, "last_tool": "Bash",
         "notification": "Claude needs your permission to use Bash", "summary": "old reply"},
        {"project": "web-client", "state": "working", "state_since": 820, "last_tool": "Edit"},
        {"project": "infra", "state": "idle", "state_since": 280, "summary": "Deployed the preview stack"},
    ], hidden=2)
    lines = out.splitlines()
    assert lines[0].split() == ["Project", "State", "Last", "tool", "Last", "reply"]
    assert lines[1].split() == ["api-server", "needs", "you", "2m", "Bash",
                                "Claude", "needs", "your", "permission", "to", "use", "Bash"]
    assert lines[2].split()[0] == "web-client" and lines[2].split()[1] in dash.SPINNER
    assert lines[2].split()[2:] == ["working", "3m", "Edit"]
    assert lines[3].split() == ["infra", "idle", "12m", "Deployed", "the", "preview", "stack"]
    assert "3 sessions, 1 needs you. 2 sessions quiet for over 30m hidden. Press q to quit." in out


def test_bad_stale_minutes_env_is_a_usage_error():
    r = subprocess.run([sys.executable, DASH], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_DASH_STALE_MIN": "30m"})
    assert r.returncode == 2 and "invalid float value: '30m'" in r.stderr and "Traceback" not in r.stderr


def test_render_empty():
    assert "No active sessions" in render_text([])
    assert "1 session quiet for over 30m hidden." in render_text([], hidden=1)
