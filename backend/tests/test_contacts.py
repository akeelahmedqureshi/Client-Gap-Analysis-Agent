"""Client contacts and official profiles: collected from the official website and verified strictly."""

import json

import httpx

from cip.connectors.research import contacts as cr
from cip.connectors.research.web import WebFetcher

from fakes import html

SITE = "https://acme-dental.com"
JSONLD = json.dumps({"@context": "https://schema.org", "@type": "Organization", "name": "Acme Dental",
                     "address": {"@type": "PostalAddress", "streetAddress": "12 Main St", "addressLocality": "Austin",
                                 "addressRegion": "TX", "postalCode": "78701", "addressCountry": "US"},
                     "sameAs": ["https://www.youtube.com/@acmedental", "https://github.com/acme-dental"]})
PAGES = {
    f"{SITE}/": html("Acme Dental | Smile care", "<h1>Acme Dental</h1><p>Family dentistry in Austin.</p>"
                     "<footer>Call +1 512 555 0199</footer>",
                     links=["/contact", "/about",
                            "https://www.linkedin.com/company/acme-dental",       # official, identifies -> verified
                            "https://www.linkedin.com/company/acme-dental-old",   # a different organization
                            "https://www.linkedin.com/in/jane-doe",                # personal: never collected
                            "https://www.linkedin.com/shareArticle?url=x",         # share link: never collected
                            "https://gitlab.com/acme-dental",                      # redirects to sign-in
                            "https://apps.apple.com/us/app/acme-dental/id123",     # gone
                            "https://twitter.com/acmedental", "https://www.facebook.com/acmedental",
                            "https://www.instagram.com/acmedental"],
                     head=f'<script type="application/ld+json">{JSONLD}</script>'),
    f"{SITE}/contact": html("Contact", "<p>Write to <a href='mailto:hello@acme-dental.com'>us</a>, our agency "
                                       "<a href='mailto:info@agency-mail.com'>here</a> or the dentist "
                                       "<a href='mailto:jane.doe@acme-dental.com'>Jane</a>. Call +1 512 555 0100.</p>"
                                       "<address>12 Main St, Austin, TX 78701</address>"),
    f"{SITE}/about": html("About Acme Dental", "<h1>About us</h1><p>Since 1998.</p>"),
    "https://www.linkedin.com/company/acme-dental": html(
        "Acme Dental | LinkedIn", "<p>Acme Dental · Hospitals and Health Care · Austin</p>",
        head="<meta property='og:site_name' content='Acme Dental'>"),
    "https://www.linkedin.com/company/acme-dental-old": html("Acme Partners LLP | LinkedIn", "<p>Legal services</p>"),
    "https://github.com/acme-dental": html("Acme Dental · GitHub", "<p>Open source tools by Acme Dental</p>"),
    "https://www.linkedin.com/company/acme-csv": html("Acme Dental | LinkedIn", "<p>Acme Dental</p>"),
}


def transport(blocked: tuple[str, ...] = ()) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).rstrip("/")
        if url.endswith("/robots.txt"):
            host = request.url.host
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n") if host in blocked else httpx.Response(404)
        if url == "https://gitlab.com/acme-dental":
            return httpx.Response(302, headers={"location": "https://gitlab.com/users/sign_in"})
        if url == "https://gitlab.com/users/sign_in":
            return httpx.Response(200, text=html("Sign in · GitLab", "<form></form>"),
                                  headers={"content-type": "text/html"})
        body = PAGES.get(url) or PAGES.get(url + "/")
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})
    return httpx.MockTransport(handler)


async def research(blocked=(), extra=()):
    fetcher = WebFetcher(transport=transport(blocked))
    pages = await fetcher.crawl(SITE, max_pages=5)
    found, ignored = cr.collect(pages, "Acme Dental", "acme-dental.com", list(extra))
    return await cr.verify(fetcher, found, "Acme Dental", "acme-dental.com", pages), ignored


def by_value(contacts):
    return {c.value.rstrip("/"): c for c in contacts}


async def test_only_business_contacts_and_official_profiles_are_collected():
    contacts, ignored = await research()
    types = {c.type for c in contacts}
    assert not types & {"twitter", "facebook", "instagram", "youtube"}   # excluded platforms
    values = by_value(contacts)
    assert "https://www.linkedin.com/in/jane-doe" not in values and not any("shareArticle" in v for v in values)
    assert "jane.doe@acme-dental.com" not in values and ignored == 1        # personal email never collected
    assert {"website", "contact_page", "about_page", "email", "phone", "address", "linkedin", "github"} <= types


async def test_verified_means_official_and_the_destination_names_the_company():
    contacts, _ = await research()
    v = by_value(contacts)
    li = v["https://www.linkedin.com/company/acme-dental"]
    assert li.status == "verified" and li.account_name == "Acme Dental" and li.official_source
    assert "published on the official website" in li.status_detail and li.verified_at and li.method
    gh = v["https://github.com/acme-dental"]          # from schema.org sameAs
    assert gh.status == "verified" and gh.source.rstrip("/") == SITE
    assert v[SITE].status == "verified" and v[f"{SITE}/contact"].status == "verified"
    assert v["hello@acme-dental.com"].status == "verified"
    assert v["info@agency-mail.com"].status == "unverified"                 # another domain
    phones = [c for c in contacts if c.type == "phone"]
    assert phones and all(c.status == "verified" for c in phones)
    addresses = [c.value for c in contacts if c.type == "address"]
    assert any("12 Main St" in a and "78701" in a for a in addresses)


async def test_links_that_cannot_be_confirmed_are_flagged_not_verified():
    contacts, _ = await research()
    v = by_value(contacts)
    other = v["https://www.linkedin.com/company/acme-dental-old"]
    assert other.status == "mismatch" and "Acme Partners LLP" in other.status_detail
    gl = v["https://gitlab.com/acme-dental"]
    assert gl.status == "official_link" and "signing in" in gl.status_detail      # sign-in wall: not verified
    app = v["https://apps.apple.com/us/app/acme-dental/id123"]
    assert app.status == "inaccessible"


async def test_robots_rules_and_unofficial_sources_are_never_verified():
    contacts, _ = await research(blocked=("www.linkedin.com",))
    li = by_value(contacts)["https://www.linkedin.com/company/acme-dental"]
    assert li.status == "official_link" and "automated checks" in li.status_detail

    contacts, _ = await research(extra=[("https://www.linkedin.com/company/acme-csv", "csv://row/2")])
    csv_li = by_value(contacts)["https://www.linkedin.com/company/acme-csv"]
    assert csv_li.status == "unverified" and "does not link to it" in csv_li.status_detail


async def test_client_research_stores_verified_contacts_with_evidence(make_ctx):
    from cip.agents.client_research import ClientResearchAgent

    ctx = make_ctx(fetcher=WebFetcher(transport=transport()))
    ctx.record.client.name, ctx.record.client.domain = "Acme Dental", "acme-dental.com"
    ctx.record.project.url = SITE
    result = await ClientResearchAgent().run(ctx)
    contacts = result.data["profile"]["contacts"]
    li = next(c for c in contacts if c["value"] == "https://www.linkedin.com/company/acme-dental")
    assert li["status"] == "verified" and len(li["evidence_ids"]) == 2
    sources = {ctx.ledger.get(e).source_url.rstrip("/") for e in li["evidence_ids"]}
    assert sources == {SITE, li["value"]}  # where it is published, and the profile that names the company
    assert result.data["linkedin_url"] == li["value"]
