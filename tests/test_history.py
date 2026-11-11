"""propose --learn-from: seed observations from existing Hermes run history."""

import json
import sqlite3
from pathlib import Path

import pytest

from mandate_core import history as H
from mandate_core import policy as P
from tests.test_engine import LEGIT, Harness

JOB_ID = "a1b2c3"
JOB_NAME = "nightly-triage"
SECRET = "ghp_" + "A" * 36
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nCANARY-PRIVATE-KEY\n-----END OPENSSH PRIVATE KEY-----"


def _msgs(calls, run, blocked=()):
    tcs = [{"id": f"{run}-{i}", "type": "function",
            "function": {"name": t, "arguments": json.dumps(a)}} for i, (t, a) in enumerate(calls)]
    rows = [{"role": "user", "content": "run the nightly triage", "timestamp": 1700000000.0 + run},
            {"role": "assistant", "content": "", "tool_calls": tcs}]
    for i, (t, _) in enumerate(calls):
        body = "MANDATE BLOCKED this call." if i in blocked else "ok"
        rows.append({"role": "tool", "tool_call_id": f"{run}-{i}", "tool_name": t, "content": body})
    return rows


def _write_session(home: Path, sid: str, rows, extra_lines=()):
    sdir = home / "sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra_lines)
    (sdir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")


@pytest.fixture
def hermes(tmp_path, sandbox, monkeypatch):
    home = tmp_path / "hermes"
    (home / "cron" / "output" / JOB_ID).mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(json.dumps(
        {"jobs": [{"id": JOB_ID, "name": JOB_NAME, "schedule": "0 3 * * *", "workdir": sandbox["wd"]},
                  {"id": "zzz", "name": "other-job", "schedule": "0 4 * * *"}]}))
    for n in range(3):
        _write_session(home, f"cron_{JOB_ID}_2026110{n + 1}_030000", _msgs(LEGIT, n))
        (home / "cron" / "output" / JOB_ID / f"2026-11-0{n + 1}_03-00-05.md").write_text("# Cron Job\nok\n")
    _write_session(home, "cron_zzz_20261101_040000", _msgs([("terminal", {"command": "rm -rf build"})], 9))
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def h(tmp_path, sandbox):
    return Harness(tmp_path, sandbox["wd"])


def _dump(h):
    return "\n".join(str(tuple(r)) for r in h.eng.store.q("SELECT * FROM observations"))


def test_parser_reads_real_calls_and_skips_bad_rows(tmp_path):
    hist = H.History(label="t")
    rows = _msgs([("terminal", {"command": "ls"}), ("read_file", {"path": "a"})], 0, blocked=(1,))
    rows.append({"role": "assistant", "tool_calls": [{"id": "x1", "function": {"name": "terminal",
                                                                                 "arguments": "{not json"}}]})
    rows.append({"role": "assistant", "tool_calls": [{"id": "x2", "function": {"arguments": "{}"}}]})
    rows.append({"role": "assistant", "tool_calls": "[[broken"})
    rows.append({"role": "assistant", "tool_calls": json.dumps(
        [{"id": "x3", "function": {"name": "search_files", "arguments": {"pattern": "TODO"}}}])})
    rows.append({"role": "assistant", "tool_calls": [{"id": "0-0", "function": {"name": "terminal",
                                                                                  "arguments": '{"command": "ls"}'}}]})
    calls = H.calls_from_messages(rows, hist)
    assert calls == [("terminal", {"command": "ls"}), ("search_files", {"pattern": "TODO"})]
    assert hist.skipped_calls == 2 and hist.skipped_rows == 1 and hist.blocked_calls == 1

    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps(rows[1]) + "\n{truncated\n[1, 2]\n")
    got = H.from_path(f, "f")
    assert got.skipped_rows == 2 and [r.calls for r in got.runs] == [[("terminal", {"command": "ls"}),
                                                                       ("read_file", {"path": "a"})]]


