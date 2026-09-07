from pathlib import Path


FORBIDDEN_MARKERS = (
    "Math.random()",
    "generateCandlesForSymbol",
)


def test_no_ai_studio_synthetic_market_backend_in_active_tree():
    for root in (Path("server"), Path("frontend"), Path("dashboard_react")):
        if not root.exists():
            continue
        for path in root.rglob("*.ts"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not any(marker in text for marker in FORBIDDEN_MARKERS), f"Synthetic market backend detected: {path}"
