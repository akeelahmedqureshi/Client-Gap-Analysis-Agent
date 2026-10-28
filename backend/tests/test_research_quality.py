"""Research coverage and evidence grades: the crawler reads the pages that describe the offering first, and
a keyword on a blog post, job advert or legal page never proves a capability."""

from cip.agents.competitor_research import _keyword_features
from cip.agents.product_features import combine_signals
from cip.connectors.research.pages import kind_of, link_priority
from cip.connectors.research.web import WebFetcher, parse_html

from fakes import html, web_transport


def test_page_kinds_in_several_languages():
    cases = {"https://x.com/": "home", "https://x.com/features/chat": "features", "https://x.com/pricing": "pricing",
             "https://x.es/precios": "pricing", "https://x.de/funktionen": "features", "https://x.fr/a-propos": "about",
             "https://x.com/docs/api/auth": "docs", "https://x.com/blog/new-features-for-you": "news",
             "https://x.com/careers/engineer": "careers", "https://x.com/privacy-policy": "legal",
             "https://x.com/login": "account", "https://x.com/solutions/clinics": "product"}
    assert {u: kind_of(u) for u in cases} == cases
    assert kind_of("https://x.com/xyz", anchor="Pricing") == "pricing"   # the link text tells
    assert link_priority("https://x.com/features") > link_priority("https://x.com/blog") \
        > link_priority("https://x.com/blog/some-post") > link_priority("https://x.com/blog/tag/ai")


async def test_crawl_spends_its_budget_on_the_offering_not_on_blog_posts():
    site = "https://shop.example"
    posts = [f"/blog/post-{i}" for i in range(12)]
    sites = {
        f"{site}/": html("Shop", "<h1>Shop software</h1>", links=[*posts, "/privacy", "/products/pos", "/blog"]),
        f"{site}/products/pos": html("POS", "<h1>Point of sale</h1>", links=["/products/pos/inventory", "/features"]),
        f"{site}/products/pos/inventory": html("Inventory", "<h1>Inventory sync</h1>"),
        f"{site}/features": html("Features", "<h1>Features</h1>"),
        f"{site}/privacy": html("Privacy", "<h1>Privacy</h1>"),
        f"{site}/blog": html("Blog", "<h1>Blog</h1>"),
        **{f"{site}{p}": html("Post", "<p>Story</p>") for p in posts},
    }
    pages = await WebFetcher(transport=web_transport(sites)).crawl(site, max_pages=8)
    urls = [p.url.rstrip("/") for p in pages]
    assert {f"{site}/products/pos", f"{site}/products/pos/inventory", f"{site}/features"} <= set(urls)
    assert f"{site}/privacy" not in urls                                   # legal pages are never read
    assert sum(1 for u in urls if "/blog" in u) <= 2                      # news pages are capped


def test_client_evidence_grades():
    def web(fid, page, kws=1):
        return {"feature_id": fid, "status": "available", "evidence_id": f"ev_{fid}_{page}", "source": "website",
                "confidence": 0.5, "keywords": kws, "page": page}

    obs = combine_signals([
        web("comm.sms", "https://x.com/about"),                                # one keyword, one non-offering page
        web("comm.chat", "https://x.com/features"),                            # on a features page
        web("comm.video", "https://x.com/about", 2),                           # two keywords
        web("billing.payments", "https://x.com/about"), web("billing.payments", "https://x.com/customers"),
        {"feature_id": "ai.assistant", "status": "partial", "evidence_id": "ev_q", "source": "llm_extraction",
         "confidence": 0.8},
    ], {"website": True})
    assert (obs["comm.sms"].status.value, obs["comm.sms"].verification) == ("unknown", "mentioned")
    assert "not confirmed" in obs["comm.sms"].notes
    for fid in ("comm.chat", "comm.video", "billing.payments"):
        assert (obs[fid].status.value, obs[fid].verification) == ("available", "evidenced"), fid
    assert (obs["ai.assistant"].status.value, obs["ai.assistant"].verification) == ("partial", "documented")


def test_competitor_keywords_on_blog_or_legal_pages_prove_nothing(make_ctx):
    ctx = make_ctx()
    pages = [parse_html("https://rival.example/blog/ai-trends", 200, html("AI trends", "<p>AI chatbot hype.</p>")),
             parse_html("https://rival.example/careers", 200, html("Jobs", "<p>Build our video call stack.</p>")),
             parse_html("https://rival.example/about", 200, html("About", "<p>We send SMS reminders.</p>")),
             parse_html("https://rival.example/features", 200, html("Features", "<p>Online payments.</p>"))]
    obs = _keyword_features(ctx, "Rival", pages, {})
    assert "ai.assistant" not in obs and "comm.video" not in obs       # blog post, job advert: ignored
    assert (obs["comm.sms"].status.value, obs["comm.sms"].verification) == ("unknown", "mentioned")
    assert (obs["billing.payments"].status.value, obs["billing.payments"].verification) == ("available", "evidenced")