def test_source_grammar(hermes, sandbox):
    for src in ("history", f"cron:{JOB_NAME}", f"cron:{JOB_ID}"):
        hist = H.resolve(src, JOB_NAME)
        assert hist.job_id == JOB_ID and hist.workdir == sandbox["wd"]
        assert [r.session_id for r in hist.runs] == [f"cron_{JOB_ID}_2026110{n}_030000" for n in (1, 2, 3)]
        assert hist.output_runs == 3
    assert len(H.resolve(str(hermes / "sessions"), "x").runs) == 4
    one = H.resolve(str(hermes / "sessions" / f"cron_{JOB_ID}_20261101_030000.jsonl"), "x")
    assert [r.run_id for r in one.runs] == [f"history:cron_{JOB_ID}_20261101_030000"]
    with pytest.raises(H.HistoryError, match="unknown --learn-from source"):
        H.resolve("yesterday", JOB_NAME)
    with pytest.raises(H.HistoryError, match="cron output"):
        H.resolve(str(next((hermes / "cron" / "output" / JOB_ID).glob("*.md"))), JOB_NAME)
    with pytest.raises(H.HistoryError, match="does not exist"):
        H.resolve("./nope.jsonl", JOB_NAME)


def test_state_db_sessions_and_ledger(hermes, sandbox):
    db = sqlite3.connect(hermes / "state.db")
    db.executescript("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL);"
                     "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
                     "content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL);")
    sid = f"cron_{JOB_ID}_20261104_030000"
    db.execute("INSERT INTO sessions VALUES (?, 'cron', 1700000400.0)", (sid,))
    for m in _msgs(LEGIT[:2], 4):
        db.execute("INSERT INTO messages(session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
                   "VALUES (?,?,?,?,?,?,?)", (sid, m["role"], m.get("content"), m.get("tool_call_id"),
                                              json.dumps(m["tool_calls"]) if "tool_calls" in m else None,
                                              m.get("tool_name"), 1700000400.0))
    db.commit()
    db.close()
    ledger = sqlite3.connect(hermes / "cron" / "executions.db")
    ledger.execute("CREATE TABLE executions (id TEXT, job_id TEXT, status TEXT)")
    ledger.executemany("INSERT INTO executions VALUES (?,?,?)", [(f"e{n}", JOB_ID, "done") for n in range(6)])
    ledger.commit()
    ledger.close()
    hist = H.resolve("history", JOB_NAME)
    assert len(hist.runs) == 4 and hist.runs[-1].session_id == sid and hist.runs[-1].started_at == 1700000400.0
    assert hist.ledger_runs == 6


def test_fixture_history_proposes_without_live_runs(h, hermes, capsys):
    assert h.cli("propose", JOB_NAME, "--learn-from", "history") == 0
    out = capsys.readouterr().out
    store = h.eng.store
    job = store.job(JOB_NAME)
    assert job["job_id"] == JOB_ID and job["active_version"] is None and job["mode"] == "observe"
    assert store.active_policy(JOB_ID) is None and store.latest_pending(JOB_ID)["version"] == 1
    assert not [r for r in store.runs(JOB_ID) if r["source"] != "history"]
    pol = P.parse(store.latest_pending(JOB_ID)["yaml"])
    assert pol.observed_runs == 3 and pol.history_runs == 3
    assert "0 observed live, 3 learned from Hermes history" in out
    assert "# learned from history: 12 call(s) in 3 run(s)" in out
    assert '"reports/*.md"  # seen 3x, 3x from history' in out
    assert "# warning: learned history shows what the job did" in out
    assert "# next: hermes mandate approve nightly-triage" in out


def test_distinct_evidence_marking(h, hermes, capsys):
    h.run(JOB_ID, "live-1", LEGIT[:2], name=JOB_NAME)
    assert h.cli("propose", JOB_NAME, "--learn-from", "history") == 0
    store = h.eng.store
    rows = store.q("SELECT run_id, source FROM observations ORDER BY id")
    assert {r["source"] for r in rows if r["run_id"] == "live-1"} == {"hook"}
    assert {r["source"] for r in rows if r["run_id"].startswith("history:")} == {"history"}
    assert {r["source"] for r in store.runs(JOB_ID)} == {"hook", "history"}
    text = store.latest_pending(JOB_ID)["yaml"]
    assert "generated from 4 run(s): 1 observed live, 3 learned from Hermes history." in text
    assert "history_runs: 3" in text
    assert '"git log --oneline -n"  # seen 4x, 3x from history' in text
    assert P.render(P.parse(text)) == text
    capsys.readouterr()
    assert h.cli("status", JOB_NAME, "--no-color") == 0
    assert "4 run(s) observed (3 learned from Hermes history)" in capsys.readouterr().out
    plain = P.render(P.compile_policy("j", {"r": []}, ""))
    assert "history_runs" not in plain and "from history" not in plain


