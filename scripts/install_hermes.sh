#!/usr/bin/env bash
# Install a pinned Hermes Agent release into ~/.cache/mandate-hermes/$HERMES_TAG/.venv (no wheels: Hermes refuses
# non-Nix wheel builds, so we use a checkout at the tag plus its frozen uv lock).
set -euo pipefail
# Supported releases: tag -> peeled commit sha (git rev-parse HEAD of the tag checkout).
HERMES_TAG="${HERMES_TAG:-v0.23.0}"
case "$HERMES_TAG" in
  v0.23.0) PINNED_SHA=2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19 ;;
  v0.23.1) PINNED_SHA=3b421578588243f920886e6fe79a6faa87ea026e ;;
  *) PINNED_SHA="" ;;
esac
HERMES_SHA="${HERMES_SHA:-$PINNED_SHA}"
if [ -z "$HERMES_SHA" ]; then
  echo "No pinned commit for $HERMES_TAG; set HERMES_SHA to the tag's commit sha" >&2
  exit 1
fi
if [ -n "$PINNED_SHA" ] && [ "$HERMES_SHA" != "$PINNED_SHA" ]; then
  echo "HERMES_SHA $HERMES_SHA does not match the pinned commit for $HERMES_TAG ($PINNED_SHA)" >&2
  exit 1
fi
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
