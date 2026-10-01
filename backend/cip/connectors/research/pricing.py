"""Deterministic pricing extraction from pricing pages.

Extracts plans and prices (normalised to a monthly figure), the pricing model
(per-seat, flat, usage-based, tiered, freemium, quote-based), free tier / free
trial, annual-billing discount and "contact sales" enterprise tiers. Every
extracted value keeps the snippet it came from so it can be stored as evidence.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from cip.connectors.research.web import Page

PRICING_PATH = re.compile(r"/(pricing|plans|plans-and-pricing|price|prices|buy|subscribe)(/|$)", re.I)
CURRENCIES = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "a$": "AUD",
              "c$": "CAD", "₹": "INR", "inr": "INR"}
_MONEY = re.compile(
    r"(?P<cur>A\$|C\$|[$€£₹]|\b(?:USD|EUR|GBP|INR)\b)\s?(?P<amt>\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"(?P<tail>[^$€£₹]{0,60})",
    re.I,
)
_PERIOD_YEAR = re.compile(r"/\s?(?:yr|year|annum)\b|per\s+year|annually|/\s?y\b|a year", re.I)
_PERIOD_MONTH = re.compile(r"/\s?(?:mo|month)\b|per\s+month|monthly|a month|/\s?m\b", re.I)
_PER_SEAT = re.compile(r"(?:/|per)\s?(?:user|seat|member|agent|provider|editor|license|licence|staff|practitioner)", re.I)
_USAGE = re.compile(r"(?:/|per)\s?(?:request|call|api call|transaction|message|sms|minute|min|gb|credit|"
                    r"appointment|booking|order|run|1k|1,000|thousand)|usage[- ]based|pay[- ]as[- ]you[- ]go", re.I)
_FREE_TIER = re.compile(r"\bfree (?:plan|tier|forever|for ever|edition|version)\b|\bforever free\b|\$0\b|€0\b|£0\b", re.I)
_TRIAL = re.compile(r"(\d{1,3})[- ]day (?:free )?trial|"
                    r"(?:try (?:it )?|free )(?:free )?(?:for|trial of) (\d{1,3}) days|"
                    r"free trial|try (?:it )?(?:for )?free", re.I)
_CONTACT = re.compile(r"contact (?:us|sales)|talk to (?:sales|us)|request (?:a )?(?:quote|demo|pricing)|custom pricing|"
                      r"get a quote|let'?s talk|custom quote", re.I)
_ANNUAL_DISCOUNT = re.compile(r"save\s*(?:up to\s*)?(\d{1,2})\s?%[^.]{0,40}(?:annual|year)|"
                              r"(?:annual|yearly|year)[^.]{0,40}save\s*(?:up to\s*)?(\d{1,2})\s?%|"
                              r"(\d{1,2})\s?%\s*(?:off|discount)[^.]{0,30}(?:annual|year)", re.I)
PLAN_NAMES = re.compile(r"^(free|starter|start|basic|lite|essentials?|standard|plus|pro|professional|team|teams|"
                        r"business|growth|scale|premium|advanced|ultimate|enterprise|custom|solo|individual|"
                        r"personal|small business|practice|clinic|unlimited)\b", re.I)


@dataclass
class Plan:
    name: str
    price: float | None           # in `currency`, per `period`
    period: str | None            # month | year | None (one-off/unknown)
    unit: str | None              # seat | usage | None (flat)
    monthly: float | None         # normalised monthly price (None for quote-based)
    quote_based: bool = False
    snippet: str = ""


@dataclass
class PricingProfile:
    source_url: str
    currency: str | None = None
    plans: list[Plan] = field(default_factory=list)
    models: list[str] = field(default_factory=list)   # per_seat, flat, usage_based, tiered, freemium, quote_based
    free_tier: bool = False
    free_trial: bool = False
    trial_days: int | None = None
    enterprise_contact: bool = False
    annual_discount_pct: int | None = None
    snippets: dict[str, str] = field(default_factory=dict)  # fact -> supporting text

    @property
    def paid_monthly(self) -> list[float]:
        return sorted(p.monthly for p in self.plans if p.monthly and p.monthly > 0)

    @property
    def entry_price(self) -> float | None:
        prices = self.paid_monthly
        return prices[0] if prices else None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["entry_price_monthly"] = self.entry_price
        d["max_price_monthly"] = self.paid_monthly[-1] if self.paid_monthly else None
        return d


def _amount(raw: str) -> float | None:
    try:
        return float(raw.replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _context(text: str, start: int, end: int, width: int = 90) -> str:
    return " ".join(text[max(0, start - width): end + width].split())


def _plan_name_before(text: str, pos: int, headings: list[str]) -> str | None:
    """Closest plan-like heading that appears before ``pos`` (within ~350 chars)."""
    window = text[max(0, pos - 350):pos]
    best, best_idx = None, -1
    for h in headings:
        h = h.strip()
        if not (1 <= len(h) <= 40) or not (PLAN_NAMES.match(h) or len(h.split()) <= 3):
            continue
        idx = window.rfind(h)
        if idx > best_idx:
            best, best_idx = h, idx
    return best


def is_pricing_page(page: Page) -> bool:
    return bool(PRICING_PATH.search(urlparse(page.url).path)) or "pricing" in page.title.lower()


def extract_pricing(pages: list[Page]) -> PricingProfile | None:
    pricing_pages = [p for p in pages if is_pricing_page(p)]
    if not pricing_pages:
        return None
    page = pricing_pages[0]
    text = page.text
    prof = PricingProfile(source_url=page.url)

    # schema.org Offers, when published
    for obj in page.structured_data:
        offers = obj.get("offers") if isinstance(obj, dict) else None
        for off in offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []:
            price = _amount(str(off.get("price", "")))
            if price is not None:
                cur = str(off.get("priceCurrency") or "").upper() or None
                prof.currency = prof.currency or cur
                name = str(off.get("name") or obj.get("name") or "Plan")[:40]
                prof.plans.append(Plan(name, price, "month", None, price, snippet=f"schema.org Offer {name}: {price} {cur}"))

    seen: set[tuple[str, float]] = {(p.name.lower(), p.price or 0) for p in prof.plans}
    for m in _MONEY.finditer(text):
        amount = _amount(m.group("amt"))
        if amount is None or amount > 100_000:
            continue
        tail = m.group("tail")
        cur = CURRENCIES.get(m.group("cur").lower())
        if _PERIOD_YEAR.search(tail):
            period, monthly = "year", amount / 12
        elif _PERIOD_MONTH.search(tail) or _PER_SEAT.search(tail):
            period, monthly = "month", amount
        else:
            continue  # a bare amount (e.g. "$2M raised") is not a plan price
        unit = "seat" if _PER_SEAT.search(tail) else "usage" if _USAGE.search(tail) else None
        name = _plan_name_before(text, m.start(), page.headings) or f"Plan {len(prof.plans) + 1}"
        key = (name.lower(), amount)
        if key in seen:
            continue
        seen.add(key)
        prof.currency = prof.currency or cur
        prof.plans.append(Plan(name, amount, period, unit, round(monthly, 2), snippet=_context(text, m.start(), m.end())))

    if (m := _FREE_TIER.search(text)) or any(p.price == 0 for p in prof.plans) or \
            any(re.match(r"^free\b", p.name, re.I) for p in prof.plans):
        prof.free_tier = True
        if m:
            prof.snippets["free_tier"] = _context(text, m.start(), m.end())
    if m := _TRIAL.search(text):
        prof.free_trial = True
        days = m.group(1) or m.group(2)
        prof.trial_days = int(days) if days else None
        prof.snippets["free_trial"] = _context(text, m.start(), m.end())
    if m := _CONTACT.search(text):
        prof.enterprise_contact = True
        prof.snippets["enterprise_contact"] = _context(text, m.start(), m.end())
        if not any(p.quote_based for p in prof.plans):
            name = next((h for h in page.headings if re.match(r"^(enterprise|custom)\b", h.strip(), re.I)), "Enterprise")
            prof.plans.append(Plan(name[:40], None, None, None, None, quote_based=True,
                                   snippet=_context(text, m.start(), m.end())))
    if m := _ANNUAL_DISCOUNT.search(text):
        pct = next(int(g) for g in m.groups() if g)
        if 0 < pct < 80:
            prof.annual_discount_pct = pct
            prof.snippets["annual_discount"] = _context(text, m.start(), m.end())

    paid = [p for p in prof.plans if p.monthly]
    models = []
    if any(p.unit == "seat" for p in paid):
        models.append("per_seat")
    if any(p.unit == "usage" for p in paid) or _USAGE.search(text):
        models.append("usage_based")
    if paid and not any(p.unit for p in paid):
        models.append("flat")
    if len(paid) >= 2:
        models.append("tiered")
    if prof.free_tier:
        models.append("freemium")
    if prof.enterprise_contact and not paid:
        models.append("quote_based")
    prof.models = models
    if not (prof.plans or prof.free_trial or prof.free_tier):
        return None
    return prof


def market_summary(profiles: list[PricingProfile]) -> dict:
    """Aggregate competitor pricing: entry-price range and how common each practice is."""
    n = len(profiles)
    if not n:
        return {"competitors_with_pricing": 0}
    share = lambda pred: round(sum(1 for p in profiles if pred(p)) / n, 2)  # noqa: E731
    currencies = [p.currency for p in profiles if p.currency]
    currency = max(sorted(set(currencies)), key=currencies.count) if currencies else None
    # Prices in different currencies are not comparable: price statistics use the dominant currency only.
    entries = [p.entry_price for p in profiles if p.entry_price and (p.currency == currency or not currency)]
    excluded = sum(1 for p in profiles if p.entry_price and currency and p.currency != currency)
    return {
        "competitors_with_pricing": n,
        "currency": currency,
        "excluded_other_currency": excluded,
        "entry_price_min": min(entries) if entries else None,
        "entry_price_median": round(statistics.median(entries), 2) if entries else None,
        "entry_price_max": max(entries) if entries else None,
        "published_prices_share": share(lambda p: bool(p.entry_price)),
        "free_trial_share": share(lambda p: p.free_trial),
        "free_tier_share": share(lambda p: p.free_tier),
        "per_seat_share": share(lambda p: "per_seat" in p.models),
        "usage_based_share": share(lambda p: "usage_based" in p.models),
        "annual_discount_share": share(lambda p: p.annual_discount_pct is not None),
        "enterprise_tier_share": share(lambda p: p.enterprise_contact),
    }
