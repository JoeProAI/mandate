"""Build proof/proof.json and proof/PROOF.md from generated artifacts only (junit xml, scenario json)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    cases = [c for s in suites for c in s.iter("testcase")]

    def status(c):
        if c.find("failure") is not None or c.find("error") is not None:
            return "failed"
        if c.find("skipped") is not None:
            return "skipped"
        return "passed"

    esc = [c for c in cases if c.get("classname", "").endswith("test_adversarial")
           and c.get("name", "").startswith(("test_shell_escape", "test_path_escape", "test_host_escape"))]
    return {"total": len(cases), "passed": sum(status(c) == "passed" for c in cases),
            "failed": sum(status(c) == "failed" for c in cases), "skipped": sum(status(c) == "skipped" for c in cases),
            "escape_cases": len(esc), "escape_caught": sum(status(c) == "passed" for c in esc)}


def _ver(tag: str) -> tuple:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def _readme_unchanged_outside_proof() -> bool:
    start, end = "<!-- proof:start -->", "<!-- proof:end -->"
    head = subprocess.run(["git", "show", "HEAD:README.md"], cwd=ROOT, capture_output=True, text=True).stdout
    cur = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""

    def strip(t: str) -> str:
        return t[:t.index(start)] + t[t.index(end):] if start in t and end in t else t
    return strip(head) == strip(cur)


def _timing(run: dict) -> str:
    parts = []
    for kind, label in (("warm", "Hermes already installed"), ("cold", "fresh Hermes clone and install")):
        e = run.get("prove_seconds", {}).get(kind)
        if e:
            parts.append(f"{e['seconds']} s {kind} ({label})")
    return "; ".join(parts) or "not measured"


def main() -> int:
    tag = sys.argv[1]
    junit_path, scen_path, validate_path = map(Path, sys.argv[2:5])
    scen = json.loads(scen_path.read_text())
    tests = junit(junit_path)
    validate = validate_path.read_text()
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    # proof/ and the generated README block are outputs of this script, so they do not count as dirty.
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", ".", ":!proof", ":!README.md"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()) or not _readme_unchanged_outside_proof()
    out = ROOT / "proof"
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_file = runs_dir / f"{tag}.json"
    prev = json.loads(run_file.read_text()) if run_file.exists() else {}
    timings = {k: v for k, v in prev.get("prove_seconds", {}).items()}
    secs, kind = os.environ.get("MANDATE_PROVE_SECONDS"), os.environ.get("MANDATE_PROVE_INSTALL")
    if secs and kind:
        timings[kind] = {"seconds": float(secs), "plugin_commit": sha}
    run = {
        "hermes_tag": tag,
        "hermes_version": scen["hermes_version"],
        "plugin_commit": sha,
        "plugin_tree_dirty": dirty,
        "validate_passed": "Validation passed" in validate,
        "tests": tests,
        "scenario": {k: scen[k] for k in (
            "observe_runs", "observe_blocked", "proposal_rules", "off_policy_calls", "off_policy_executed_observe",
            "off_policy_executed_enforce", "off_policy_blocked_enforce", "legit_calls_enforce",
            "legit_succeeded_enforce", "cron_visible_reason", "ssh_key_leaked_enforce", "build_dir_intact_enforce",
            "drift_records")},
        "hook_overhead_ms": scen["hook_ms"],
        "prove_seconds": timings,
        "proposal": scen["proposal"],
        "drift": scen["drift"],
        "leash_line": scen["leash_line"],
    }
    # Raw call text stays out of the JSON: one-line JSON strings join drift rows the validator's scan then flags.
    details = ("proposal", "drift", "leash_line")
    run_file.write_text(json.dumps({k: v for k, v in run.items() if k not in details}, indent=2) + "\n")
    runs = sorted((json.loads(f.read_text()) for f in runs_dir.glob("v*.json")), key=lambda r: _ver(r["hermes_tag"]))
    (out / "proof.json").write_text(json.dumps({"releases": runs}, indent=2) + "\n")

    def row(label, fn):
        return f"| {label} | " + " | ".join(fn(r) for r in runs) + " |"

    def frac(a, b):
        return lambda r: f"{r['scenario'][a]}/{r['scenario'][b]}"
    head = "| Item | " + " | ".join(f"Hermes {r['hermes_tag']}" for r in runs) + " |"
    rows = [
        head, "|---|" + "---|" * len(runs),
        row("Hermes", lambda r: r["hermes_version"]),
        row("Plugin commit", lambda r: f"`{r['plugin_commit'][:12]}`" + (" (uncommitted changes)" if r["plugin_tree_dirty"] else "")),
        row("`hermes plugins validate . --install-deps`", lambda r: "passed" if r["validate_passed"] else "FAILED"),
        row("Tests", lambda r: f"{r['tests']['passed']} passed, {r['tests']['failed']} failed, {r['tests']['skipped']} skipped (of {r['tests']['total']})"),
        row("Escape cases caught", lambda r: f"{r['tests']['escape_caught']}/{r['tests']['escape_cases']}"),
        row("Observe runs (blocked calls)", lambda r: f"{r['scenario']['observe_runs']} ({r['scenario']['observe_blocked']})"),
        row("Proposed rules", lambda r: str(r["scenario"]["proposal_rules"])),
        row("Off-policy calls executed, observe only", frac("off_policy_executed_observe", "off_policy_calls")),
        row("Off-policy calls executed, enforce", frac("off_policy_executed_enforce", "off_policy_calls")),
        row("Legit calls succeeded, enforce", frac("legit_succeeded_enforce", "legit_calls_enforce")),
        row("Drift records", lambda r: str(r["scenario"]["drift_records"])),
        row("Cron-visible block reason", lambda r: str(r["scenario"]["cron_visible_reason"])),
        row("SSH key canary leaked (enforce)", lambda r: str(r["scenario"]["ssh_key_leaked_enforce"])),
        row("build/ intact (enforce)", lambda r: str(r["scenario"]["build_dir_intact_enforce"])),
        row("pre_tool_call dispatch, Mandate enforcing, p50 / p95",
            lambda r: f"{r['hook_overhead_ms']['enforce_p50']} ms / {r['hook_overhead_ms']['enforce_p95']} ms ({r['hook_overhead_ms']['samples']} samples)"),
        row("pre_tool_call dispatch, no job scope, p50 / p95",
            lambda r: f"{r['hook_overhead_ms']['no_scope_p50']} ms / {r['hook_overhead_ms']['no_scope_p95']} ms"),
        row("`make prove` wall clock", _timing),
    ]
    last = run
    md = f"""# Mandate proof

