"""Source-quality tiers (BRS 28.1; PRD 10.24): how authoritative a piece of evidence's source is.

1. Official product, documentation or pricing source (and the code itself);
2. Official company announcement or case study;
3. Trusted third-party source (app stores, vulnerability databases, review platforms, job boards…);
4. Industry publication or other third-party page;
5. Aggregator or search-result evidence.

Every evidence item gets a tier when it enters the ledger. Tiers influence confidence without being its
only determinant: prioritization scales a gap's confidence by the factor of its best-supporting tier
(``source_tier_factors``, configurable), and the QA agent reports the tier mix.
"""

from __future__ import annotations

from cip.core.urls import urlparse

TIER_LABELS = {
    1: "Official product / documentation / pricing",
    2: "Official announcement / case study",
    3: "Trusted third party",
    4: "Industry publication / other third party",
    5: "Aggregator / search result",
}
DEFAULT_FACTORS = {1: 1.0, 2: 1.0, 3: 0.95, 4: 0.85, 5: 0.75}

ANNOUNCEMENT_HINTS = ("/blog", "/news", "/press", "/announc", "/case-stud", "/customers/", "/stories", "/changelog",
                      "/release", "/updates", "/insights", "/resources/")
TRUSTED_THIRD_PARTY = ("g2.com", "capterra.com", "trustradius.com", "getapp.com", "softwareadvice.com",
                       "gartner.com", "producthunt.com", "apps.apple.com", "play.google.com", "osv.dev",
                       "github.com", "gitlab.com", "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                       "crunchbase.com", "wikipedia.org", "linkedin.com")
PUBLICATIONS = ("techcrunch.com", "forbes.com", "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "theverge.com",
                "venturebeat.com", "zdnet.com", "businesswire.com", "prnewswire.com", "globenewswire.com")
TYPE_TIERS = {"github": 1, "gitlab": 1, "csv": 2, "documentation": 1, "advisory": 3, "app_store": 3,
              "marketplace": 3, "linkedin": 3, "social": 4, "search": 5}


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def tier(source_type: str, source_url: str) -> int:
    """The quality tier of a source (1 best … 5 weakest)."""
    host = _host(source_url)
    if source_type == "website" and host:
        if _matches(host, TRUSTED_THIRD_PARTY):
            return 3
        if _matches(host, PUBLICATIONS):
            return 4
        path = (urlparse(source_url).path or "").lower()
        return 2 if any(h in path for h in ANNOUNCEMENT_HINTS) else 1
    if source_type in TYPE_TIERS:
        return TYPE_TIERS[source_type]
    return 4


def factor(t: int | None, factors: dict | None = None) -> float:
    table = {int(k): float(v) for k, v in (factors or DEFAULT_FACTORS).items()}
    return table.get(int(t), 1.0) if t else 1.0


def mix(tiers: list[int]) -> dict[str, float]:
    """Share of evidence per tier, e.g. {"1": 0.6, "3": 0.3, "5": 0.1}."""
    if not tiers:
        return {}
    return {str(t): round(tiers.count(t) / len(tiers), 3) for t in sorted(set(tiers))}
