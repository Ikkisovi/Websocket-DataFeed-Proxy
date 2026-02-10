from pathlib import Path


def test_run_strategy_has_no_relay_mode():
    repo_root = Path(__file__).resolve().parents[2]
    content = (repo_root / "run_strategy.ps1").read_text(encoding="utf-8")
    assert "Relay (Local -> Cloud)" not in content
    assert "ALPACA_PROXY_MODE=relay" not in content
    assert "CLOUD_PROXY_URL" not in content
    assert "CLOUD_HISTORY_URL" not in content
