from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deepseek_harness_defaults_to_reme_memory() -> None:
    source = (ROOT / "soul" / "adapters" / "deepseek_harness.ts").read_text(encoding="utf-8")

    assert 'return "soul_reme";' in source
    assert "baseUrl}/transition/propose" not in source
    assert "/reme/transition/propose" not in source
    assert "/evidence/enqueue" in source


def test_deepseek_harness_dsh_plugin_enqueues_turn_stopping() -> None:
    source = (ROOT / "soul" / "adapters" / "dsh_plugin.mjs").read_text(encoding="utf-8")

    assert 'ctx.on("agent/turn-stopping"' in source
    assert "/evidence/enqueue" in source
    assert 'source: "deepseek-harness"' in source
