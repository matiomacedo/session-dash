#!/bin/bash
# Open the dashboard in a new Terminal.app window.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
uv="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$uv" ]; then
  echo "uv not found. Install it with: brew install uv"
  exit 1
fi

printf -v cmd 'clear; exec %q run --quiet --script %q' "$uv" "$root/scripts/dash.py"
cmd="${cmd//\\/\\\\}"
cmd="${cmd//\"/\\\"}"
osascript -e "tell application \"Terminal\" to do script \"$cmd\"" -e 'tell application "Terminal" to activate' >/dev/null
echo "Opened the session dashboard in a new Terminal window."
