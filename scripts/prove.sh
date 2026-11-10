#!/usr/bin/env bash
# One command from a clean clone: pinned Hermes, validate, tests, real-Hermes scenario, benchmark, proof.
# HERMES_TAG=v0.23.1 ./scripts/prove.sh proves against another supported release (default v0.23.0).
set -euo pipefail
cd "$(dirname "$0")/.."
START="$(date +%s.%N)"
export HERMES_TAG="${HERMES_TAG:-v0.23.0}"
export HERMES_SRC="${HERMES_SRC:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-hermes/$HERMES_TAG}"
# "warm": Hermes for this tag was already installed when the run started; "cold": installed by this run.
if [ -x "$HERMES_SRC/.venv/bin/hermes" ]; then INSTALL=warm; else INSTALL=cold; fi
./scripts/install_hermes.sh
PY="$HERMES_SRC/.venv/bin/python"
if command -v npm >/dev/null; then ./scripts/install_react.sh; fi
HERMES="$HERMES_SRC/.venv/bin/hermes"
# Scratch lives outside the plugin tree: the validator's security scan walks the whole plugin dir.
TMP="${MANDATE_PROVE_TMP:-${XDG_CACHE_HOME:-$HOME/.cache}/mandate-prove}/$HERMES_TAG"
rm -rf "$TMP" && mkdir -p "$TMP"
export HERMES_HOME="$TMP/hermes-home"
echo "== hermes plugins validate . --install-deps ($HERMES_TAG)"
"$HERMES" plugins validate . --install-deps 2>&1 | tee "$TMP/validate.txt"
unset HERMES_HOME
echo "== integration scenario (real Hermes loader, cron identity, tool dispatch)"
"$PY" scripts/integration_scenario.py > "$TMP/scenario.json"
echo "== pytest"
MANDATE_SCENARIO_JSON="$TMP/scenario.json" "$PY" -m pytest --junitxml="$TMP/junit.xml" || true
END="$(date +%s.%N)"
echo "== proof"
MANDATE_PROVE_SECONDS="$("$PY" -c "print(round($END - $START, 1))")" MANDATE_PROVE_INSTALL="$INSTALL" \
  "$PY" scripts/make_proof.py "$HERMES_TAG" "$TMP/junit.xml" "$TMP/scenario.json" "$TMP/validate.txt"
