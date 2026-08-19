from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deepseek_harness_defaults_to_reme_memory() -> None:
    source = (ROOT / "soul" / "adapters" / "deepseek_harness.ts").read_text(encoding="utf-8")

    assert 'return "soul_reme";' in source
    assert '"legacy"' not in source
    assert "baseUrl}/transition/propose" not in source
    assert "/reme/transition/propose" in source
