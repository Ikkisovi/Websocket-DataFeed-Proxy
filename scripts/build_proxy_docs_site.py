#!/usr/bin/env python3
"""Build the Alpaca proxy docs site.

The site is intentionally self-contained so it can be served:

* from GitHub Pages as a static docs site
* from a private admin deployment that exposes the same token lookup API

The landing page renders the proxy API markdown and includes an internal-only
WeChat-to-token lookup form for the private admin deployment.
"""

from __future__ import annotations

import argparse
import shutil
from html import escape
from pathlib import Path
from textwrap import dedent

import markdown


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "proxy" / "cloud" / "docs-site"
DOCS_DIR = PROJECT_ROOT / "docs"
REPORTS_DIR = DOCS_DIR / "reports"
PROXY_DOC_MD = DOCS_DIR / "ec2_alpaca_proxy_api.md"
WS_USAGE_MD = DOCS_DIR / "cloud-ws-usage.md"

MARKDOWN_EXTENSIONS = [
    "fenced_code",
    "tables",
    "sane_lists",
    "attr_list",
    "md_in_html",
]


def render_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    return markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS, output_format="html5")


def _copy_report_site(source_dir: Path, target_dir: Path) -> int:
    if not source_dir.exists():
        return 0
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for existing in list(target_dir.glob("*.html")):
        existing.unlink()
    for source_path in sorted(source_dir.glob("*.html")):
        shutil.copy2(source_path, target_dir / source_path.name)
        copied += 1
    return copied


def _build_nav_links() -> str:
    return dedent(
        """
        <nav class="site-nav" aria-label="Site navigation">
          <a href="./">Proxy API</a>
          <a href="./cloud-ws-usage.html">WS usage</a>
          <a href="./reports/">Reports</a>
        </nav>
        """
    ).strip()


def _build_lookup_panel() -> str:
    return dedent(
        """
        <section class="lookup-panel" aria-labelledby="lookup-title">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Internal lookup</p>
              <h2 id="lookup-title">WeChat token lookup</h2>
            </div>
            <p class="panel-note">
              The lookup section is visible in the public docs site.
              The button is active only in the private admin deployment that can reach the token lookup API.
            </p>
          </div>
          <form id="lookup-form" class="lookup-form">
            <label class="field">
              <span>WeChat ID</span>
              <input id="wechat-id" name="wechat_id" type="text" autocomplete="off" placeholder="wxid..." />
            </label>
            <div class="field actions">
              <button id="lookup-submit" type="submit">Lookup / assign token</button>
              <button id="lookup-copy" type="button" disabled>Copy token</button>
            </div>
          </form>
          <p id="lookup-status" class="lookup-status">Enter a WeChat ID to resolve or create a token.</p>
          <pre id="lookup-result" class="lookup-result" aria-live="polite">{}</pre>
        </section>
        """
    ).strip()


def _build_lookup_script() -> str:
    return dedent(
        """
        <script>
        (() => {
          const lookupForm = document.getElementById('lookup-form');
          const statusEl = document.getElementById('lookup-status');
          const resultEl = document.getElementById('lookup-result');
          const submitButton = document.getElementById('lookup-submit');
          const copyButton = document.getElementById('lookup-copy');
          const wechatInput = document.getElementById('wechat-id');
          const internalHosts = new Set(['35.88.155.223', 'localhost', '127.0.0.1']);
          const lookupEnabled = internalHosts.has(window.location.hostname) || window.location.port === '8768';
          const apiPath = '/v1/admin/token/lookup';
          let latestToken = '';

          function renderResult(payload) {
            resultEl.textContent = JSON.stringify(payload, null, 2);
            latestToken = payload && payload.token ? String(payload.token) : '';
            copyButton.disabled = !latestToken;
          }

          if (!lookupEnabled) {
            submitButton.disabled = true;
            wechatInput.disabled = true;
            statusEl.textContent = 'Token lookup is disabled on this host. Open the private admin deployment to resolve tokens.';
            renderResult({ available: false, host: window.location.hostname });
            return;
          }

          lookupForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const wechatId = wechatInput.value.trim();
            if (!wechatId) {
              statusEl.textContent = 'WeChat ID is required.';
              return;
            }

            submitButton.disabled = true;
            statusEl.textContent = 'Looking up token...';

            try {
              const response = await fetch(apiPath, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wechat_id: wechatId, create_missing: true }),
              });
              const payload = await response.json();
              if (!response.ok) {
                throw new Error(payload && payload.error ? payload.error : `HTTP ${response.status}`);
              }
              renderResult(payload);
              statusEl.textContent = payload.created ? 'Token created and stored.' : 'Token resolved from the registry.';
            } catch (error) {
              renderResult({ error: String(error) });
              statusEl.textContent = 'Lookup failed.';
            } finally {
              submitButton.disabled = false;
            }
          });

          copyButton.addEventListener('click', async () => {
            if (!latestToken) return;
            try {
              await navigator.clipboard.writeText(latestToken);
              statusEl.textContent = 'Token copied to clipboard.';
            } catch (error) {
              statusEl.textContent = `Copy failed: ${error}`;
            }
          });
        })();
        </script>
        """
    ).strip()


