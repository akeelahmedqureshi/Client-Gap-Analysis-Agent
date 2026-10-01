"""UX deep dive (passive): accessibility, mobile, performance and conversion practices of public pages.

Two layers, both doing only what an ordinary visitor's browser does:

* **Static checks** on the page HTML (always): WCAG basics (language, title, image alt text, form
  labels, link/button names, headings, landmarks, zoom), the mobile viewport, and conversion
  practices (primary call to action, sign-in, help, live chat, trust signals, site search).
* **Browser checks** in headless Chromium (when available): colour contrast, horizontal overflow
  and tap-target size in a phone viewport, small text, and lab page-speed (LCP, page weight,
  request count). Same in-browser SSRF guard as the crawler; a fresh context per page.

Every issue carries the page URL and a short snippet of the offending markup as evidence.
Results are indicative (automated checks find roughly a third of WCAG problems; speed is measured
from the analysis server), and are labelled so.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup

from cip.config import Settings, get_settings

log = logging.getLogger(__name__)

SEVERITY_ORDER = ["high", "medium", "low", "info"]
PENALTY = {"high": 15, "medium": 7, "low": 3, "info": 0}
CATEGORIES = ("accessibility", "mobile", "performance", "conversion")


@dataclass
class UxIssue:
    key: str
    category: str  # accessibility | mobile | performance | conversion
    severity: str
    title: str
    detail: str = ""
    recommendation: str = ""
    wcag: str | None = None  # success criterion, e.g. "1.1.1"
    count: int = 1
    examples: list[str] = field(default_factory=list)
    page: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _snippet(tag, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(tag))
    return s if len(s) <= limit else s[:limit] + "…"


def _text(tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)) if tag else ""


def _accessible_name(tag) -> str:
    if tag.get("aria-label", "").strip() or tag.get("aria-labelledby") or tag.get("title", "").strip():
        return "named"
    txt = _text(tag)
    if txt:
        return txt
    img = tag.find("img", alt=True)
    if img is not None and img.get("alt", "").strip():
        return img["alt"]
    svg = tag.find("svg")
    if svg is not None and (svg.find("title") or svg.get("aria-label")):
        return "svg"
    return ""


# --------------------------------------------------------------------------- static checks

_SKIP_INPUTS = {"hidden", "submit", "button", "image", "reset"}
_VAGUE = re.compile(r"^(click here|here|read more|more|learn more|details)$", re.I)


def static_checks(url: str, html: str) -> list[UxIssue]:
    soup = BeautifulSoup(html, "html.parser")
    issues: list[UxIssue] = []

    def add(key, category, severity, title, recommendation, wcag=None, examples=(), count=1, detail=""):
        issues.append(UxIssue(key=key, category=category, severity=severity, title=title, detail=detail,
                              recommendation=recommendation, wcag=wcag, count=count,
                              examples=list(examples)[:3], page=url))

    root = soup.find("html")
    if root is None or not (root.get("lang") or "").strip():
        add("html-lang", "accessibility", "medium", "Page language not declared",
            'Add lang="…" to the <html> element so screen readers use the right pronunciation.', "3.1.1")
    title = soup.find("title")
    if title is None or not _text(title):
        add("document-title", "accessibility", "medium", "Page has no title",
            "Give every page a unique, descriptive <title>.", "2.4.2")

    viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    content = (viewport.get("content") or "").lower() if viewport else ""
    if viewport is None:
        add("meta-viewport", "mobile", "high", "No mobile viewport",
            'Add <meta name="viewport" content="width=device-width, initial-scale=1"> and a responsive layout.',
            "1.4.10")
    elif "user-scalable=no" in content.replace(" ", "") or re.search(r"maximum-scale=(0?\.\d+|1(\.0)?)\b", content):
        add("zoom-disabled", "accessibility", "medium", "Pinch-zoom is disabled",
            "Remove user-scalable=no / maximum-scale=1 so people with low vision can zoom.", "1.4.4",
            examples=[_snippet(viewport)])

    imgs = [i for i in soup.find_all("img") if not i.has_attr("alt") and i.get("role") != "presentation"]
    if imgs:
        add("image-alt", "accessibility", "high" if len(imgs) >= 5 else "medium",
            f"{len(imgs)} image(s) without alt text", 'Add alt text (or alt="" for decorative images).', "1.1.1",
            examples=[_snippet(i) for i in imgs], count=len(imgs))

    labelled = {lab.get("for") for lab in soup.find_all("label") if lab.get("for")}
    unlabelled = []
    for f in soup.find_all(["input", "select", "textarea"]):
        if f.name == "input" and (f.get("type") or "text").lower() in _SKIP_INPUTS:
            continue
        if f.get("id") in labelled or f.find_parent("label") or f.get("aria-label") or f.get("aria-labelledby") \
                or f.get("title"):
            continue
        unlabelled.append(f)
    if unlabelled:
        add("form-label", "accessibility", "high" if len(unlabelled) >= 3 else "medium",
            f"{len(unlabelled)} form field(s) without a label",
            "Associate a visible <label> with every field (placeholder text is not a label).", "1.3.1 / 4.1.2",
            examples=[_snippet(f) for f in unlabelled], count=len(unlabelled))

    nameless_links = [a for a in soup.find_all("a", href=True) if not _accessible_name(a)]
    if nameless_links:
        add("link-name", "accessibility", "medium", f"{len(nameless_links)} link(s) without an accessible name",
            "Give icon-only links an aria-label or visually hidden text.", "2.4.4 / 4.1.2",
            examples=[_snippet(a) for a in nameless_links], count=len(nameless_links))
    nameless_buttons = [b for b in soup.find_all("button") if not _accessible_name(b)]
    if nameless_buttons:
        add("button-name", "accessibility", "medium", f"{len(nameless_buttons)} button(s) without an accessible name",
            "Give icon-only buttons an aria-label.", "4.1.2",
            examples=[_snippet(b) for b in nameless_buttons], count=len(nameless_buttons))
    vague = [a for a in soup.find_all("a", href=True) if _VAGUE.match(_text(a))]
    if len(vague) >= 2:
        add("vague-links", "accessibility", "low", f"{len(vague)} links with vague text (\"read more\", \"click here\")",
            "Use link text that says where the link goes.", "2.4.4", examples=[_snippet(a) for a in vague],
            count=len(vague))

    levels = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    if levels and 1 not in levels:
        add("no-h1", "accessibility", "low", "No main heading (h1)", "Start each page with one h1 describing it.",
            "1.3.1")
    skips = [(a, b) for a, b in zip(levels, levels[1:]) if b > a + 1]
    if skips:
        add("heading-order", "accessibility", "low", "Heading levels are skipped",
            "Keep heading levels sequential (h2 after h1, h3 after h2…).", "1.3.1",
            examples=[f"h{a} → h{b}" for a, b in skips], count=len(skips))
    if soup.find("main") is None and soup.find(attrs={"role": "main"}) is None:
        add("landmark-main", "accessibility", "low", "No main landmark",
            "Wrap the primary content in <main> so keyboard and screen-reader users can jump to it.", "1.3.1 / 2.4.1")
    return issues


# --------------------------------------------------------------------------- conversion practices

CTA_SELF_SERVE = re.compile(r"\b(sign ?up|get started|start (your )?(free|trial|now)|try (it )?(for )?free|"
                            r"free trial|create (an |your )?account|join (now|free)|start free)\b", re.I)
CTA_SALES = re.compile(r"\b(book|request|schedule|get) (a |your )?(demo|call|consultation)|contact sales|talk to sales\b",
                       re.I)
SIGN_IN = re.compile(r"\b(log ?in|sign ?in|my account)\b", re.I)
HELP = re.compile(r"\b(help( center| centre)?|support|faq|knowledge base|docs|documentation)\b", re.I)
CHAT_SCRIPTS = re.compile(r"intercom|drift\.com|crisp\.chat|zdassets|zendesk|hs-scripts|hubspot.*conversations|"
                          r"tawk\.to|livechatinc|freshchat|olark|tidio|gorgias", re.I)
TRUST = re.compile(r"\b(testimonial|case stud(y|ies)|trusted by|our customers|customer stories|reviews?|rated|"
                   r"soc ?2|hipaa|gdpr|iso 27001|g2|capterra|trustpilot)\b", re.I)

PRACTICES = {
    "self_serve_cta": "Self-serve sign-up / trial call to action",
    "sales_cta": "Book-a-demo / contact-sales call to action",
    "sign_in": "Sign-in link for existing customers",
    "help": "Help center / FAQ / docs link",
    "live_chat": "Live chat widget",
    "trust": "Trust signals (customers, reviews, compliance)",
    "search": "Site search",
}


def detect_practices(url: str, html: str) -> dict[str, str | None]:
    """practice -> evidence snippet (None when absent)."""
    soup = BeautifulSoup(html, "html.parser")
    actions = soup.find_all(["a", "button"])

    def first(pattern) -> str | None:
        for t in actions:
            txt = _text(t) or t.get("aria-label", "")
            if txt and len(txt) <= 60 and pattern.search(txt):
                return _snippet(t, 120)
        return None

    scripts = " ".join((s.get("src") or "") + " " + (s.string or "")[:2000] for s in soup.find_all("script"))
    chat = CHAT_SCRIPTS.search(scripts)
    body = _text(soup.body) if soup.body else ""
    trust = TRUST.search(body)
    search = soup.find("input", attrs={"type": "search"}) or soup.find(attrs={"role": "search"})
    return {
        "self_serve_cta": first(CTA_SELF_SERVE),
        "sales_cta": first(CTA_SALES),
        "sign_in": first(SIGN_IN),
        "help": first(HELP),
        "live_chat": f"script: {chat.group(0)}" if chat else None,
        "trust": body[max(0, trust.start() - 60): trust.end() + 60] if trust else None,
        "search": _snippet(search, 120) if search else None,
    }


def conversion_issues(url: str, practices: dict[str, str | None]) -> list[UxIssue]:
    out = []
    if not practices.get("self_serve_cta") and not practices.get("sales_cta"):
        out.append(UxIssue("primary-cta", "conversion", "high", "No clear call to action on the homepage",
                           recommendation="Add one prominent primary action (start a trial / sign up, or book a demo) "
                                          "above the fold.", page=url))
    if not practices.get("help"):
        out.append(UxIssue("help-link", "conversion", "low", "No help center, FAQ or docs link",
                           recommendation="Link self-service help from the main navigation or footer.", page=url))
    if not practices.get("trust"):
        out.append(UxIssue("trust-signals", "conversion", "low", "No visible trust signals",
                           recommendation="Show customer logos, testimonials, ratings or compliance badges.", page=url))
    return out


# --------------------------------------------------------------------------- browser checks

AUDIT_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return {r:p[0], g:p[1], b:p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parse(cs.backgroundColor); if (c && c.a > 0.95) return c; }
    return {r:255, g:255, b:255, a:1}; };
  const snip = (el) => el.outerHTML.replace(/\s+/g, ' ').slice(0, 140);
  const contrast = []; let checked = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (checked >= 400) break;
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').trim();
    if (!own || !visible(el)) continue;
    checked++;
    const cs = getComputedStyle(el); const fg = parse(cs.color); const bg = bgOf(el);
    if (!fg || !bg) continue;
    const L1 = lum(fg), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(cs.fontSize); const bold = parseInt(cs.fontWeight) >= 700;
    const large = size >= 24 || (bold && size >= 18.66);
    if (ratio < (large ? 3 : 4.5)) contrast.push({text: own.slice(0, 60), ratio: Math.round(ratio * 100) / 100,
      color: cs.color, background: `rgb(${bg.r}, ${bg.g}, ${bg.b})`, html: snip(el)});
  }
  const small = []; const targets = [];
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && visible(el) && parseFloat(getComputedStyle(el).fontSize) < 12) small.push(snip(el));
  }
  for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, [role=button]')) {
    if (!visible(el)) continue;
    if (el.tagName === 'A' && el.closest('p, li') && el.closest('p, li').textContent.trim().length > el.textContent.trim().length + 20) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 24 || r.height < 24) targets.push({w: Math.round(r.width), h: Math.round(r.height), html: snip(el)});
  }
  const sw = document.scrollingElement ? document.scrollingElement.scrollWidth : document.body.scrollWidth;
  return {contrast, contrastChecked: checked, smallText: small.slice(0, 50), smallTextCount: small.length,
          targets: targets.slice(0, 50), targetCount: targets.length, overflow: sw > window.innerWidth + 2,
          scrollWidth: sw, viewportWidth: window.innerWidth};
}
"""

