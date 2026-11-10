import random

from mandate_core import policy as P
from mandate_core.normalize import normalize_call
from mandate_core.shell import parse

CALLS = [
    ("terminal", {"command": "gh issue list --json number,title --limit 20"}),
    ("terminal", {"command": "git log --oneline -n 5 && git status --short"}),
    ("read_file", {"path": "src/a.py"}),
    ("write_file", {"path": "reports/triage.md", "content": "x"}),
    ("web_extract", {"urls": ["https://api.github.com/x"]}),
    ("terminal", {"command": "bash -c 'make test'"}),
]


def _runs(wd, seed=None):
    runs = {}
    for n in range(3):
        calls = list(CALLS)
        if seed is not None:
            random.Random(seed + n).shuffle(calls)
        runs[f"r{n}"] = [normalize_call(t, a, wd) for t, a in calls]
    if seed is not None:
        runs = dict(sorted(runs.items(), key=lambda kv: random.Random(seed).random()))
    return runs


def test_byte_identical_yaml(sandbox):
    wd = sandbox["wd"]
    first = P.render(P.compile_policy("nightly-triage", _runs(wd), wd))
    for seed in range(5):
        assert P.render(P.compile_policy("nightly-triage", _runs(wd, seed), wd)) == first
    assert P.content_hash(first) == P.content_hash(first)


def test_roundtrip(sandbox):
    wd = sandbox["wd"]
    text = P.render(P.compile_policy("nightly-triage", _runs(wd), wd))
    again = P.render(P.parse(text))
    assert again == text


def test_evidence_counts_and_rules(sandbox):
    wd = sandbox["wd"]
    pol = P.compile_policy("nightly-triage", _runs(wd), wd)
    text = P.render(pol)
    assert '"gh issue list --json --limit"  # seen 3x' in text
    assert '"=bash -c \'make test\'"  # seen 3x' in text
    assert '"reports/*.md"  # seen 3x' in text
    assert "max_writes_per_run: 1" in text
    assert all(r.evidence > 0 for fam in pol.families().values() for r in fam)


def test_verbatim_rule_allows_exact_opaque_only(sandbox):
    wd = sandbox["wd"]
    pol = P.compile_policy("j", _runs(wd), wd)
    ok = normalize_call("terminal", {"command": "bash -c 'make test'"}, wd)
    bad = normalize_call("terminal", {"command": "bash -c 'make test; rm -rf /'"}, wd)
    assert P.check(ok, pol) == []
    assert P.check(bad, pol)


def test_parse_segments():
    segs = parse("A=1 git -C repo log --oneline -n5 | head -20 > out.txt 2>/dev/null")
    assert [s.words for s in segs] == [("git", "repo"), ("head",)]
    assert segs[0].env == ("A",)
    assert segs[1].redirects == ((">", "out.txt"), ("2>", "/dev/null"))
    assert segs[1].is_write
    assert parse("echo ';' && ls")[0].positionals == (";",)
    assert parse("echo hi # rm -rf /")[0].positionals == ("hi",)


def test_parse_rejects_bad_policy():
    import pytest

    with pytest.raises(P.PolicyParseError):
        P.parse("job: x\n")
    with pytest.raises(P.PolicyParseError):
        P.parse("mandate: 1\nsurprise: 1\n")
