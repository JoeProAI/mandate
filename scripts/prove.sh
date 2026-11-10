#!/usr/bin/env bash
# One command from a clean clone: pinned Hermes, validate, tests, real-Hermes scenario, benchmark, proof.
set -euo pipefail
cd "$(dirname "$0")/.."
export HERMES_SRC="${HERMES_SRC:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-hermes/v0.23.0}"
./scripts/install_hermes.sh
PY="$HERMES_SRC/.venv/bin/python"
HERMES="$HERMES_SRC/.venv/bin/hermes"
# Scratch lives outside the plugin tree: the validator's security scan walks the whole plugin dir.
TMP="${MANDATE_PROVE_TMP:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-prove}"
rm -rf "$TMP" && mkdir -p "$TMP"
export HERMES_HOME="$TMP/hermes-home"
echo "== hermes plugins validate . --install-deps"
"$HERMES" plugins validate . --install-deps 2>&1 | tee "$TMP/validate.txt"
unset HERMES_HOME
echo "== integration scenario (real Hermes loader, cron identity, tool dispatch)"
"$PY" scripts/integration_scenario.py > "$TMP/scenario.json"
echo "== pytest"
MANDATE_SCENARIO_JSON="$TMP/scenario.json" "$PY" -m pytest --junitxml="$TMP/junit.xml" || true
echo "== proof"
"$PY" scripts/make_proof.py "$TMP/junit.xml" "$TMP/scenario.json" "$TMP/validate.txt"