LCP_JS = """
() => new Promise((resolve) => {
  let lcp = null;
  try { new PerformanceObserver((list) => { const e = list.getEntries(); if (e.length) lcp = e[e.length - 1].startTime; })
        .observe({type: 'largest-contentful-paint', buffered: true}); } catch (e) {}
  setTimeout(() => { const nav = performance.getEntriesByType('navigation')[0] || {};
    resolve({lcp, dcl: nav.domContentLoadedEventEnd || null, load: nav.loadEventEnd || null}); }, 600);
})
"""


class BrowserUxAuditor:
    """Runs the in-browser checks for one URL (desktop for contrast/speed, phone for mobile checks)."""

    def __init__(self, settings: Settings | None = None, check_public: bool = True) -> None:
        from cip.connectors.research.browser import BrowserRenderer

        self.settings = settings or get_settings()
        self.renderer = BrowserRenderer(self.settings, check_public)

    @property
    def available(self) -> bool:
        return self.renderer.available

    def session(self):
        return self.renderer.session()

    async def _context(self, browser, mobile: bool):
        kw = dict(user_agent=self.settings.crawler_user_agent, java_script_enabled=True, accept_downloads=False,
                  service_workers="block")
        if mobile:
            kw.update(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        else:
            kw.update(viewport={"width": 1366, "height": 900})
        context = await browser.new_context(**kw)

        async def guard(route):
            # Images and fonts load (they matter for speed and contrast); media and non-public hosts don't.
            if route.request.resource_type == "media" or not await self.renderer._allowed(route.request.url):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", guard)
        return context

    async def audit(self, url: str) -> dict | None:
        if not self.available:
            return None
        try:
            browser = await self.renderer._ensure_browser()
        except Exception:  # noqa: BLE001
            return None
        timeout_ms = int(self.settings.crawler_timeout_seconds * 1000)
        result: dict = {"url": url}
        async with self.renderer._sem:
            for mobile in (False, True):
                context = await self._context(browser, mobile)
                try:
                    page = await context.new_page()
                    sizes: list[int] = []
                    requests = [0]

                    async def finished(req):
                        requests[0] += 1
                        try:
                            sizes.append((await req.sizes()).get("responseBodySize", 0))
                        except Exception:  # noqa: BLE001
                            pass

                    page.on("requestfinished", finished)
                    resp = await page.goto(url, wait_until="load", timeout=timeout_ms)
                    if resp is None or resp.status >= 400 or not await self.renderer._allowed(page.url):
                        return None
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 6000))
                    except Exception:  # noqa: BLE001
                        pass
                    checks = await page.evaluate(AUDIT_JS)
                    if mobile:
                        result["mobile"] = {k: checks[k] for k in ("overflow", "scrollWidth", "viewportWidth",
                                                                   "targets", "targetCount", "smallText",
                                                                   "smallTextCount")}
                    else:
                        timing = await page.evaluate(LCP_JS)
                        result["desktop"] = {"contrast": checks["contrast"][:50],
                                             "contrastCount": len(checks["contrast"]),
                                             "contrastChecked": checks["contrastChecked"]}
                        result["performance"] = {**timing, "requests": requests[0], "bytes": sum(sizes)}
                except Exception as exc:  # noqa: BLE001 - audits are best-effort
                    log.info("UX browser audit failed for %s: %s", url, exc)
                    return None
                finally:
                    await context.close()
        return result


