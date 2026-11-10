#!/usr/bin/env bash
# Deterministic ~90 s terminal story for screen recording. Real Hermes, real plugin, throwaway HOME.
# DEMO_PAUSE=0 ./demo/run_demo.sh runs it without pauses.
set -euo pipefail
cd "$(dirname "$0")/.."
export HERMES_TAG="${HERMES_TAG:-v0.23.0}"
export HERMES_SRC="${HERMES_SRC:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-hermes/$HERMES_TAG}"
[ -x "$HERMES_SRC/.venv/bin/python" ] || ./scripts/install_hermes.sh >/dev/null
export MANDATE_FORCE_COLOR="${MANDATE_FORCE_COLOR:-1}"
exec "$HERMES_SRC/.venv/bin/python" demo/run_demo.py "$@"