def _build_page(title: str, body_html: str, include_lookup: bool = False) -> str:
    lookup_panel = _build_lookup_panel() if include_lookup else ""
    lookup_script = _build_lookup_script() if include_lookup else ""
    return dedent(
        f"""
        <!DOCTYPE html>
        <html lang="zh-Hans">
        <head>
          <meta charset="UTF-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <meta name="color-scheme" content="light" />
          <title>{escape(title)}</title>
          <style>
            :root {{
              --bg: #f6f8fb;
              --surface: #ffffff;
              --surface-alt: #f8fafc;
              --border: #d0d7de;
              --text: #1f2328;
              --muted: #57606a;
              --accent: #0969da;
              --accent-soft: #dbeafe;
              --success: #1a7f37;
              --warning: #9a6700;
            }}
            * {{
              box-sizing: border-box;
            }}
            html {{
              scroll-behavior: smooth;
            }}
            body {{
              margin: 0;
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
              background: var(--bg);
              color: var(--text);
              line-height: 1.6;
            }}
            a {{
              color: var(--accent);
              text-decoration: none;
            }}
            a:hover {{
              text-decoration: underline;
            }}
            .page {{
              max-width: 1240px;
              margin: 0 auto;
              padding: 24px 18px 48px;
            }}
            .site-header {{
              display: grid;
              gap: 14px;
              padding: 20px;
              margin-bottom: 18px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: var(--surface);
            }}
            .eyebrow {{
              margin: 0;
              color: var(--muted);
              font-size: 0.82rem;
              text-transform: uppercase;
              letter-spacing: 0;
            }}
            .site-header h1 {{
              margin: 0;
              font-size: 1.65rem;
              line-height: 1.2;
            }}
            .site-header p {{
              margin: 0;
              color: var(--muted);
            }}
            .site-nav {{
              display: flex;
              flex-wrap: wrap;
              gap: 10px;
            }}
            .site-nav a {{
              display: inline-flex;
              align-items: center;
              min-height: 34px;
              padding: 0 12px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: var(--surface-alt);
              color: var(--text);
            }}
            .site-nav a:hover {{
              border-color: var(--accent);
              text-decoration: none;
            }}
            .lookup-panel {{
              margin: 18px 0 24px;
              padding: 18px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: var(--surface);
            }}
            .panel-header {{
              display: flex;
              flex-wrap: wrap;
              justify-content: space-between;
              gap: 12px;
              margin-bottom: 14px;
            }}
            .panel-header h2 {{
              margin: 4px 0 0;
              font-size: 1.18rem;
            }}
            .panel-note {{
              max-width: 600px;
              margin: 0;
              color: var(--muted);
            }}
            .lookup-form {{
              display: grid;
              grid-template-columns: minmax(0, 1fr) auto;
              gap: 12px;
              align-items: end;
            }}
            .field {{
              display: grid;
              gap: 6px;
            }}
            .field span {{
              font-size: 0.88rem;
              color: var(--muted);
            }}
            .field input {{
              width: 100%;
              min-width: 280px;
              height: 40px;
              padding: 0 12px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: #fff;
              color: var(--text);
            }}
            .actions {{
              grid-auto-flow: column;
              justify-content: start;
              gap: 8px;
            }}
            button {{
              height: 40px;
              padding: 0 14px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: var(--surface-alt);
              color: var(--text);
              cursor: pointer;
            }}
            button:hover:not(:disabled) {{
              border-color: var(--accent);
            }}
            button:disabled {{
              cursor: not-allowed;
              opacity: 0.55;
            }}
            .lookup-status {{
              margin: 10px 0 8px;
              color: var(--muted);
            }}
            .lookup-result {{
              margin: 0;
              padding: 14px;
              min-height: 120px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: #0f172a;
              color: #e2e8f0;
              overflow-x: auto;
              white-space: pre-wrap;
              word-break: break-word;
            }}
            .doc-shell {{
              padding: 20px;
              border: 1px solid var(--border);
              border-radius: 8px;
              background: var(--surface);
            }}
            .doc-shell h1,
            .doc-shell h2,
            .doc-shell h3,
            .doc-shell h4 {{
              line-height: 1.25;
            }}
            .doc-shell h1 {{
              margin-top: 0;
            }}
            .doc-shell p {{
              margin: 0.6rem 0;
            }}
            .doc-shell ul,
            .doc-shell ol {{
              padding-left: 1.35rem;
            }}
            .doc-shell code {{
              font-family: Consolas, 'SFMono-Regular', monospace;
              font-size: 0.92em;
            }}
            .doc-shell pre {{
              overflow-x: auto;
              padding: 14px;
              border-radius: 8px;
              border: 1px solid var(--border);
              background: #0f172a;
              color: #e2e8f0;
            }}
            .doc-shell pre code {{
              color: inherit;
            }}
            .doc-shell table {{
              width: 100%;
              border-collapse: collapse;
              overflow: hidden;
              border-radius: 8px;
            }}
            .doc-shell th,
            .doc-shell td {{
              padding: 8px 10px;
              border: 1px solid var(--border);
              text-align: left;
              vertical-align: top;
            }}
            .doc-shell th {{
              background: #f1f5f9;
            }}
            .doc-shell blockquote {{
              margin: 1rem 0;
              padding: 0.2rem 0 0.2rem 1rem;
              border-left: 4px solid var(--accent-soft);
              color: var(--muted);
            }}
            .doc-shell hr {{
              border: 0;
              border-top: 1px solid var(--border);
              margin: 1.5rem 0;
            }}
            .doc-shell img {{
              max-width: 100%;
              height: auto;
            }}
            .doc-shell .footnote {{
              color: var(--muted);
              font-size: 0.9rem;
            }}
            .doc-shell details {{
              margin: 1rem 0;
            }}
            .doc-shell summary {{
              cursor: pointer;
            }}
            @media (max-width: 760px) {{
              .page {{
                padding: 16px 12px 32px;
              }}
              .lookup-form {{
                grid-template-columns: 1fr;
              }}
              .actions {{
                grid-auto-flow: row;
              }}
              .field input {{
                min-width: 0;
              }}
            }}
          </style>
        </head>
        <body>
          <div class="page">
            <header class="site-header">
              <div>
                <p class="eyebrow">Public Docs Site</p>
                <h1>{escape(title)}</h1>
                <p>Rendered from the repository markdown sources and published as a static GitHub Pages site.</p>
              </div>
              {_build_nav_links()}
            </header>
            {lookup_panel}
            <article class="doc-shell">
              {body_html}
            </article>
          </div>
          {lookup_script}
        </body>
        </html>
        """
    ).strip()


