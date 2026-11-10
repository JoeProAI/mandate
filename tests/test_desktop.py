import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mandate_core.store import Store
from tests.test_engine import BAD, Harness, learn

ROOT = Path(__file__).resolve().parents[1]
fastapi = pytest.importorskip("fastapi")


def _api(monkeypatch, db):
    import importlib.util

    monkeypatch.setenv("MANDATE_DB", str(db))
    spec = importlib.util.spec_from_file_location("mandate_dashboard_api", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def populated(tmp_path, sandbox):
    h = Harness(tmp_path, sandbox["wd"])
    learn(h)
    h.run("nightly-triage", "r3", BAD)
    did = h.eng.store.drift("nightly-triage")[0]["id"]
    assert h.cli("widen", "nightly-triage", str(did)) == 0
    h.run("other-job", "o1", [("terminal", {"command": "ls"})])
    return h, tmp_path / "m.db"


def test_api_matches_core_and_is_read_only(monkeypatch, populated):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mandate_core import policy as P
    from mandate_core.render import TRACKS, _rules

    h, db = populated
    api = _api(monkeypatch, db)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/mandate")
    c = TestClient(app)
    before = db.read_bytes()
    jobs = c.get("/api/plugins/mandate/jobs").json()["jobs"]
    assert {j["name"] for j in jobs} == {"nightly-triage", "other-job"}
    nt = next(j for j in jobs if j["name"] == "nightly-triage")
    assert nt["version"] == 1 and nt["pending_version"] == 2 and nt["open_drift"] == len(BAD) - 1
    d = c.get("/api/plugins/mandate/jobs/nightly-triage").json()
    assert c.get("/api/plugins/mandate/jobs/nope").status_code == 404
    store = h.eng.store
    active = P.parse(store.active_policy(d["job"]["id"])["yaml"])
    for t in d["tracks"]:
        assert [(r["value"], r["evidence"]) for r in t["allowed"]] == [(r.value, r.evidence) for r in _rules(active, t["name"])]
    assert [t["name"] for t in d["tracks"]] == list(TRACKS)
    assert sum(len(t["notches"]) for t in d["tracks"]) == len(BAD)
    assert d["diff"].startswith("--- v1") and "+" in d["diff"] and d["pending"]["version"] == 2
    assert len(d["drift"]) == len(BAD) and {x["status"] for x in d["drift"]} == {"open", "widen v2"}
    assert db.read_bytes() == before
    with pytest.raises(Exception):
        api._connect().execute("DELETE FROM drift")


def test_api_without_db(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path / "absent.db")
    assert api.jobs_payload() == {"jobs": [], "db": "missing"}
    assert api.job_payload("x") is None
    assert not (tmp_path / "absent.db").exists()


REACT = os.environ.get("MANDATE_REACT_DIR") or str(Path.home() / ".cache/mandate-desktop-test/node_modules")


@pytest.mark.skipif(not shutil.which("node") or not Path(REACT, "react-dom").exists(),
                    reason="node + react-dom needed (npm install react react-dom into MANDATE_REACT_DIR)")
def test_desktop_page_renders(monkeypatch, populated, tmp_path):
    _, db = populated
    api = _api(monkeypatch, db)
    fx = {"/jobs": api.jobs_payload(), "/jobs/nightly-triage": api.job_payload("nightly-triage")}
    (tmp_path / "fx.json").write_text(json.dumps(fx))
    out = subprocess.run(["node", "--import", "./register.mjs", "render_test.mjs", str(tmp_path / "fx.json")],
                         cwd=ROOT / "tests" / "desktop", capture_output=True, text=True,
                         env={**os.environ, "MANDATE_REACT_DIR": REACT}, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["id"] == "mandate" and set(r["areas"]) == {"routes", "sidebar.nav", "palette"}
    assert r["navigated"] == ["/mandate"] and r["disposers"] == 1
    html = r["html"]
    for needle in ("nightly-triage", "other-job", "ENFORCING", "Leash Line", "Drift timeline", "\u25bc#",
                   "\u25b3#", "v2 (pending)", "#12E08A", "#FF4D2E", "#F5C542", "aria-selected=\"true\""):
        assert needle in html, needle
