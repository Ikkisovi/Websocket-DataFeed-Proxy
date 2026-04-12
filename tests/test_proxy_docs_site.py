import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_proxy_docs_site import build_proxy_docs_site  # noqa: E402


def test_build_proxy_docs_site_renders_docs_and_lookup_panel(tmp_path):
    project_root = tmp_path / "repo"
    docs_dir = project_root / "docs"
    reports_dir = docs_dir / "reports"
    reports_dir.mkdir(parents=True)

    (docs_dir / "ec2_alpaca_proxy_api.md").write_text(
        "# Proxy API\n\n## Section One\n\n- item one\n- item two\n\n|A|B|\n|---|---|\n|1|2|\n",
        encoding="utf-8",
    )
    (docs_dir / "cloud-ws-usage.md").write_text("# WS Usage\n\nBody text.\n", encoding="utf-8")
    (reports_dir / "2026-04-12.html").write_text("<html><body>report</body></html>", encoding="utf-8")

    output_dir = tmp_path / "site"
    result = build_proxy_docs_site(project_root, output_dir)

    index_html = (output_dir / "index.html").read_text(encoding="utf-8")
    ws_html = (output_dir / "cloud-ws-usage.html").read_text(encoding="utf-8")

    assert "Public Docs Site" in index_html
    assert "Internal lookup" in index_html
    assert "WeChat token lookup" in index_html
    assert "Lookup / assign token" in index_html
    assert "Proxy API" in index_html
    assert "Section One" in index_html
    assert "WS Usage" in ws_html
    assert (output_dir / "reports" / "2026-04-12.html").exists()
    assert result["reports_copied"] == 1
