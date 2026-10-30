import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import menubar  # noqa: E402

SESSIONS = [
    {"project": "api-server", "state": "needs_you", "state_since": 880, "last_tool": "Bash",
     "notification": "Claude needs your permission to use Bash", "summary": "old reply"},
    {"project": "web-client", "state": "working", "state_since": 820, "last_tool": "Edit"},
    {"project": "infra", "state": "idle", "state_since": 280, "summary": "Deployed the preview stack"},
]


def title(sessions, now):
    return menubar.render_menu(sessions, now).splitlines()[0]


def test_title_rotates_every_period():
    assert title(SESSIONS, 999).startswith("● api-server · needs you 1m · Bash |")
    assert title(SESSIONS, 1002).startswith("(1!) ● web-client · working 3m · Edit |")
    assert title(SESSIONS, 1005).startswith("(1!) ● infra · idle 12m |")
    assert title(SESSIONS, 1008).startswith("● api-server")


def test_title_colors_by_state():
    assert "color=#E5484D" in title(SESSIONS, 999)
    assert "color=#E8B04B" in title(SESSIONS, 1002)
    assert "color=" not in title(SESSIONS, 1005)


def test_title_without_blocked_sessions_has_no_prefix():
    assert title(SESSIONS[1:], 1002).startswith("● web-client")


def test_empty():
    out = menubar.render_menu([], 1000, hidden=1)
    assert out.splitlines()[0] == "✳︎"
    assert "No active sessions. 1 session quiet for over 30m hidden." in out


def test_dropdown_lists_all_sessions_with_replies():
    out = menubar.render_menu(SESSIONS, 1000, hidden=2)
    lines = out.splitlines()
    assert lines[1] == "---"
    assert lines[2].startswith("api-server — needs you 2m · Bash | color=#E5484D")
    assert lines[3].startswith("--Claude needs your permission to use Bash |")
    assert lines[4].startswith("web-client — working 3m · Edit | color=#E8B04B")
    assert lines[5] == "infra — idle 12m"
    assert lines[6].startswith("--Deployed the preview stack |")
    assert "3 sessions, 1 needs you. 2 sessions quiet for over 30m hidden." in out
    assert any(l.startswith("Open dashboard | bash=") and "open-dash.sh" in l for l in lines)


def test_user_text_cannot_inject_params_or_lines():
    s = {"project": "a|b", "state": "idle", "state_since": 1000, "summary": "---x | bash=/bin/rm\nnext"}
    lines = menubar.render_menu([s], 1000).splitlines()
    assert lines[0].count("|") == 1
    reply = [l for l in lines if l.startswith("--") and not l.startswith("---")][0]
    assert reply.startswith("--x │ bash=/bin/rm next |")


def test_mcp_tool_shows_only_its_own_name():
    s = {"project": "web", "state": "working", "state_since": 1000, "last_tool": "mcp__claude_ai_Google_Drive__search_files"}
    assert title([s], 1000).startswith("● web · working 0s · search_files |")
