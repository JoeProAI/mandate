#!/usr/bin/env bash
# Pinned react + react-dom outside the plugin tree, used only by tests/test_desktop.py to render desktop/plugin.js.
set -euo pipefail
DIR="${MANDATE_REACT_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-desktop-test/node_modules}"
ROOT="$(dirname "$DIR")"
[ -d "$DIR/react-dom" ] && exit 0
mkdir -p "$ROOT"
[ -f "$ROOT/package.json" ] || echo '{"name":"mandate-desktop-test","private":true}' > "$ROOT/package.json"
npm install --prefix "$ROOT" --no-audit --no-fund --silent react@19.1.1 react-dom@19.1.1