def test_learned_calls_redacted_like_live(h, hermes, sandbox, tmp_path):
    calls = [("write_file", {"path": "reports/a.md", "content": "TOPSECRET-CONTENT " + KEY}),
             ("terminal", {"command": f"curl -H 'Authorization: Bearer {SECRET}' https://api.github.com/x"}),
             ("read_file", {"path": "~/.ssh/id_rsa"}),
             ("terminal", {"command": f"cat /tmp/tmpq8x1/scratch.txt && echo {SECRET}"})]
    rows = _msgs(calls, 7)
    rows[2]["content"] = KEY  # tool result holding file contents: never stored
    _write_session(hermes, f"cron_{JOB_ID}_20261107_030000", rows)
    assert h.cli("propose", JOB_NAME, "--learn-from", "history") == 0
    dump = _dump(h)
    for raw in ("TOPSECRET-CONTENT", SECRET, "CANARY-PRIVATE-KEY", "BEGIN OPENSSH"):
        assert raw not in dump
    learned = [json.loads(r["shape"]) for r in h.eng.store.q(
        "SELECT shape FROM observations WHERE run_id=? ORDER BY id", (f"history:cron_{JOB_ID}_20261107_030000",))]
    (tmp_path / "live").mkdir()
    live = Harness(tmp_path / "live", sandbox["wd"])
    live.run(JOB_ID, "r", calls, name=JOB_NAME)
    assert learned == [json.loads(r["shape"]) for r in live.eng.store.q("SELECT shape FROM observations ORDER BY id")]


def test_deterministic(tmp_path, hermes, sandbox, capsys):
    texts, obs = [], []
    for n in range(2):
        d = tmp_path / f"s{n}"
        d.mkdir()
        hh = Harness(d, sandbox["wd"])
        assert hh.cli("propose", JOB_NAME, "--learn-from", f"cron:{JOB_ID}") == 0
        texts.append(hh.eng.store.latest_pending(JOB_ID)["yaml"])
        obs.append([tuple(r)[1:] for r in hh.eng.store.q(
            "SELECT id, job_id, run_id, ts, tool, shape, outcome, source FROM observations ORDER BY id")])
    assert texts[0] == texts[1] and obs[0] == obs[1]
    # Re-learning the same history replaces those runs instead of piling up evidence.
    capsys.readouterr()
    assert hh.cli("propose", JOB_NAME, "--learn-from", "history") == 0
    assert "# unchanged: identical to v1" in capsys.readouterr().out
    assert len(hh.eng.store.q("SELECT id FROM observations")) == len(obs[1])


def test_no_history_fails_clearly(h, hermes, tmp_path, capsys):
    empty = tmp_path / "empty-hermes"
    empty.mkdir()
    for argv in (["propose", "ghost-job", "--learn-from", "history"],
                 ["propose", "ghost-job", "--learn-from", "cron:ghost-job"],
                 ["propose", "ghost-job", "--learn-from", str(empty)]):
        assert h.cli(*argv) == 1
        err = capsys.readouterr().err
        assert "no usable Hermes history for ghost-job" in err and "Nothing was written" in err
    assert h.cli("propose", "ghost-job", "--learn-from", "bogus") == 1
    assert "history, cron:<job name or id>" in capsys.readouterr().err
    assert h.eng.store.q("SELECT * FROM policies") == [] and h.eng.store.q("SELECT * FROM jobs") == []
    assert h.eng.store.q("SELECT * FROM observations") == []


def test_learn_never_approves_or_enforces(h, hermes, capsys):
    for n in range(3):
        h.run(JOB_ID, f"r{n}", LEGIT[:2], name=JOB_NAME)
    assert h.cli("propose", JOB_NAME) == 0
    assert h.cli("approve", JOB_NAME, "--yes") == 0
    store = h.eng.store
    before = store.job(JOB_NAME)
    assert before["mode"] == "enforce" and before["active_version"] == 1
    assert h.cli("propose", JOB_NAME, "--learn-from", "history") == 0
    after = store.job(JOB_NAME)
    assert after["mode"] == "enforce" and after["active_version"] == 1
    assert store.latest_pending(JOB_ID)["version"] == 2
    assert h.cli("approve", JOB_NAME) == 1  # no TTY under pytest: the --yes gate still holds
    assert "needs a human" in capsys.readouterr().err
    assert store.job(JOB_NAME)["active_version"] == 1

    capsys.readouterr()
    other = store.job("other-job")
    assert other is None
    assert h.cli("propose", "other-job", "--learn-from", "history") == 0
    assert store.job("other-job")["mode"] == "observe" and store.job("other-job")["active_version"] is None
    assert store.active_policy("zzz") is None