Generated by `make prove` (one Hermes release per run; `make prove-all` runs every supported release).
Every number below is read from `proof/proof.json` (per-release files in `proof/runs/`).

{chr(10).join(rows)}

`make prove` wall clock is measured by `scripts/prove.sh` from its first line to the end of pytest, on the
VM that generated this file. "warm" means Hermes for that tag was already installed; "cold" means the run
cloned and installed it.

The scenario loads Mandate through Hermes's own plugin loader, binds real cron execution identities
(`cron.execution_identity.enter_cron_execution`) for `nightly-triage`, and sends each call through
`model_tools.handle_function_call`, so the real `pre_tool_call` path decides. Legit calls really run
(terminal, read_file, search_files, write_file) in a throwaway git repo with a fake HOME.

Sections below are from the Hermes {last['hermes_tag']} run.

## Proposal (from the 3 observed runs)

```yaml
{last['proposal'].rstrip()}
```

## Drift inbox after the poisoned run

```
{last['drift'].rstrip()}
```

## Leash Line

```
{last['leash_line'].rstrip()}
```
"""
    (out / "PROOF.md").write_text(md)
    readme = ROOT / "README.md"
    start, end = "<!-- proof:start -->", "<!-- proof:end -->"
    if readme.exists() and start in readme.read_text():
        text = readme.read_text()
        lines = []
        for r in runs:
            t, s, h = r["tests"], r["scenario"], r["hook_overhead_ms"]
            lines.append(
                f"- Hermes {r['hermes_tag']}: validate {'passed' if r['validate_passed'] else 'FAILED'}; "
                f"tests {t['passed']} passed, {t['failed']} failed; escape cases caught {t['escape_caught']}/{t['escape_cases']}; "
                f"off-policy calls executed {s['off_policy_executed_observe']}/{s['off_policy_calls']} in observe mode, "
                f"{s['off_policy_executed_enforce']}/{s['off_policy_calls']} with Mandate enforcing; legit calls succeeded "
                f"while enforcing {s['legit_succeeded_enforce']}/{s['legit_calls_enforce']}; `pre_tool_call` with Mandate "
                f"enforcing p50 {h['enforce_p50']} ms, p95 {h['enforce_p95']} ms; `make prove` {_timing(r)}")
        block = (f"{start}\n" + "\n".join(lines) + "\n\n"
                 f"Proposal after 3 observed runs (Hermes {last['hermes_tag']}; the workdir is the scenario's throwaway repo):\n\n"
                 f"```yaml\n{last['proposal'].rstrip()}\n```\n\n"
                 f"Leash Line after the poisoned run, no-color fallback:\n\n```\n{last['leash_line'].rstrip()}\n```\n"
                 f"{end}")
        text = text[:text.index(start)] + block + text[text.index(end) + len(end):]
        rs, re_ = "<!-- runtime:start -->", "<!-- runtime:end -->"
        if rs in text and re_ in text:
            times = "\n".join(f"- Hermes {r['hermes_tag']}: {_timing(r)}" for r in runs)
            rblock = (f"{rs}\n`make prove` wall clock on the build VM (validate, full pytest, real-Hermes scenario, "
                      f"hook benchmark; from [proof/proof.json](proof/proof.json)):\n\n{times}\n{re_}")
            text = text[:text.index(rs)] + rblock + text[text.index(re_) + len(re_):]
        readme.write_text(text)
    print(md)
    t, s = tests, run["scenario"]
    ok = (run["validate_passed"] and t["failed"] == 0 and s["off_policy_executed_enforce"] == 0
          and s["legit_succeeded_enforce"] == s["legit_calls_enforce"] and t["escape_caught"] == t["escape_cases"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
