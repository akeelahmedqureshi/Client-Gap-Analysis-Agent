"""App Store Agent: the client's and competitors' mobile apps — listings, ratings, review themes.

Apps are found from App Store / Google Play links on the companies' own websites, then by
App Store search; search hits are kept only when ownership is verified (developer website on
the company's domain, or developer name = company name). Customer reviews (Apple) are
classified into deterministic themes and feature requests; every quoted review is evidence.
Runs only when external research was approved (it reads public third-party data).
"""

from __future__ import annotations

import asyncio
import statistics

from cip.agents.base import Agent, RunContext
from cip.connectors.research.appstore import (
    STALE_DAYS,
    THEMES,
    AppListing,
    analyze_reviews,
    apple_reviews,
    apple_search,
    listing_for_link,
    ownership,
    parse_app_link,
)
from cip.core.schemas import AgentResult, AgentStatus, Basis, Finding, Gap, GapType

MAX_APPS_PER_COMPANY = 3
MIN_RATINGS = 20  # ratings needed before an average is compared with the market
THEME_MIN_NEGATIVE = 3
THEME_MIN_SHARE = 0.2
RATING_GAP = 0.3

THEME_GAPS = {
    "stability": "Mobile app stability (crashes & bugs)",
    "performance": "Mobile app performance",
    "login": "Mobile login & account access",
    "notifications": "Reliable notifications & reminders",
    "sync": "Mobile data sync reliability",
    "usability": "Mobile app usability",
    "support": "Customer support responsiveness",
    "pricing": "In-app pricing & subscription experience",
}
RATING_GAP_NAME = "App store rating below competitors"
STALE_GAP_NAME = "Mobile app release cadence"


