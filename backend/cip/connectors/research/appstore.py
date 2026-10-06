"""Public app-store data: Apple App Store and Google Play listings, Apple customer reviews.

Sources (all public, no credentials):

* Apple — the iTunes Search / Lookup API and the App Store customer-reviews RSS feed.
* Google Play — the public listing page (schema.org ``SoftwareApplication`` JSON-LD), fetched
  through the normal crawler, so robots.txt is respected. Play reviews have no public API and
  are not collected.

Apps are attributed to a company only when ownership is verified: the developer's website is
on the company's domain, or the developer name matches the company name. Review authors are
never stored, and e-mail addresses / phone numbers inside review text are masked.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote

from cip.core.urls import urlparse

from cip.connectors.research.web import _EMAIL, _PHONE, WebFetcher, registrable_domain

ITUNES = "https://itunes.apple.com"
PLAY = "https://play.google.com/store/apps/details"
STALE_DAYS = 180
_CORP_SUFFIX = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|gmbh|ag|sa|sas|bv|plc|corp|corporation|co|company|holdings|group|"
    r"technologies|technology|labs|software|apps?)\b\.?", re.I)


@dataclass
class AppListing:
    platform: str  # ios | android
    app_id: str
    name: str
    url: str
    developer: str = ""
    developer_url: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    version: str | None = None
    updated: str | None = None  # ISO date of the current version
    release_notes: str = ""
    price: float | None = None
    currency: str | None = None
    genre: str | None = None
    description: str = ""

    def days_since_update(self, now: datetime | None = None) -> int | None:
        if not self.updated:
            return None
        try:
            dt = datetime.fromisoformat(self.updated.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - dt).days

    def to_dict(self) -> dict:
        d = asdict(self)
        d["description"] = d["description"][:500]
        d["release_notes"] = d["release_notes"][:500]
        return d


@dataclass
class Review:
    rating: int
    title: str
    text: str
    version: str | None = None
    date: str | None = None


def parse_app_link(url: str) -> tuple[str, str] | None:
    """App Store / Google Play URL -> (platform, app id)."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host in ("apps.apple.com", "itunes.apple.com"):
        m = re.search(r"/id(\d{5,})", p.path)
        return ("ios", m.group(1)) if m else None
    if host == "play.google.com" and p.path.startswith("/store/apps/details"):
        pkg = parse_qs(p.query).get("id", [""])[0]
        return ("android", pkg) if re.fullmatch(r"[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+", pkg) else None
    return None


def mask_pii(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _CORP_SUFFIX.sub(" ", name.lower())).strip()


def ownership(listing: AppListing, company: str, domain: str | None) -> float:
    """Confidence that ``listing`` belongs to the company (0 = not attributable)."""
    if domain and listing.developer_url and registrable_domain(listing.developer_url) == registrable_domain(domain):
        return 0.95
    dev, comp = _norm_name(listing.developer), _norm_name(company)
    if dev and comp and (dev == comp or dev.startswith(comp + " ") or comp.startswith(dev + " ")):
        return 0.8
    return 0.0


# --------------------------------------------------------------------------- Apple


def _apple_listing(r: dict) -> AppListing:
    return AppListing(
        platform="ios", app_id=str(r.get("trackId")), name=r.get("trackName", ""),
        url=(r.get("trackViewUrl") or f"https://apps.apple.com/app/id{r.get('trackId')}").split("?")[0],
        developer=r.get("sellerName") or r.get("artistName") or "", developer_url=r.get("sellerUrl"),
        rating=round(float(r["averageUserRating"]), 2) if r.get("averageUserRating") is not None else None,
        rating_count=r.get("userRatingCount"), version=r.get("version"),
        updated=r.get("currentVersionReleaseDate"), release_notes=r.get("releaseNotes") or "",
        price=r.get("price"), currency=r.get("currency"), genre=r.get("primaryGenreName"),
        description=r.get("description") or "")


async def apple_lookup(fetcher: WebFetcher, app_id: str, country: str = "us") -> AppListing | None:
    data = await fetcher.get_json(f"{ITUNES}/lookup?id={quote(app_id)}&country={country}")
    results = (data or {}).get("results", []) if isinstance(data, dict) else []
    return _apple_listing(results[0]) if results else None


async def apple_search(fetcher: WebFetcher, term: str, country: str = "us", limit: int = 5) -> list[AppListing]:
    data = await fetcher.get_json(f"{ITUNES}/search?term={quote(term)}&entity=software&country={country}"
                                  f"&limit={limit}")
    results = (data or {}).get("results", []) if isinstance(data, dict) else []
    return [_apple_listing(r) for r in results if r.get("kind") == "software" or r.get("trackId")]


