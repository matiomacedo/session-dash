#!/bin/bash
# Add the menu bar ticker to SwiftBar's plugin folder.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
uv="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$uv" ]; then
  echo "uv not found. Install it with: brew install uv"
  exit 1
fi

plugins="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [ -z "$plugins" ] || [ ! -d "$plugins" ]; then
  echo "SwiftBar plugin folder not found. Install SwiftBar (brew install swiftbar), open it once and choose a plugin folder, then run /dash-bar again."
  exit 0
fi

# SwiftBar runs plugins with a minimal PATH, so the wrapper uses absolute paths.
# .3s. makes SwiftBar rerun it every 3 seconds, which is what rotates the title.
plugin="$plugins/session-dash.3s.sh"
printf '#!/bin/bash\nexec %q run --quiet --script %q\n' "$uv" "$root/scripts/menubar.py" > "$plugin"
chmod +x "$plugin"
open -g "swiftbar://refreshallplugins"
echo "Added the session ticker to the menu bar."
