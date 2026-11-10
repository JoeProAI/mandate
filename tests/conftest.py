import os
from pathlib import Path

import pytest


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A job workdir with a symlink escape, plus a fake HOME holding a canary key."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("CANARY-PRIVATE-KEY\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside secret\n")
    wd = tmp_path / "repo"
    (wd / "src" / "pkg").mkdir(parents=True)
    (wd / "src" / "a.py").write_text("print('a')\n")
    (wd / "src" / "pkg" / "b.py").write_text("print('b')\n")
    (wd / "reports").mkdir()
    (wd / "build").mkdir()
    os.symlink(outside, wd / "link_out")
    monkeypatch.setenv("HOME", str(home))
    return {"wd": str(wd), "home": str(home), "outside": str(outside)}
