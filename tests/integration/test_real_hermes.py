"""Runs scripts/integration_scenario.py against the installed Hermes (skipped when Hermes is absent)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("model_tools", reason="needs Hermes Agent installed in this interpreter")


@pytest.fixture(scope="module")
def scenario():
    out = os.environ.get("MANDATE_SCENARIO_JSON")
    if out and Path(out).exists():
        return json.loads(Path(out).read_text())
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "integration_scenario.py")],
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


def test_loaded_by_real_loader(scenario):
    assert scenario["mandate_loaded_by_hermes"] and scenario["mandate_status_registered"]
    assert os.environ.get("HERMES_TAG", "v0.23.0") in scenario["hermes_version"]


def test_observe_runs_never_blocked(scenario):
    assert scenario["observe_runs"] == 3 and scenario["observe_blocked"] == 0


def test_off_policy_blocked_and_recorded(scenario):
    assert scenario["off_policy_executed_enforce"] == 0
    assert scenario["off_policy_blocked_enforce"] == scenario["off_policy_calls"] == 4
    assert scenario["drift_records"] == 4
    assert not scenario["ssh_key_leaked_enforce"] and scenario["build_dir_intact_enforce"]


def test_legit_calls_still_succeed(scenario):
    assert scenario["legit_succeeded_enforce"] == scenario["legit_calls_enforce"] > 0


def test_cron_visible_reason(scenario):
    assert scenario["cron_visible_reason"]
