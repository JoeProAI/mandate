"""`hermes mandate ...` subcommands."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from collections import defaultdict

from . import policy as P
from .engine import Engine
from .normalize import CallShape
from .paths import PathRef, generalize_read, generalize_write, glob_match
from .render import leash_line, stamp, use_color, Paint, ALLOW, DENY, PENDING, DIM

COMMANDS = ("watch", "list", "propose", "approve", "undo", "drift", "widen", "status", "export", "doctor",
            "enforce", "observe")


def setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="mandate_cmd")
    for name, help_ in [("watch", "start observing a job (one command to begin)"), ("propose", "compile a mandate"),
                        ("approve", "approve the pending mandate"), ("undo", "roll back to the previous version"),
                        ("drift", "list blocked calls"), ("status", "Leash Line for a job"),
                        ("export", "print the active mandate"), ("enforce", "switch a job to enforce"),
                        ("observe", "switch a job back to observe")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("job")
        if name == "approve":
            p.add_argument("--yes", action="store_true", help="approve without the interactive prompt")
            p.add_argument("--observe", action="store_true", help="approve but keep the job in observe mode")
        if name == "export":
            p.add_argument("--json", action="store_true")
        if name == "status":
            p.add_argument("--no-color", action="store_true")
    w = sub.add_parser("widen", help="propose a new version that allows a drift record")
    w.add_argument("job")
    w.add_argument("drift_id", type=int)
    w.add_argument("--pattern", action="store_true", help="allow a broader rule and preview what it newly allows")
    sub.add_parser("list", help="list known jobs")
    sub.add_parser("doctor", help="check database, hooks and mode")


def dispatch(args: argparse.Namespace, eng: Engine) -> int:
    cmd = getattr(args, "mandate_cmd", None)
    if not cmd:
        print("usage: hermes mandate {" + ",".join(COMMANDS) + "} ...")
        return 2
    try:
        return int(globals()["cmd_" + cmd](args, eng) or 0)
    except UserError as exc:
        print(f"mandate: {exc}", file=sys.stderr)
        return 1


class UserError(Exception):
    pass


def _job(eng: Engine, ref: str):
    row = eng.store.job(ref)
    if row is None:
        raise UserError(f"unknown job {ref!r}. Run `hermes mandate watch {ref}` first, or `hermes mandate list`.")
    return row


def _name(job) -> str:
    return job["job_name"] or job["job_id"]


def _shapes_by_run(eng: Engine, job_id: str) -> tuple[dict[str, list[CallShape]], str]:
    runs: dict[str, list[CallShape]] = defaultdict(list)
    roots: dict[str, int] = defaultdict(int)
    for row in eng.store.observations(job_id):
        data = json.loads(row["shape"])
        roots[data.get("root", "")] += 1
        runs[row["run_id"]].append(CallShape.from_json(data))
    root = sorted(roots.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if roots else ""
    return dict(runs), root


def cmd_watch(args, eng: Engine) -> int:
    job = eng.store.watch(args.job)
    print(f"Watching {args.job} in observe mode. Nothing will be blocked.")
    print("Let it run at least 3 times, then: hermes mandate propose " + args.job)
    if job["job_id"].startswith("name:"):
        print("(bound by name: the first cron run of a job with this name adopts it)")
    return 0


def cmd_list(args, eng: Engine) -> int:
    for j in eng.store.jobs():
        runs = len(eng.store.runs(j["job_id"]))
        print(f"{_name(j):<24} {j['mode'] or eng.default_mode():<8} v{j['active_version'] or '-':<3} "
              f"{runs} run(s)  id={j['job_id']}")
    return 0


def cmd_propose(args, eng: Engine) -> int:
    job = _job(eng, args.job)
    runs, root = _shapes_by_run(eng, job["job_id"])
    if not runs:
        raise UserError(f"no observed calls for {args.job} yet")
    pol = P.compile_policy(_name(job), runs, root)
    text = P.render(pol)
    sha = P.content_hash(text)
    store = eng.store
    for existing in (store.latest_pending(job["job_id"]), store.active_policy(job["job_id"])):
        if existing is not None and existing["sha256"] == sha:
            print(text, end="")
            print(f"# unchanged: identical to v{existing['version']} sha256:{sha[:12]}")
            return 0
    version = store.add_policy(job["job_id"], text, sha, "propose")
    print(text, end="")
    print(f"# proposed v{version} sha256:{sha[:12]}  ({pol.rule_count()} rules from {len(runs)} run(s))")
    if len(runs) < P.MIN_RUNS:
        print(f"# warning: only {len(runs)} run(s) observed; {P.MIN_RUNS}+ give a more reliable mandate")
    for line in prompt_path_warnings(pol, _job_prompt(job["job_id"])):
        print(line)
    print(f"# next: hermes mandate approve {args.job}")
    return 0


_PROMPT_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:md|txt|json|csv|html|log|yaml|yml|xml))(?![\w/])")


def _job_prompt(job_id: str) -> str:
    """The cron job's prompt, when Hermes's cron store is importable. Best effort: never fails propose."""
    try:
        from cron.jobs import get_job  # type: ignore
        job = get_job(job_id) or {}
        return str(job.get("prompt") or "")
    except Exception:
        return ""


def prompt_path_warnings(pol: P.Policy, prompt: str) -> list[str]:
    """Warn when the job prompt names a file the learned write rules do not cover. That is how a wrong learned
    path (the model wrote status.md, the prompt says reports/status.md) gets caught before approval."""
    if not prompt or not pol.writes:
        return []
    named = sorted(set(_PROMPT_PATH.findall(prompt)))
    inside_writes = [r for r in pol.writes if not r.value.startswith("/")]
    out = []
    for path in named:
        ref = PathRef(path.lstrip("./") if path.startswith("./") else path, True)
        if any(glob_match(r.value, ref) for r in inside_writes):
            continue
        if any(glob_match(r.value, ref) for r in pol.reads):
            continue  # an input the job reads, not an output
        out.append(path)
    if not out:
        return []
    return [f"# warning: the job prompt names {', '.join(out)}, which no learned write rule covers. "
            f"Learned writes: {', '.join(r.value for r in inside_writes) or 'none'}. "
            "Check the write paths before approving."]


def _diff(old: str, new: str, color: bool) -> str:
    paint = Paint(color)
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), "active", "proposed", lineterm="", n=1):
        if line.startswith("+") and not line.startswith("+++"):
            out.append(paint(line, ALLOW))
        elif line.startswith("-") and not line.startswith("---"):
            out.append(paint(line, DENY))
        else:
            out.append(paint(line, DIM))
    return "\n".join(out)


def cmd_approve(args, eng: Engine) -> int:
    job = _job(eng, args.job)
    store = eng.store
    pending = store.latest_pending(job["job_id"])
    if pending is None:
        raise UserError(f"nothing pending for {args.job}. Run `hermes mandate propose {args.job}` first.")
    P.parse(pending["yaml"])
    active = store.active_policy(job["job_id"])
    color = use_color()
    print(_diff(active["yaml"] if active else "", pending["yaml"], color))
    if not args.yes:
        if not sys.stdin.isatty():
            raise UserError("approval needs a human: run interactively or pass --yes")
        answer = input(f"Approve mandate v{pending['version']} for {args.job}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Not approved.")
            return 1
    store.approve(job["job_id"], pending["version"])
    if not args.observe:
        store.set_mode(job["job_id"], "enforce")
    print(stamp(_name(job), pending["version"], pending["sha256"], color))
    mode = "observe (complain only)" if args.observe else "enforce"
    print(f"{args.job} now runs under mandate v{pending['version']} in {mode} mode.")
    return 0


def cmd_undo(args, eng: Engine) -> int:
    job = _job(eng, args.job)
    prev = eng.store.undo(job["job_id"])
    if prev is None:
        print(f"{args.job}: no earlier approved version; the job is back in observe mode with no mandate.")
    else:
        print(f"{args.job}: rolled back to mandate v{prev}.")
    return 0


def drift_text(eng: Engine, job, color: bool | None = None) -> str:
    paint = Paint(use_color() if color is None else color)
    rows = eng.store.drift(job["job_id"])
    if not rows:
        return f"No drift for {_name(job)}."
    out = [f"Drift inbox for {_name(job)} ({len(rows)} record(s), newest first)"]
    for d in rows:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d["ts"]))
        out.append(f"  {paint('#' + str(d['id']), DENY, True):<6} {when}  run {d['run_id'][:12]}  "
                   f"[{d['family']}] {d['status']}")
        out.append(f"        {d['call']}")
        out.append(paint(f"        {d['reason'][:160]}", DIM))
    return "\n".join(out)


def cmd_drift(args, eng: Engine) -> int:
    print(drift_text(eng, _job(eng, args.job)))
    return 0


def _broaden(family: str, subject: str, shape: dict) -> str:
    if family in ("paths.read", "paths.write"):
        inside = any(r[0] == subject and r[1] for r in shape.get(family.split(".")[1] + "s", []))
        if inside:
            return generalize_read(subject) if family == "paths.read" else generalize_write(subject)
        parent = subject.rsplit("/", 1)[0] or "/"
        return parent.rstrip("/") + "/*"
    if family == "commands":
        if subject.startswith("="):
            return subject
        toks = [t for t in subject.split() if not t.startswith("-")]
        return " ".join(toks[:1] + ["*", "-*"])
    if family == "hosts":
        parts = subject.split(":")[0].split(".")
        return "*." + ".".join(parts[-2:]) if len(parts) > 2 else subject
    return subject


def cmd_widen(args, eng: Engine) -> int:
    job = _job(eng, args.job)
    store = eng.store
    d = store.drift_item(job["job_id"], args.drift_id)
    if d is None:
        raise UserError(f"no drift #{args.drift_id} for {args.job}")
    base_row = store.latest_pending(job["job_id"]) or store.active_policy(job["job_id"])
    if base_row is None:
        raise UserError("no mandate to widen")
    old = P.parse(base_row["yaml"])
    new = P.parse(base_row["yaml"])
    shape = json.loads(d["shape"])
    sh = CallShape.from_json(shape)
    # Widen every failing aspect of the drifted call, not only the first.
    violations = P.check(sh, new, 0)
    if not violations:
        print(f"drift #{d['id']} is already allowed by v{base_row['version']}")
        return 0
    for v in violations:
        value = _broaden(v.family, v.subject, shape) if args.pattern else v.subject
        target = {"tools": new.tools, "paths.read": new.reads, "paths.write": new.writes,
                  "commands": new.commands, "hosts": new.hosts}.get(v.family)
        if v.family == "limits":
            new.max_writes_per_run = (new.max_writes_per_run or 0) + max(1, sh.write_count)
        elif target is not None and value not in {r.value for r in target}:
            target.append(P.Rule(value, 1))
            target.sort(key=lambda r: r.value)
        print(f"  + {v.family}: {value}")
    text = P.render(new)
    version = store.add_policy(job["job_id"], text, P.content_hash(text), f"widen #{d['id']}",
                               "pattern" if args.pattern else "exact")
    store.set_drift_status(d["id"], f"widen v{version}")
    print(f"Proposed v{version} (pending). Nothing changes until: hermes mandate approve {args.job}")
    if args.pattern:
        newly = []
        for row in store.q("SELECT id, shape, outcome FROM observations WHERE job_id=? ORDER BY id",
                           (job["job_id"],)):
            s = CallShape.from_json(json.loads(row["shape"]))
            if P.check(s, old, 0) and not P.check(s, new, 0):
                newly.append(s.summary)
        print(f"Preview: the broader rule newly allows {len(newly)} past call(s):")
        for s in sorted(set(newly))[:20]:
            print(f"    {s}")
    return 0


def status_text(eng: Engine, job, color: bool | None = None) -> str:
    store = eng.store
    row = store.active_policy(job["job_id"])
    pend = store.latest_pending(job["job_id"])
    active = eng.parsed(row) if row else None
    pending = P.parse(pend["yaml"]) if pend else None
    mode = job["mode"] or eng.default_mode()
    return leash_line(_name(job), active, row["version"] if row else None, row["sha256"] if row else None, mode,
                      pending, store.drift(job["job_id"]), len(store.runs(job["job_id"])), color)


def cmd_status(args, eng: Engine) -> int:
    print(status_text(eng, _job(eng, args.job), False if args.no_color else None))
    return 0


def cmd_export(args, eng: Engine) -> int:
    job = _job(eng, args.job)
    row = eng.store.active_policy(job["job_id"])
    if row is None:
        raise UserError(f"{args.job} has no approved mandate")
    if args.json:
        pol = P.parse(row["yaml"])
        print(json.dumps({"version": row["version"], "sha256": row["sha256"], "job": pol.job,
                          "families": {k: [[r.value, r.evidence] for r in v] for k, v in pol.families().items()},
                          "max_writes_per_run": pol.max_writes_per_run}, indent=2))
    else:
        print(row["yaml"], end="")
    return 0


def cmd_enforce(args, eng: Engine) -> int:
    job = _job(eng, args.job)
    if eng.store.active_policy(job["job_id"]) is None:
        raise UserError("approve a mandate before enforcing")
    eng.store.set_mode(job["job_id"], "enforce")
    print(f"{args.job}: enforce")
    return 0


def cmd_observe(args, eng: Engine) -> int:
    eng.store.set_mode(_job(eng, args.job)["job_id"], "observe")
    print(f"{args.job}: observe (nothing will be blocked)")
    return 0


def cmd_doctor(args, eng: Engine) -> int:
    ok = True
    checks = []
    try:
        counts = eng.store.counts()
        checks.append((True, f"database {eng.store.path} readable: {counts}"))
        eng.store.x("CREATE TABLE IF NOT EXISTS _doctor(x)")
        eng.store.x("DROP TABLE _doctor")
        checks.append((True, "database writable"))
        for j in eng.store.jobs():
            row = eng.store.active_policy(j["job_id"])
            if row is not None:
                good = P.content_hash(row["yaml"]) == row["sha256"]
                checks.append((good, f"{_name(j)} v{row['version']} hash {'matches' if good else 'MISMATCH'}"))
    except Exception as exc:
        checks.append((False, f"database error: {type(exc).__name__}: {exc}"))
    checks.append((eng.hooks_registered, "pre_tool_call and post_tool_call hooks registered"))
    checks.append((True, f"default mode: {eng.default_mode()}; unattended violations: block"))
    for good, text in checks:
        ok &= good
        print(("ok   " if good else "FAIL ") + text)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Standalone entry (`python -m mandate_core.cli`), same store as the plugin."""
    from .store import Store

    parser = argparse.ArgumentParser(prog="mandate")
    setup(parser)
    args = parser.parse_args(argv)
    eng = Engine(Store, lambda k, d: d, lambda: None, lambda: "")
    eng.hooks_registered = False
    return dispatch(args, eng)


if __name__ == "__main__":
    raise SystemExit(main())
