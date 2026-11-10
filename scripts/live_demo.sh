#!/usr/bin/env bash
# Live end-to-end with a real model through Hermes's own cron runner.
# NEEDS JOE'S CONFIGURED MODEL AND KEY in ~/.hermes. Nothing is added or bought here. Not run in CI.
# Usage: scripts/live_demo.sh [repo-dir]
set -euo pipefail
REPO="$(cd "${1:-$PWD}" && pwd)"
PROMPT="In this repo: run 'git log --oneline -n 5' and 'grep -rn TODO .', then write a short triage report to reports/triage.md."
hermes plugins enable mandate
hermes mandate watch nightly-triage
hermes cron create --name nightly-triage --workdir "$REPO" "0 3 * * *" "$PROMPT"
JOB_ID="$(hermes cron list 2>/dev/null | awk '/nightly-triage/ {print $1; exit}')"
JOB_ID="${JOB_ID:-nightly-triage}"
fire() { hermes cron run "$JOB_ID" && hermes cron tick; }
for i in 1 2 3; do fire; done
hermes mandate propose nightly-triage
hermes mandate approve nightly-triage          # interactive: shows the diff, asks y/N
fire
hermes mandate status nightly-triage
hermes mandate drift nightly-triage