def _app_links(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for u in urls:
        parsed = parse_app_link(u)
        if parsed and parsed not in seen:
            seen.add(parsed)
            out.append(u)
    return out


def _weighted_rating(apps: list[dict]) -> tuple[float | None, int]:
    rated = [a for a in apps if a.get("rating") and (a.get("rating_count") or 0) > 0]
    total = sum(a["rating_count"] for a in rated)
    if not total:
        return None, 0
    return round(sum(a["rating"] * a["rating_count"] for a in rated) / total, 2), total


class AppStoreAgent(Agent):
    name = "app_store"
    description = "Analyze mobile apps of the client and competitors: store listings, ratings and review themes"
    after = ("client_research", "competitor_research")

    async def _find_apps(self, ctx: RunContext, name: str, domain: str | None, site_links: list[str],
                         search_terms: list[str]) -> list[tuple[AppListing, float, str]]:
        """(listing, ownership confidence, how it was found)"""
        country = ctx.settings.app_store_country
        found: dict[tuple[str, str], tuple[AppListing, float, str]] = {}
        links = _app_links(site_links)[:MAX_APPS_PER_COMPANY]
        for link, listing in zip(links, await asyncio.gather(*(listing_for_link(ctx.fetcher, u, country)
                                                               for u in links))):
            if listing:
                # Linked from the company's own website: strong evidence even if the developer name differs.
                conf = max(ownership(listing, name, domain), 0.85)
                found[(listing.platform, listing.app_id)] = (listing, conf, f"linked from {name}'s website")
        if not any(p == "ios" for p, _ in found):
            for term in search_terms:
                for listing in await apple_search(ctx.fetcher, term, country):
                    conf = ownership(listing, name, domain)
                    if conf and (listing.platform, listing.app_id) not in found:
                        found[(listing.platform, listing.app_id)] = (listing, conf, f"App Store search '{term}'")
                if any(p == "ios" for p, _ in found):
                    break
        return list(found.values())[:MAX_APPS_PER_COMPANY]

    def _record_listing(self, ctx: RunContext, owner: str, listing: AppListing, conf: float, how: str) -> dict:
        age = listing.days_since_update()
        parts = [f"{listing.name} ({'iOS' if listing.platform == 'ios' else 'Android'}) by "
                 f"{listing.developer or 'unknown developer'}"]
        if listing.rating:
            parts.append(f"rated {listing.rating:g}★ from {listing.rating_count or 0:,} ratings")
        if listing.version:
            parts.append(f"version {listing.version}" + (f" released {listing.updated[:10]}" if listing.updated else ""))
        ev = ctx.ledger.add(f"{owner} app: " + ", ".join(parts), listing.url, "app_store", round(conf, 2),
                            extracted_text=f"{how}; developer site: {listing.developer_url or 'n/a'}")
        return {**listing.to_dict(), "ownership": round(conf, 2), "found_via": how, "days_since_update": age,
                "evidence_id": ev.id}

    async def run(self, ctx: RunContext) -> AgentResult:
        s = ctx.settings
        if not s.app_store_enabled:
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0, data={"enabled": False})
        research = ctx.outputs.get("client_research")
        if research is None or research.status != AgentStatus.COMPLETED:
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0,
                               data={"enabled": True, "reason": "external research not approved or not run"})
        rec, ledger = ctx.record, ctx.ledger
        cr = ctx.data("client_research")
        profile = cr.get("profile") or {}
        domain = profile.get("domain") or rec.client.domain
        client_links = [c["value"] for c in profile.get("contacts", []) if c.get("type") in ("app_store", "google_play")]
        client_links += rec.sources.social + rec.sources.websites
        terms = [rec.client.name] + [p["name"] for p in profile.get("products", [])[:2] if p.get("name")]
        findings: list[Finding] = []
        notes: list[str] = []

        # Client apps --------------------------------------------------------
        client_apps = [self._record_listing(ctx, rec.client.name, li, conf, how)
                       for li, conf, how in await self._find_apps(ctx, rec.client.name, domain, client_links, terms)]

        # Competitor apps ----------------------------------------------------
        competitors = ctx.data("competitor_research").get("competitors", [])[: s.max_competitors]

        async def competitor_apps(c: dict) -> list[tuple[AppListing, float, str]]:
            links: list[str] = []
            if c.get("url"):
                page = await ctx.fetcher.fetch(c["url"])
                links = page.external_links if page else []
            return await self._find_apps(ctx, c["name"], c.get("url"), links, [c["name"]])

        comp_results = await asyncio.gather(*(competitor_apps(c) for c in competitors))
        comp_apps = []
        for c, apps in zip(competitors, comp_results):
            if apps:
                comp_apps.append({"competitor_id": c["id"], "name": c["name"],
                                  "apps": [self._record_listing(ctx, c["name"], li, conf, how)
                                           for li, conf, how in apps]})

        # Reviews (Apple feed) --------------------------------------------------
        country = s.app_store_country

        async def reviews_for(app: dict):
            return await apple_reviews(ctx.fetcher, app["app_id"], country) if app["platform"] == "ios" else []

        client_reviews = [r for rs in await asyncio.gather(*(reviews_for(a) for a in client_apps)) for r in rs]
        client_analysis = analyze_reviews(client_reviews, ctx.taxonomy)
        review_url = next((a["url"] for a in client_apps if a["platform"] == "ios"), None)
        themes_out = []
        for t in client_analysis.themes:
            # The excerpt keeps each review a separate ledger entry (the ledger merges identical claims).
            ev_ids = list(dict.fromkeys(
                ledger.add(f"{rec.client.name} app review ({ex['rating']}★) mentions {t['label'].lower()}: "
                           f"\"{ex['quote'][:80]}\"", review_url, "app_store", 0.75, extracted_text=ex["quote"]).id
                for ex in t["examples"]))
            themes_out.append({**t, "evidence_ids": ev_ids})
        requests_out = []
        for rq in client_analysis.requests:
            feature = ctx.taxonomy.get(rq["feature_id"])
            ev_ids = list(dict.fromkeys(
                ledger.add(f"{rec.client.name} app review asks for {feature.name}: \"{ex['quote'][:80]}\"",
                           review_url, "app_store", 0.75, extracted_text=ex["quote"]).id for ex in rq["examples"]))
            requests_out.append({**rq, "name": feature.name, "evidence_ids": ev_ids})

        competitor_themes = []
        for entry in comp_apps[: s.app_store_competitor_reviews]:
            ios = next((a for a in entry["apps"] if a["platform"] == "ios"), None)
            if not ios:
                continue
            analysis = analyze_reviews(await reviews_for(ios))
            top = [t for t in analysis.themes if t["negative"]][:3]
            if top:
                competitor_themes.append({"competitor_id": entry["competitor_id"], "name": entry["name"],
                                          "reviews": analysis.total, "average": analysis.average,
                                          "themes": [{k: t[k] for k in ("theme", "label", "negative")}
                                                     for t in top]})

        # Market comparison -------------------------------------------------------
        client_rating, client_count = _weighted_rating(client_apps)
        comp_ratings = []
        for entry in comp_apps:
            r, n = _weighted_rating(entry["apps"])
            if r is not None and n >= MIN_RATINGS:
                comp_ratings.append(r)
        market = {"client_rating": client_rating, "client_rating_count": client_count,
                  "competitor_median_rating": round(statistics.median(comp_ratings), 2) if comp_ratings else None,
                  "competitors_with_apps": len(comp_apps), "competitors_checked": len(competitors)}

        # Findings & gaps -------------------------------------------------------
        gaps: list[Gap] = []
        comp_app_evidence = [a["evidence_id"] for e in comp_apps for a in e["apps"]]
        if client_apps:
            findings.append(Finding(category="app_store", title=f"{len(client_apps)} client app(s) in the app stores",
                                    detail="; ".join(f"{a['name']} ({'iOS' if a['platform'] == 'ios' else 'Android'}): "
                                                     f"{a['rating'] or 'n/a'}★ / {a['rating_count'] or 0} ratings"
                                                     for a in client_apps),
                                    evidence_ids=[a["evidence_id"] for a in client_apps], confidence=0.9))
        elif comp_apps:
            gaps.append(Gap(
                feature_id="ux.mobile_app" if "ux.mobile_app" in ctx.taxonomy else None, name="Native mobile app",
                category="UX", gap_type=GapType.UX,
                description=f"{len(comp_apps)} of {len(competitors)} competitors publish mobile apps "
                            f"({', '.join(e['name'] for e in comp_apps)}); no app by the client was found in the "
                            "App Store or on Google Play.",
                competitors_with=[e["competitor_id"] for e in comp_apps], evidence_ids=comp_app_evidence[:8],
                confidence=0.6, basis=Basis.INFERRED))
            notes.append("No client app found: searched links on the client's website and the App Store by name; "
                         "apps published under a different developer name may be missed.")

        if (client_rating is not None and client_count >= MIN_RATINGS and market["competitor_median_rating"]
                and client_rating <= market["competitor_median_rating"] - RATING_GAP):
            gaps.append(Gap(
                name=RATING_GAP_NAME, category="UX", gap_type=GapType.UX,
                description=f"The client's apps average {client_rating:g}★ ({client_count:,} ratings) vs a "
                            f"competitor median of {market['competitor_median_rating']:g}★.",
                competitors_with=[e["competitor_id"] for e in comp_apps],
                evidence_ids=[a["evidence_id"] for a in client_apps] + comp_app_evidence[:4],
                confidence=0.8, basis=Basis.EVIDENCE))

        for t in themes_out:
            if t["negative"] >= THEME_MIN_NEGATIVE and t["share_negative"] >= THEME_MIN_SHARE:
                gaps.append(Gap(
                    name=THEME_GAPS[t["theme"]], category="UX", gap_type=GapType.UX,
                    description=f"{t['negative']} of {client_analysis.sentiment.get('negative', 0)} negative recent "
                                f"App Store reviews mention {t['label'].lower()}.",
                    evidence_ids=t["evidence_ids"], confidence=0.75, basis=Basis.EVIDENCE))
                findings.append(Finding(category="app_store.reviews", title=f"Review theme: {t['label']}",
                                        detail=t["examples"][0]["quote"], evidence_ids=t["evidence_ids"],
                                        confidence=0.75))

        stale = [a for a in client_apps if (a["days_since_update"] or 0) > STALE_DAYS]
        if stale:
            gaps.append(Gap(
                name=STALE_GAP_NAME, category="Operations", gap_type=GapType.TECHNOLOGY,
                description="; ".join(f"{a['name']} ({'iOS' if a['platform'] == 'ios' else 'Android'}) last updated "
                                      f"{a['days_since_update']} days ago"
                                      for a in stale) + " — app stores and OS updates penalise stale apps.",
                evidence_ids=[a["evidence_id"] for a in stale], confidence=0.8, basis=Basis.EVIDENCE))
        for rq in requests_out:
            findings.append(Finding(category="app_store.requests",
                                    title=f"Customers ask for {rq['name']} ({rq['count']} review(s))",
                                    evidence_ids=rq["evidence_ids"], confidence=0.7))
        for ct in competitor_themes:
            weak = {t["theme"] for t in ct["themes"] if t["negative"] >= 2}
            client_weak = {t["theme"] for t in themes_out if t["negative"] >= THEME_MIN_NEGATIVE}
            for theme in sorted(weak - client_weak):
                findings.append(Finding(
                    category="app_store.positioning",
                    title=f"{ct['name']} users complain about {THEMES[theme][0].lower()}",
                    detail="A positioning opportunity if the client performs better here.", confidence=0.5,
                    basis=Basis.INFERRED))

        evidence = [ledger.get(i) for i in {*(a["evidence_id"] for a in client_apps), *comp_app_evidence,
                                            *(e for t in themes_out for e in t["evidence_ids"]),
                                            *(e for r in requests_out for e in r["evidence_ids"])} if ledger.get(i)]
        return AgentResult(
            findings=findings, evidence=evidence,
            confidence=0.8 if client_apps or comp_apps else 0.4,
            data={"enabled": True, "client_apps": client_apps, "client_has_app": bool(client_apps),
                  "competitor_apps": comp_apps,
                  "client_reviews": {"total": client_analysis.total, "average": client_analysis.average,
                                     "sentiment": client_analysis.sentiment, "themes": themes_out},
                  "requests": requests_out, "competitor_review_themes": competitor_themes, "market": market,
                  "gaps": [g.model_dump(mode="json") for g in gaps], "notes": notes,
                  "method": "Public App Store (iTunes lookup/search, customer-review feed) and Google Play listing "
                            "data. Recent reviews only (up to 50 per app); review authors are not stored."},
        )