def browser_issues(audit: dict) -> list[UxIssue]:
    url = audit["url"]
    out: list[UxIssue] = []
    d, m, p = audit.get("desktop", {}), audit.get("mobile", {}), audit.get("performance", {})
    n = d.get("contrastCount", 0)
    if n:
        ex = [f"“{c['text']}” {c['ratio']}:1 ({c['color']} on {c['background']})" for c in d["contrast"][:3]]
        out.append(UxIssue("color-contrast", "accessibility", "high" if n >= 10 else "medium",
                           f"{n} text element(s) below the WCAG contrast minimum",
                           recommendation="Raise text/background contrast to at least 4.5:1 (3:1 for large text).",
                           wcag="1.4.3", count=n, examples=ex, page=url))
    if m.get("overflow"):
        out.append(UxIssue("mobile-overflow", "mobile", "high", "Page scrolls sideways on a phone",
                           detail=f"Content is {m['scrollWidth']}px wide in a {m['viewportWidth']}px viewport.",
                           recommendation="Make the layout responsive (fluid widths, wrapping tables/images).",
                           wcag="1.4.10", page=url))
    if m.get("targetCount", 0) >= 3:
        ex = [f"{t['w']}×{t['h']}px {t['html']}" for t in m["targets"][:3]]
        out.append(UxIssue("tap-targets", "mobile", "medium", f"{m['targetCount']} tap target(s) smaller than 24×24px",
                           recommendation="Make buttons and links at least 24×24px (44×44px recommended) on touch "
                                          "screens.", wcag="2.5.8", count=m["targetCount"], examples=ex, page=url))
    if m.get("smallTextCount", 0) >= 5:
        out.append(UxIssue("small-text", "mobile", "low", f"{m['smallTextCount']} text element(s) under 12px on a phone",
                           recommendation="Use at least 16px body text on mobile.", count=m["smallTextCount"],
                           examples=m["smallText"][:3], page=url))
    lcp = p.get("lcp")
    if lcp is not None and lcp > 2500:
        out.append(UxIssue("lcp", "performance", "high" if lcp > 4000 else "medium",
                           f"Largest contentful paint {lcp / 1000:.1f}s",
                           detail="Lab measurement from the analysis server (Core Web Vitals threshold 2.5s).",
                           recommendation="Optimise the hero image/fonts, reduce render-blocking scripts, use a CDN.",
                           page=url))
    mb = (p.get("bytes") or 0) / 1_000_000
    if mb > 3:
        out.append(UxIssue("page-weight", "performance", "high" if mb > 6 else "medium", f"Page weighs {mb:.1f} MB",
                           detail=f"{p.get('requests', 0)} requests.",
                           recommendation="Compress images (WebP/AVIF), lazy-load below the fold, trim scripts.",
                           page=url))
    elif (p.get("requests") or 0) > 150:
        out.append(UxIssue("request-count", "performance", "low", f"{p['requests']} requests on load",
                           recommendation="Bundle and defer non-critical scripts.", page=url))
    return out


# --------------------------------------------------------------------------- scoring


def score(issues: list[UxIssue], categories: set[str]) -> dict:
    """Per category: 100 minus penalties (each issue key counted once per category); overall = mean."""
    out = {}
    for cat in CATEGORIES:
        if cat not in categories:
            continue
        seen: dict[str, str] = {}
        for i in issues:
            if i.category == cat:
                prev = seen.get(i.key)
                if prev is None or SEVERITY_ORDER.index(i.severity) < SEVERITY_ORDER.index(prev):
                    seen[i.key] = i.severity
        out[cat] = max(0, 100 - sum(PENALTY[s] for s in seen.values()))
    overall = round(sum(out.values()) / len(out)) if out else None
    return {"categories": out, "overall": overall,
            "method": "Per category 100 minus penalties (high 15, medium 7, low 3; each check counted once "
                      "across pages); overall = average of the categories measured."}
