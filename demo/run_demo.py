"""Driver for demo/run_demo.sh. Reuses the integration scenario setup; prints the story."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import integration_scenario as sc  # noqa: E402

PAUSE = float(os.environ.get("DEMO_PAUSE", "1.2"))
NO_COLOR = bool(os.environ.get("NO_COLOR"))


def c(text: str, rgb: tuple[int, int, int], bold: bool = False) -> str:
    if NO_COLOR:
        return text
    return ("\x1b[1m" if bold else "") + f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\x1b[0m"


G, R, Y, T, D = (0x12, 0xE0, 0x8A), (0xFF, 0x4D, 0x2E), (0xF5, 0xC5, 0x42), (0xE8, 0xEE, 0xF8), (0x6B, 0x7A, 0x99)


def say(text: str, pause: float = 1.0) -> None:
    print(c(text, T, True), flush=True)
    time.sleep(PAUSE * pause)


def cmd(env: dict, *args: str, pause: float = 1.0) -> None:
    print(c("$ hermes mandate " + " ".join(args), D), flush=True)
    exe = Path(sys.executable).with_name("hermes")
    subprocess.run([str(exe), "mandate", *args], env=env, check=False)
    time.sleep(PAUSE * pause)


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="mandate-demo-"))
    s = sc.setup(base)
    env = {**s["env"], "MANDATE_FORCE_COLOR": os.environ.get("MANDATE_FORCE_COLOR", "")}
    env.pop("NO_COLOR", None) if not NO_COLOR else None
    os.environ.update(s["env"])
    os.chdir(s["wd"])
    from hermes_cli.plugins import discover_plugins
    from cron.execution_identity import enter_cron_execution, exit_cron_execution
    import model_tools

    discover_plugins(force=True)

    def fire(run: str, calls: list, n: int) -> None:
        token = enter_cron_execution(sc.JOB, run, {"source": "builtin"})
        try:
            for i, (tool, args) in enumerate(calls):
                a = sc._fmt(args, n)
                out = model_tools.handle_function_call(tool, a, task_id=run, tool_call_id=f"{run}-{i}",
                                                       session_id=f"cron_{run}")
                label = a.get("command") or a.get("path") or a.get("pattern")
                if "MANDATE BLOCKED" in out:
                    print("   " + c("\u2715 BLOCKED ", R, True) + c(f"{tool}: {label}", R), flush=True)
                else:
                    print("   " + c("\u2713 ", G) + c(f"{tool}: {label}", D), flush=True)
                time.sleep(PAUSE * 0.15)
        finally:
            exit_cron_execution(token)

    say("A cron job, nightly-triage, reads the repo and writes a triage report. Mandate watches it.")
    cmd(env, "watch", "nightly-triage")
    for n in range(3):
        say(f"Cron run {n + 1} of 3 (observe mode, nothing is blocked)", 0.4)
        fire(f"exec-{n}", sc.LEGIT, n)
    say("Compile what the job actually did into a mandate:")
    cmd(env, "propose", "nightly-triage", pause=2.5)
    say("A human approves it:")
    cmd(env, "approve", "nightly-triage", "--yes", pause=2.0)
    say("Run 4: the issue text carried a prompt injection. The agent tries off-mandate calls.")
    fire("exec-3", sc.LEGIT[:3] + sc.OFF_POLICY + sc.LEGIT[3:], 3)
    time.sleep(PAUSE)
    say("Legit work finished. Off-mandate calls never ran:")
    cmd(env, "status", "nightly-triage", pause=3.0)
    cmd(env, "drift", "nightly-triage", pause=2.0)
    say("Next week the job legitimately needs `make test`. It is blocked, then widened:")
    fire("exec-4", [("terminal", {"command": "make -n test"})], 4)
    rows = subprocess.run([str(Path(sys.executable).with_name("hermes")), "mandate", "drift", "nightly-triage"],
                          env={**env, "NO_COLOR": "1", "MANDATE_FORCE_COLOR": ""}, capture_output=True, text=True)
    newest = rows.stdout.split("#", 1)[1].split()[0] if "#" in rows.stdout else "1"
    cmd(env, "widen", "nightly-triage", newest)
    cmd(env, "approve", "nightly-triage", "--yes", pause=1.5)
    fire("exec-5", [("terminal", {"command": "make -n test"})], 5)
    cmd(env, "status", "nightly-triage", pause=2.0)
    shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
