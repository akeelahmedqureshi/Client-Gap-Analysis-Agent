"""Offline fakes: a mini web, a fake source-control provider, search and LLM."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel

from cip.connectors.research.search import SearchProvider, SearchResult
from cip.connectors.source_control.base import (
    ActivityItem,
    RepoMetadata,
    RepoRef,
    RepositoryAccessDenied,
    SourceControlProvider,
    TreeEntry,
)
from cip.core.llm import LLMUnavailable


def html(title: str, body: str, desc: str = "", links: list[str] = (), head: str = "", lang: str | None = None) -> str:
    anchors = "".join(f'<a href="{u}">{u}</a>' for u in links)
    lang_attr = f' lang="{lang}"' if lang else ""
    return (f"<html{lang_attr}><head><title>{title}</title><meta name='description' content='{desc}'>{head}</head>"
            f"<body><nav>{anchors}</nav>{body}</body></html>")


VIEWPORT = "<meta name='viewport' content='width=device-width, initial-scale=1'>"

ABC_JSONLD = json.dumps({
    "@context": "https://schema.org", "@type": "Organization", "name": "ABC Healthcare",
    "legalName": "ABC Healthcare Holdings Inc.", "foundingDate": "2012-03-01",
    "address": {"@type": "PostalAddress", "addressLocality": "Austin", "addressRegion": "TX", "addressCountry": "US"},
    "numberOfEmployees": {"@type": "QuantitativeValue", "minValue": 51, "maxValue": 200},
    "brand": [{"@type": "Brand", "name": "Patient Scheduler"}], "areaServed": ["United States", "Canada"],
    "sameAs": ["https://www.youtube.com/@abchealth"],
})


SITES: dict[str, str] = {
    # --- client ---------------------------------------------------------
    "https://abc-healthcare.com/": html(
        "ABC Healthcare", "<h1>ABC Healthcare</h1><p>Book an appointment online with our clinics. "
        "Email reminders keep patients on time. Founded in 2012 in Austin, Texas.</p>"
        "<img src='/hero.jpg'><img src='/team.jpg'><form><input type='email' placeholder='Newsletter'></form>",
        "ABC Healthcare runs patient scheduling software for clinics.",
        ["/about", "/products/patient-scheduler", "/contact", "https://www.linkedin.com/company/abc-healthcare",
         "https://twitter.com/abchealth"],
        head=f'<script type="application/ld+json">{ABC_JSONLD}</script>'),
    "https://abc-healthcare.com/pricing": html(
        "Pricing", "<h1>Pricing</h1><h2>Practice</h2><p>$79 /month per provider, billed monthly.</p>"
        "<h2>Group</h2><p>$149 /month per provider.</p>"),
    "https://abc-healthcare.com/careers": html(
        "Careers", "<h1>Join ABC Healthcare</h1><p>See our open roles.</p>"
        "<a href='https://boards.greenhouse.io/abchealth'>Open positions</a>"),
    "https://abc-healthcare.com/blog": html(
        "Blog", "<h1>Blog</h1><a href='/blog/introducing-sms-reminders'>Introducing SMS reminders for every clinic</a>"
        "<a href='/blog/patient-no-shows-guide'>A practical guide to reducing patient no-shows</a>"
        "<a href='/blog/page/2'>2</a><p>© 2026 ABC Healthcare Holdings Inc. All rights reserved.</p>"),
    "https://boards-api.greenhouse.io/v1/boards/abchealth/jobs": {"jobs": [
        {"title": "Senior Machine Learning Engineer", "location": {"name": "Remote"},
         "departments": [{"name": "Engineering"}], "absolute_url": "https://boards.greenhouse.io/abchealth/jobs/1"},
        {"title": "Applied Scientist, LLM", "location": {"name": "Austin"}, "departments": [{"name": "AI"}],
         "absolute_url": "https://boards.greenhouse.io/abchealth/jobs/2"},
        {"title": "iOS Engineer", "location": {"name": "Remote"}, "departments": [{"name": "Mobile"}],
         "absolute_url": "https://boards.greenhouse.io/abchealth/jobs/3"},
        {"title": "Account Executive", "location": {"name": "Austin"}, "departments": [{"name": "Sales"}],
         "absolute_url": "https://boards.greenhouse.io/abchealth/jobs/4"},
    ]},
    "https://abc-healthcare.com/about": html(
        "About", "<h1>About us</h1><p>Our leadership: Jane Porter, CEO. Mark Levin, CTO. We serve clinics across "
        "the United States.</p>"),
    "https://abc-healthcare.com/products/patient-scheduler": html(
        "Patient Scheduler", "<h1>Patient Scheduler</h1><p>Online booking for clinics with a patient portal "
        "and dashboard.</p>", "Patient Scheduler: online booking for clinics"),
    "https://abc-healthcare.com/contact": html(
        "Contact", "<p>Contact us: <a href='mailto:support@abc-healthcare.com'>support</a> "
        "<a href='mailto:jane.porter@abc-healthcare.com'>Jane</a> Call +1 512 555 0100</p>"),
    # --- competitors ----------------------------------------------------
    "https://medibook.io/": html(
        "MediBook", "<h1>MediBook</h1><p>Online booking and scheduling for clinics. AI assistant answers "
        "patient questions 24/7. SMS reminders and email reminders. Telehealth video consultation. "
        "Single sign-on (SSO) with Okta. HIPAA compliant. iOS app and Android app on Google Play.</p>"
        "<a href='https://apps.apple.com/us/app/medibook/id2222222222'>Download on the App Store</a>"
        "<a href='https://play.google.com/store/apps/details?id=com.medibook.app'>Get it on Google Play</a>"
        "<a href='/signup'>Start free trial</a><a href='/login'>Log in</a><a href='/help'>Help center</a>"
        "<p>Trusted by 3,000 clinics.</p><script src='https://widget.intercom.io/widget/mb1'></script>",
        "Clinic scheduling with an AI assistant", head=VIEWPORT, lang="en"),
    "https://clinicflow.com/pricing": html(
        "Pricing | ClinicFlow", "<h1>Plans</h1><p>Start a 14-day free trial. Save 20% with annual billing.</p>"
        "<h2>Starter</h2><p>$49 /month per provider</p><h2>Growth</h2><p>$99 /month per provider</p>"
        "<h2>Enterprise</h2><p>Custom pricing - contact sales.</p><p>Backed by $12M in funding.</p>"),
    "https://medibook.io/pricing": html(
        "MediBook pricing", "<h1>Pricing</h1><h2>Free</h2><p>Free forever for one practitioner.</p>"
        "<h2>Pro</h2><p>$29 /month per practitioner. 30-day free trial.</p>"),
    "https://clinicflow.com/": html(
        "ClinicFlow", "<h1>ClinicFlow</h1><p>Scheduling software for clinics with online booking, SMS reminders, "
        "analytics dashboard, patient portal and a public REST API. Pricing from $49/month. HIPAA compliant.</p>"
        "<a href='/demo'>Book a demo</a><a href='/signup'>Get started</a><a href='/support'>Support</a>",
        "Clinic workflow and scheduling", head=VIEWPORT, lang="en"),
    "https://randomnews.com/": html("News", "<p>Celebrity gossip and sports scores.</p>"),
    # --- app stores (prefix keys, most specific first) --------------------
    "https://itunes.apple.com/lookup?id=1111111111*": {"results": [{
        "trackId": 1111111111, "trackName": "ABC Patient Scheduler", "sellerName": "ABC Healthcare Holdings Inc.",
        "sellerUrl": "https://abc-healthcare.com", "averageUserRating": 3.1, "userRatingCount": 412,
        "version": "2.3.0", "currentVersionReleaseDate": "2025-09-01T10:00:00Z", "releaseNotes": "Bug fixes.",
        "price": 0.0, "currency": "USD", "primaryGenreName": "Medical",
        "trackViewUrl": "https://apps.apple.com/us/app/abc-patient-scheduler/id1111111111?uo=4"}]},
    "https://itunes.apple.com/lookup?id=2222222222*": {"results": [{
        "trackId": 2222222222, "trackName": "MediBook", "sellerName": "MediBook Ltd",
        "sellerUrl": "https://medibook.io", "averageUserRating": 4.6, "userRatingCount": 2310, "version": "5.1",
        "currentVersionReleaseDate": "2026-09-20T10:00:00Z", "price": 0.0, "currency": "USD",
        "trackViewUrl": "https://apps.apple.com/us/app/medibook/id2222222222"}]},
    "https://itunes.apple.com/search?term=ClinicFlow*": {"results": [{
        "kind": "software", "trackId": 3333333333, "trackName": "ClinicFlow Mobile", "sellerName": "ClinicFlow Inc.",
        "sellerUrl": "https://www.clinicflow.com", "averageUserRating": 4.2, "userRatingCount": 88, "version": "1.9",
        "currentVersionReleaseDate": "2026-08-01T10:00:00Z",
        "trackViewUrl": "https://apps.apple.com/us/app/clinicflow-mobile/id3333333333"}]},
    "https://itunes.apple.com/search?term=*": {"results": [{
        # Name collision: must NOT be attributed to "ABC Healthcare" (different developer and domain).
        "kind": "software", "trackId": 9999999999, "trackName": "ABC Kids Learning", "sellerName": "ABC Learning LLC",
        "sellerUrl": "https://abckids.example", "averageUserRating": 4.9, "userRatingCount": 50000,
        "trackViewUrl": "https://apps.apple.com/us/app/abc-kids/id9999999999"}]},
    "https://itunes.apple.com/us/rss/customerreviews/page=1/id=2222222222*": {"feed": {"entry": [
        {"author": {"name": {"label": "Dr. Smith"}}, "im:rating": {"label": "5"}, "title": {"label": "Great"},
         "content": {"label": "Booking is fast and the AI assistant is handy."}, "im:version": {"label": "5.1"}},
        {"author": {"name": {"label": "Pat"}}, "im:rating": {"label": "2"}, "title": {"label": "Pricey"},
         "content": {"label": "Too expensive for a small practice since the subscription went up."},
         "im:version": {"label": "5.1"}},
        {"author": {"name": {"label": "Lee"}}, "im:rating": {"label": "1"}, "title": {"label": "Charged twice"},
         "content": {"label": "I was charged twice for my subscription."}, "im:version": {"label": "5.1"}},
    ]}},
    "https://itunes.apple.com/us/rss/customerreviews/*": {"feed": {}},
}

APP_STORE_PAGES = {
    "https://play.google.com/store/apps/details?id=com.medibook.app&hl=en_US&gl=US": html(
        "MediBook - Apps on Google Play", "<h1>MediBook</h1>",
        head='<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org", "@type": "SoftwareApplication", "name": "MediBook",
            "applicationCategory": "MEDICAL", "author": {"@type": "Person", "name": "MediBook Ltd",
                                                          "url": "https://medibook.io"},
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.4", "ratingCount": "1520"},
            "offers": [{"@type": "Offer", "price": "0", "priceCurrency": "USD"}]}) + "</script>"),
}
SITES.update(APP_STORE_PAGES)

# A client that does have an app (with crash-heavy reviews) — used by the app-store tests.
CLIENT_APP_REVIEWS = {"feed": {"entry": [
    {"author": {"name": {"label": "jane_doe_88"}}, "im:rating": {"label": str(r)}, "title": {"label": t},
     "content": {"label": c}, "im:version": {"label": "2.3.0"}}
    for r, t, c in [
        (1, "Keeps crashing", "The app crashes every time I open my appointments. Useless."),
        (1, "Crash on booking", "Crashing when I try to book. Call me at +1 512 555 0199 if you need details."),
        (2, "Buggy", "So buggy since the last update, the calendar is broken."),
        (1, "Freezes", "App freezes on the login screen and then crashes."),
        (2, "Can't sign in", "Cannot sign in, password reset email never arrives (me@example.com)."),
        (3, "Please add SMS", "Works ok but please add SMS reminders, my patients don't read email."),
        (3, "Missing texts", "Would be nice to get SMS reminders instead of email."),
        (5, "Love it", "Easy online booking for my clinic."),
        (4, "Good", "Nice and simple."),
    ]]}}
CLIENT_APP_SITES = {
    "https://abc-healthcare.com/products/patient-scheduler": html(
        "Patient Scheduler", "<h1>Patient Scheduler</h1><p>Online booking for clinics with a patient portal "
        "and dashboard.</p><a href='https://apps.apple.com/us/app/abc-patient-scheduler/id1111111111'>"
        "Download on the App Store</a>", "Patient Scheduler: online booking for clinics"),
    "https://itunes.apple.com/us/rss/customerreviews/page=1/id=1111111111*": CLIENT_APP_REVIEWS,
}


# A wider competitive landscape (opt-in: FakeSearch(landscape=True) + sites_with_landscape()).
LANDSCAPE_SITES: dict[str, str] = {
    "https://bookwell.app/": html(
        "BookWell", "<h1>BookWell</h1><p>Online booking for clinics and therapists. SMS reminders, email reminders "
        "and an analytics dashboard. Patient portal. Plans from $39/month.</p><a href='/pricing'>Pricing</a>"
        "<a href='/help'>Help center</a>", "Clinic booking software"),
    "https://caredesk.io/": html(
        "CareDesk", "<h1>CareDesk</h1><p>Patient portal and online payments for clinics. Invoicing, email reminders "
        "and a public API. Trusted by clinics in the United States.</p><a href='/docs'>Docs</a>",
        "Patient portal for clinics"),
    "https://slotsmart.com/": html(
        "SlotSmart", "<h1>SlotSmart</h1><p>Appointment scheduling for salons and barbers. Online booking and SMS "
        "reminders.</p>", "Salon scheduling"),
    "https://vetplan.com/": html(
        "VetPlan", "<h1>VetPlan</h1><p>Practice management for veterinary clinics: online booking, email "
        "reminders, invoicing and online payments. AI assistant for pet owners.</p><a href='/pricing'>Pricing</a>",
        "Veterinary clinic software"),
}


def sites_with_landscape() -> dict:
    return {**SITES, **LANDSCAPE_SITES}


def sites_with_client_app() -> dict:
    """SITES plus a client iOS app; prefix keys are placed before the generic fallbacks."""
    out = {k: v for k, v in CLIENT_APP_SITES.items()}
    out.update({k: v for k, v in SITES.items() if k not in out})
    return out


# Responses that need specific methods/headers (security review, OSV.dev).
OSV_VULN = {
    "id": "GHSA-test-0001", "summary": "Open redirect in express (test advisory)", "aliases": ["CVE-2099-0001"],
    "database_specific": {"severity": "HIGH"},
    "affected": [{"package": {"name": "express", "ecosystem": "npm"},
                  "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.19.2"}]}]}],
}


def _raw(request: httpx.Request, sites: dict) -> httpx.Response | None:
    url = str(request.url)
    if request.method == "POST" and url == "https://api.osv.dev/v1/querybatch":
        queries = json.loads(request.content)["queries"]
        return httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-test-0001"}]} if q["package"]["name"] == "express" else {} for q in queries]})
    if url == "https://api.osv.dev/v1/vulns/GHSA-test-0001":
        return httpx.Response(200, json=OSV_VULN)
    if url == "https://abc-healthcare.com/" and url in sites:
        return httpx.Response(200, text=sites[url], headers=[
            ("content-type", "text/html"), ("server", "nginx/1.18.0"), ("x-powered-by", "Express"),
            ("x-content-type-options", "nosniff"), ("set-cookie", "sid=abc123; Path=/")])
    if url == "http://abc-healthcare.com/":
        return httpx.Response(200, text="<html>plain http</html>", headers={"content-type": "text/html"})
    return None


def web_transport(sites: dict[str, str] | None = None) -> httpx.MockTransport:
    sites = sites or SITES

    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        raw = _raw(request, sites)
        if raw is not None:
            return raw
        url = unquote(str(request.url))
        prefixed = next((k for k in sites if k.endswith("*") and url.startswith(k[:-1])), None)
        if prefixed:
            body = sites[prefixed]
            return httpx.Response(200, json=body) if isinstance(body, (dict, list)) else httpx.Response(200, text=body)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        key = url if url.endswith("/") and url.count("/") == 3 else url.rstrip("/")
        if key.count("/") == 2:
            key += "/"
        body = sites.get(key, sites.get(url))
        if body is None:
            return httpx.Response(404, text="not found")
        if isinstance(body, (dict, list)):
            return httpx.Response(200, json=body)
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler)


# Fake credential, assembled at runtime so no key-shaped literal lives in the source tree.
FAKE_STRIPE_KEY = "sk" + "_live_" + "abcdefghijklmnop1234567890"

PACKAGE_JSON = json.dumps({
    "name": "abc-patient-management",
    "dependencies": {"react": "^18.2.0", "express": "^4.18.0", "pg": "^8.11.0", "stripe": "^14.0.0",
                     "nodemailer": "^6.9.0", "passport": "^0.7.0", "fullcalendar": "^6.1.0"},
    "devDependencies": {"jest": "^29.0.0"},
}, indent=2)

REPO_FILES = {
    "README.md": "# ABC Patient Management\nAppointment booking platform with Stripe payments.\n",
    "package.json": PACKAGE_JSON,
    "Dockerfile": "FROM node:20\nCOPY . .\nCMD node server.js\n",
    "server.js": f"const express = require('express');\nconst STRIPE_KEY = '{FAKE_STRIPE_KEY}';\n"
                 "app.use(cors({ origin: '*', credentials: true }));\n"
                 "const digest = crypto.createHash('md5').update(password).digest('hex');\n",
    "src/routes/auth.js": "router.post('/login', passport.authenticate('local'))\n",
    ".env": "DATABASE_URL=postgres://user:secret@db/prod\n",
}
REPO_TREE = list(REPO_FILES) + ["src/controllers/appointments.js", "src/models/patient.js", "src/views/index.ejs",
                                "tests/appointments.test.js", "package-lock.json"]


class FakeSourceControl(SourceControlProvider):
    name = "github"

    def __init__(self, ref: RepoRef, token: str | None = None, private: bool = False, fail_files: bool = False):
        self.token = token
        self.private = private
        self.fetched: list[str] = []

    async def get_repository(self, ref: RepoRef) -> RepoMetadata:
        if self.private and not self.token:
            raise RepositoryAccessDenied("private")
        return RepoMetadata(ref=ref, default_branch="main", private=self.private, description="ABC patient app")

    async def list_repositories(self, limit: int = 100) -> list[RepoMetadata]:
        return []

    async def get_languages(self, ref: RepoRef) -> dict[str, int]:
        return {"JavaScript": 90_000, "HTML": 5_000}

    async def get_tree(self, ref: RepoRef, branch: str) -> list[TreeEntry]:
        return [TreeEntry(p) for p in REPO_TREE]

    async def get_file(self, ref: RepoRef, path: str, branch: str) -> str | None:
        self.fetched.append(path)
        return REPO_FILES.get(path)

    async def list_activity(self, ref: RepoRef, limit: int = 10) -> list[ActivityItem]:
        return [ActivityItem("commit", "Add booking reminders", None, None, "2026-09-01")]

    def file_url(self, ref: RepoRef, path: str, branch: str, line_range: str | None = None) -> str:
        return f"https://github.com/{ref.full_name}/blob/{branch}/{path}"


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, landscape: bool = False) -> None:
        self.queries: list[str] = []
        self.landscape = landscape

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        self.queries.append(query)
        if "trend" in query.lower() or "adoption" in query.lower():
            return [
                SearchResult("Healthcare scheduling trends 2026", "https://healthtech-weekly.example/trends",
                             "Patient self-scheduling adoption is growing as clinics automate reminders to cut "
                             "no-shows."),
                SearchResult("AI in clinics survey", "https://clinic-survey.example/ai",
                             "More clinics are adopting AI assistants for patient questions and intake."),
            ]
        out = [
            SearchResult("MediBook - clinic scheduling", "https://medibook.io/", "AI-powered clinic scheduling"),
            SearchResult("ClinicFlow | Scheduling software", "https://clinicflow.com/", "Clinic scheduling"),
            SearchResult("Top 10 scheduling tools - G2", "https://www.g2.com/categories/scheduling", "reviews"),
            SearchResult("Random News", "https://randomnews.com/", "gossip"),
        ]
        if self.landscape:
            out += [SearchResult("BookWell clinic booking", "https://bookwell.app/", "Booking for clinics"),
                    SearchResult("CareDesk patient portal", "https://caredesk.io/", "Patient portal"),
                    SearchResult("SlotSmart salon booking", "https://slotsmart.com/", "Salon scheduling"),
                    SearchResult("VetPlan", "https://vetplan.com/", "Veterinary practice management")]
        return out


class ScriptedLLM:
    """Returns canned JSON per schema name; raises LLMUnavailable for unscripted schemas."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system: str, user: str, schema: type[BaseModel]):
        self.calls.append((schema.__name__, user))
        if schema.__name__ not in self.responses:
            raise LLMUnavailable("unscripted")
        payload = self.responses[schema.__name__]
        if callable(payload):
            payload = payload(user)
        return schema.model_validate(payload)