def build_proxy_docs_site(project_root: Path, output_dir: Path) -> dict[str, Path]:
    docs_dir = project_root / "docs"
    reports_dir = docs_dir / "reports"
    proxy_doc = docs_dir / "ec2_alpaca_proxy_api.md"
    ws_doc = docs_dir / "cloud-ws-usage.md"

    if not proxy_doc.exists():
        raise FileNotFoundError(f"Missing proxy docs markdown: {proxy_doc}")
    if not ws_doc.exists():
        raise FileNotFoundError(f"Missing WS usage markdown: {ws_doc}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    proxy_html = render_markdown(proxy_doc)
    ws_html = render_markdown(ws_doc)

    index_html = _build_page("Alpaca Proxy API", proxy_html, include_lookup=True)
    ws_page_html = _build_page("Cloud WS Usage", ws_html, include_lookup=False)

    index_path = output_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")

    ws_path = output_dir / "cloud-ws-usage.html"
    ws_path.write_text(ws_page_html, encoding="utf-8")

    copied_reports = _copy_report_site(reports_dir, output_dir / "reports")

    return {
        "index": index_path,
        "ws_usage": ws_path,
        "reports_dir": output_dir / "reports",
        "reports_copied": copied_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Alpaca proxy docs site")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Repository root containing docs/ and proxy/cloud/",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for the generated static site",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = Path(args.output).resolve()
    result = build_proxy_docs_site(project_root, output_dir)

    print(f"[build_proxy_docs_site] Wrote {result['index']}")
    print(f"[build_proxy_docs_site] Wrote {result['ws_usage']}")
    print(f"[build_proxy_docs_site] Copied {result['reports_copied']} report files")


if __name__ == "__main__":
    main()