async def apple_reviews(fetcher: WebFetcher, app_id: str, country: str = "us") -> list[Review]:
    """Most recent customer reviews (one feed page, up to 50). Author names are dropped."""
    data = await fetcher.get_json(f"{ITUNES}/{country}/rss/customerreviews/page=1/id={quote(app_id)}"
                                  "/sortby=mostrecent/json")
    entries = ((data or {}).get("feed") or {}).get("entry") or [] if isinstance(data, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for e in entries:
        rating = (e.get("im:rating") or {}).get("label")
        text = (e.get("content") or {}).get("label")
        if not rating or not text:
            continue  # the first entry of older feeds is the app itself
        out.append(Review(rating=int(rating), title=mask_pii((e.get("title") or {}).get("label", ""))[:200],
                          text=mask_pii(text)[:1500], version=(e.get("im:version") or {}).get("label"),
                          date=(e.get("updated") or {}).get("label")))
    return out


# --------------------------------------------------------------------------- Google Play


async def play_listing(fetcher: WebFetcher, package: str) -> AppListing | None:
    url = f"{PLAY}?id={quote(package)}&hl=en_US&gl=US"
    page = await fetcher.fetch(url)
    if page is None or page.status >= 400:
        return None
    app = next((o for o in page.structured_data
                if "SoftwareApplication" in str(o.get("@type", "")) or "MobileApplication" in str(o.get("@type", ""))),
               None)
    if app is None:
        return None
    rating = app.get("aggregateRating") or {}
    author = app.get("author") or {}
    offers = app.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    def num(v, cast=float):
        try:
            return cast(str(v).replace(",", "")) if v not in (None, "") else None
        except ValueError:
            return None

    return AppListing(
        platform="android", app_id=package, name=app.get("name", package),
        url=f"{PLAY}?id={package}",
        developer=author.get("name", "") if isinstance(author, dict) else str(author),
        developer_url=author.get("url") if isinstance(author, dict) else None,
        rating=round(num(rating.get("ratingValue")) or 0, 2) or None, rating_count=num(rating.get("ratingCount"), int),
        price=num(offers.get("price")), currency=offers.get("priceCurrency"),
        genre=app.get("applicationCategory"), description=(app.get("description") or "")[:2000])


async def listing_for_link(fetcher: WebFetcher, url: str, country: str = "us") -> AppListing | None:
    parsed = parse_app_link(url)
    if not parsed:
        return None
    platform, app_id = parsed
    return await apple_lookup(fetcher, app_id, country) if platform == "ios" else await play_listing(fetcher, app_id)


# --------------------------------------------------------------------------- review analysis

THEMES: dict[str, tuple[str, list[str]]] = {
    # theme id: (label, phrases)
    "stability": ("Crashes & bugs", ["crash", "crashes", "crashing", "freezes", "frozen", "buggy", "bug", "glitch",
                                      "not working", "doesn't work", "does not work", "broken", "error"]),
    "performance": ("Speed & performance", ["slow", "lag", "laggy", "takes forever", "loading", "battery"]),
    "login": ("Login & account access", ["login", "log in", "sign in", "signin", "password", "logged out",
                                          "verification code", "two factor", "2fa"]),
    "notifications": ("Notifications & reminders", ["notification", "notifications", "reminder", "reminders",
                                                     "alerts"]),
    "sync": ("Data sync & reliability", ["sync", "syncing", "lost my data", "data lost", "disappeared",
                                         "not saving", "didn't save"]),
    "usability": ("Usability & design", ["confusing", "hard to use", "difficult to use", "not intuitive", "clunky",
                                         "cluttered", "user interface", "navigation"]),
    "support": ("Customer support", ["customer service", "customer support", "no response", "support team",
                                     "refund"]),
    "pricing": ("Pricing & subscriptions", ["subscription", "too expensive", "expensive", "charged", "paywall",
                                            "price", "ads"]),
}
REQUEST_CUES = ["please add", "wish", "would be nice", "would love", "would be great", "need a", "needs a",
                "missing", "should have", "add a feature", "feature request", "hope you add", "if only"]

_PATTERNS = {t: re.compile(r"\b(" + "|".join(re.escape(p) for p in ps) + r")\b", re.I)
             for t, (_, ps) in THEMES.items()}
_REQUEST = re.compile(r"\b(" + "|".join(re.escape(p) for p in REQUEST_CUES) + r")\b", re.I)


def sentiment(rating: int) -> str:
    return "negative" if rating <= 2 else "positive" if rating >= 4 else "neutral"


def _quote(review: Review, match: re.Match | None) -> str:
    text = review.text
    if match is None or len(text) <= 240:
        return text[:240]
    start = max(0, match.start() - 100)
    return ("…" if start else "") + text[start:start + 240] + "…"


@dataclass
class ReviewAnalysis:
    total: int = 0
    average: float | None = None
    sentiment: dict[str, int] = field(default_factory=dict)
    themes: list[dict] = field(default_factory=list)  # [{theme, label, count, negative, share_negative, examples}]
    requests: list[dict] = field(default_factory=list)  # [{feature_id, count, examples}]


def analyze_reviews(reviews: list[Review], taxonomy=None) -> ReviewAnalysis:
    a = ReviewAnalysis(total=len(reviews))
    if not reviews:
        return a
    a.average = round(sum(r.rating for r in reviews) / len(reviews), 2)
    a.sentiment = {s: sum(1 for r in reviews if sentiment(r.rating) == s) for s in ("negative", "neutral", "positive")}
    negatives = max(1, a.sentiment["negative"])
    for theme, (label, _) in THEMES.items():
        hits = [(r, m) for r in reviews if (m := _PATTERNS[theme].search(f"{r.title}. {r.text}"))]
        if not hits:
            continue
        neg = [(r, m) for r, m in hits if sentiment(r.rating) == "negative"]
        examples = (neg or hits)[:3]
        a.themes.append({"theme": theme, "label": label, "count": len(hits), "negative": len(neg),
                         "share_negative": round(len(neg) / negatives, 2),
                         "examples": [{"rating": r.rating, "quote": _quote(r, m), "version": r.version}
                                      for r, m in examples]})
    a.themes.sort(key=lambda t: (-t["negative"], -t["count"]))
    if taxonomy is not None:
        requested: dict[str, list[Review]] = {}
        for r in reviews:
            body = f"{r.title}. {r.text}"
            if not _REQUEST.search(body):
                continue
            for fid in taxonomy.match_text(body):
                requested.setdefault(fid, []).append(r)
        a.requests = sorted(({"feature_id": fid, "count": len(rs),
                              "examples": [{"rating": r.rating, "quote": r.text[:240]} for r in rs[:2]]}
                             for fid, rs in requested.items()), key=lambda x: -x["count"])
    return a
