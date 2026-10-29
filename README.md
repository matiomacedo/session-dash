# session-dash

See every open Claude Code session in one terminal window: which are working, which are idle, and which are waiting on you.

```
Project     State             Last tool  Last reply
api-server    needs you   2m  Bash       Claude needs your permission to use Bash
web-client  ⠧ working     3m  Edit
infra         idle       12m             Deployed the preview stack and posted …

3 sessions, 1 needs you. Press q to quit.
```

Sessions waiting on you are red and sorted to the top, with the prompt you need to answer. Idle sessions show the first line of Claude's last reply.

## How it works

Hooks record each session's state in `~/.cache/claude-dash/<session_id>.json`, and the dashboard reads that folder once a second. There is no daemon and there are no API calls.

The hooks use plain Python with no extra packages. Each one runs in about 20ms, prints nothing, and always exits successfully, so a session behaves exactly the same whether or not the dashboard is open.

## Install

Requires macOS, Python 3.10 or later, and [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```
/plugin marketplace add matiomacedo/session-dash
/plugin install session-dash@session-dash
```

Restart any sessions that were already open so they pick up the hooks. To try it without installing, clone the repo and start a session with `claude --plugin-dir path/to/session-dash`.

## Usage

- **`/dash`** in any session opens the dashboard in a new Terminal.app window.
- **`uv run --script scripts/dash.py`** runs it in the current terminal.
- **`q`** quits.

States:

| State | Meaning | Clears when |
|---|---|---|
| needs you | Claude is asking for permission or input | you answer, or send a new prompt |
| working | Claude is thinking or using tools | Claude finishes its reply |
| idle | Claude has finished and is waiting for your next prompt | you send a prompt |

The time next to each state is how long the session has been in it.

## Options

| Setting | Default | Effect |
|---|---|---|
| `--stale-minutes N` or `CLAUDE_DASH_STALE_MIN=N` | 30 | Hide sessions with no activity for N minutes. They come back with their next event. |
| `CLAUDE_DASH_DIR` | `~/.cache/claude-dash` | Where hooks write and the dashboard reads. Set it for both. |

Sessions whose Claude process has exited are removed automatically, and so is anything untouched for 24 hours.

## Development

```
uv run --no-project --with pytest --with rich pytest -q
```

## License

MIT
