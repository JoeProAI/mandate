from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_registrations():
    text = (ROOT / "plugin.yaml").read_text()
    assert 'name: mandate' in text
    assert 'requires_hermes: ">=0.23.0"' in text
    for item in ("mandate_status", "pre_tool_call", "post_tool_call"):
        assert f"  - {item}" in text


def test_register_wires_surfaces():
    from mandate_core.plugin import register

    seen = {"hooks": [], "tools": []}

    class Ctx:
        def register_hook(self, name, cb, **kw):
            seen["hooks"].append(name)

        def register_tool(self, name, **kw):
            seen["tools"].append(name)

        def __getattr__(self, item):
            return lambda *a, **k: None

    register(Ctx())
    assert sorted(seen["hooks"]) == ["post_tool_call", "pre_tool_call"]
    assert seen["tools"] == ["mandate_status"]
