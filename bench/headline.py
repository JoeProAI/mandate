"""One-line headline from bench/results (written by score.py and replay.py)."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    live = json.loads((HERE / "results/live.json").read_text())["by_model_arm"]
    for key, c in sorted(live.items()):
        model, arm = key.split("|")
        if arm != "mandate":
            continue
        base = live.get(f"{model}|hermes_default", {})
        blocked = c.get("attack_blocked_mandate", 0) + c.get("attack_blocked_plugin", 0) + c.get("attack_blocked_hermes", 0)
        print(f"{model} on real Hermes cron, 10 jobs: Mandate blocked {blocked} of {c.get('attack_calls', 0)} injected "
              f"attack calls the model made ({c.get('attack_runs_harm', 0)} attack runs with a harmful effect; "
              f"Hermes defaults alone: {base.get('attack_runs_harm', 0)}), and falsely blocked "
              f"{c.get('clean_false_blocks', 0)} of {c.get('clean_calls', 0)} legit calls in clean runs.")
    for p in sorted((HERE / "results").glob("replay-*.json")):
        r = json.loads(p.read_text())
        h = r["pre_tool_call_ms_with_mandate"]
        print(f"replay ({r['model']}): {r['decisions_matching_live']}/{r['decisions_total']} Mandate decisions reproduced "
              f"without a key; {r['policies_identical_to_live']}/{r['policies_total']} policies rebuilt identical; "
              f"pre_tool_call p50 {h['p50']} ms, p95 {h['p95']} ms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
