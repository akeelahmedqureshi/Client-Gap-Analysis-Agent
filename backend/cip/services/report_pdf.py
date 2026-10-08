"""Client-ready PDF export of the Markdown report (headless Chromium).

The report embeds text extracted from third-party websites, so the HTML is
rendered with JavaScript disabled and every network request blocked: any
markup that slipped into scraped text cannot execute or fetch anything.
"""

from __future__ import annotations

import html as html_lib
import re

import markdown as md_lib

from cip.config import Settings, get_settings

# Status icons used in the Markdown tables -> coloured dots that render in any font.
_ICONS = {
    "✅": '<span class="dot ok" title="available">●</span>',
    "🟡": '<span class="dot partial" title="partial">●</span>',
    "❌": '<span class="dot missing" title="confirmed missing">●</span>',
    "❔": '<span class="dot unknown" title="not publicly identified">○</span>',
}

CSS = """
@page { size: A4; margin: 18mm 14mm 18mm 14mm; }
body { font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif; font-size: 9.5pt; color: #1e293b;
       line-height: 1.45; }
h1 { font-size: 20pt; color: #312e81; margin: 0 0 4pt; }
h2 { font-size: 14pt; color: #312e81; border-bottom: 1.5pt solid #c7d2fe; padding-bottom: 3pt; margin-top: 18pt;
     page-break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 12pt; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.5pt; }
th, td { border: 0.5pt solid #cbd5e1; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #eef2ff; }
tr { page-break-inside: avoid; }
blockquote { border-left: 3pt solid #a5b4fc; background: #eef2ff; margin: 8pt 0; padding: 4pt 8pt; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; }
a { color: #4338ca; text-decoration: none; word-break: break-all; }
em { color: #475569; }
.dot { font-size: 11pt; }
.dot.ok { color: #059669; } .dot.partial { color: #d97706; } .dot.missing { color: #dc2626; }
.dot.unknown { color: #64748b; }
details > summary { font-weight: bold; }
.diagram { margin: 6pt 0 12pt; page-break-inside: avoid; }
.diagram svg { width: 100%; height: auto; }
"""


_DIAGRAM_BLOCK = re.compile(r"<!-- architecture-diagram:(\w+) -->.*?<!-- /architecture-diagram -->", re.S)


def report_html(markdown_text: str, title: str, architecture: dict | None = None) -> str:
    # Mermaid needs JavaScript (disabled for PDFs): swap each diagram block for the server-rendered SVG.
    def swap(m: re.Match[str]) -> str:
        svg = (architecture or {}).get(f"svg_{m.group(1)}")
        return f'\n<div class="diagram">{svg}</div>\n' if svg else ""

    markdown_text = _DIAGRAM_BLOCK.sub(swap, markdown_text)
    body = md_lib.markdown(markdown_text, extensions=["tables", "sane_lists"])
    for icon, span in _ICONS.items():
        body = body.replace(icon, span)
    body = re.sub(r"<details>", "<details open>", body)  # collapsed sections must print expanded
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html_lib.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


class PdfUnavailable(RuntimeError):
    pass


async def render_pdf(html: str, title: str, settings: Settings | None = None) -> bytes:
    s = settings or get_settings()
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise PdfUnavailable("PDF export needs the browser extra: pip install '.[browser]' "
                             "and `playwright install chromium`") from exc
    async with async_playwright() as pw:
        kwargs = {"headless": True, "args": ["--disable-dev-shm-usage"]}
        if s.browser_executable:
            kwargs["executable_path"] = s.browser_executable
        try:
            browser = await pw.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise PdfUnavailable(f"Could not start the headless browser: {exc}") from exc
        try:
            context = await browser.new_context(java_script_enabled=False, accept_downloads=False,
                                                service_workers="block")
            await context.route("**/*", lambda route: route.abort())  # fully offline rendering
            page = await context.new_page()
            await page.set_content(html, wait_until="domcontentloaded")
            footer = ("<div style='font-size:7pt;color:#64748b;width:100%;padding:0 14mm;"
                      "display:flex;justify-content:space-between'>"
                      f"<span>{html_lib.escape(title)[:120]}</span>"
                      "<span><span class='pageNumber'></span> / <span class='totalPages'></span></span></div>")
            return await page.pdf(format="A4", print_background=True, display_header_footer=True,
                                  header_template="<div></div>", footer_template=footer,
                                  margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"})
        finally:
            await browser.close()
