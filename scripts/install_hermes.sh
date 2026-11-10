#!/usr/bin/env bash
# Install the pinned Hermes Agent release into .hermes-src/.venv (no wheels: Hermes refuses
# non-Nix wheel builds, so we use a checkout at the tag plus its frozen uv lock).
set -euo pipefail
HERMES_TAG="${HERMES_TAG:-v0.23.0}"
HERMES_SHA="${HERMES_SHA:-2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${HERMES_SRC:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-hermes/$HERMES_TAG}"
if [ ! -d "$SRC/.git" ]; then
  mkdir -p "$(dirname "$SRC")"
  git clone --quiet --depth 1 --branch "$HERMES_TAG" https://github.com/NousResearch/hermes-agent.git "$SRC"
fi
actual="$(git -C "$SRC" rev-parse HEAD)"
if [ "$actual" != "$HERMES_SHA" ]; then
  echo "Hermes checkout at $actual, expected $HERMES_SHA ($HERMES_TAG)" >&2
  exit 1
fi
(cd "$SRC" && uv sync --frozen --python 3.14 --quiet)
uv pip install --quiet --python "$SRC/.venv/bin/python" --exclude-newer "3 days" -r "$ROOT/requirements-dev.txt"
"$SRC/.venv/bin/hermes" --version | head -1
echo "HERMES_VENV=$SRC/.venv"
