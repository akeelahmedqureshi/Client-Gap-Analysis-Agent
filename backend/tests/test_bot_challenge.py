"""Bot-protection challenges (e.g. AWS WAF's HTTP 202 + script) are recognised, passed with the browser when
possible, and otherwise reported as "bot protection" instead of being read as an empty page of the site."""

from contextlib import asynccontextmanager

import httpx

from cip.connectors.research.browser import RenderedPage
from cip.connectors.research.domain import check_domain
from cip.connectors.research.web import WebFetcher, bot_challenge

AWS_CHALLENGE = ('<html><head><script src="https://abc.token.awswaf.com/abc/challenge.js"></script>'
                 '<script>AwsWafIntegration.checkForceRefresh()</script></head><body><div id="challenge-container">'
                 '</div><noscript>JavaScript is disabled</noscript></body></html>')
REAL = "<html><head><title>AJ Lakes</title></head><body><h1>AJ Lakes</h1><p>" + \
       "Family-owned lakeside resort with cabins, boat rentals and fishing trips. " * 12 + "</p></body></html>"


def challenge_transport(headers=None):
    return httpx.MockTransport(lambda r: httpx.Response(
        202, text="" if r.url.path == "/robots.txt" else AWS_CHALLENGE,
        headers={"content-type": "text/html", **(headers or {"x-amzn-waf-action": "challenge"})}))


class PassingBrowser:
    """A renderer whose browser gets through the challenge, as a real one does."""
    available = True

    def __init__(self, html=REAL):
        self.html, self.calls = html, 0

    @asynccontextmanager
    async def session(self):
        yield self

    async def render(self, url):
        self.calls += 1
        return RenderedPage(url=url, status=202, html=self.html)


SITEGROUND = ('<html><head><title>Robot Challenge Screen</title><meta http-equiv="refresh" '
              'content="0;/.well-known/sgcaptcha/?r=%2F&y=ipc:1.2.3.4:1"></head><body>'
              '<p>Please wait while your request is being verified...</p></body></html>')


def test_challenge_signals():
    assert bot_challenge(202, {}, SITEGROUND) == "SiteGround"
    assert bot_challenge(202, {}, "<html><body><p>One moment please.</p></body></html>").startswith("unidentified")
    assert bot_challenge(202, {"x-amzn-waf-action": "challenge"}, "") == "AWS WAF"
    assert bot_challenge(202, {}, AWS_CHALLENGE) == "AWS WAF"
    assert bot_challenge(403, {"cf-mitigated": "challenge"}, "") == "Cloudflare"
    assert "HTTP 202" in bot_challenge(202, {}, "<html><body></body></html>")
    # a normal page that merely includes a protection script is not a challenge
    assert bot_challenge(200, {}, REAL.replace("</body>", '<script src="/cdn-cgi/challenge-platform/x.js"></script>'
                                                         "</body>")) is None
    assert bot_challenge(200, {}, REAL) is None


async def test_challenge_without_a_browser_is_reported_not_read_as_an_empty_page():
    f = WebFetcher(transport=challenge_transport(), rendering="never")
    assert await f.fetch("https://www.ajlakes.test/") is None
    assert f.failures[-1]["reason"] == "bot_challenge" and f.failures[-1]["detail"] == "AWS WAF"

    check = await check_domain(WebFetcher(transport=challenge_transport(), rendering="never"), "www.ajlakes.test")
    assert check["status"] == "blocked" and check["reason"] == "bot_challenge"
    assert "AWS WAF" in check["detail"] and "playwright install" in check["detail"]


async def test_the_browser_passes_the_challenge():
    browser = PassingBrowser()
    f = WebFetcher(transport=challenge_transport(), rendering="auto", renderer=browser)
    page = await f.fetch("https://www.ajlakes.test/")
    assert page is not None and page.rendered and page.title == "AJ Lakes" and "boat rentals" in page.text

    check = await check_domain(WebFetcher(transport=challenge_transport(), rendering="auto", renderer=PassingBrowser()),
                               "www.ajlakes.test")
    assert check["status"] == "ok" and "passed with the browser" in check["detail"]


async def test_a_browser_that_only_sees_the_challenge_again_is_a_failure():
    for page in (AWS_CHALLENGE, SITEGROUND):  # the SiteGround screen has some text: still not the site
        f = WebFetcher(transport=challenge_transport(), rendering="auto", renderer=PassingBrowser(html=page))
        assert await f.fetch("https://www.ajlakes.test/") is None
        assert f.failures[-1]["reason"] == "bot_challenge"

        check = await check_domain(WebFetcher(transport=challenge_transport(), rendering="auto",
                                              renderer=PassingBrowser(html=page)), "www.ajlakes.test")
        assert check["status"] == "blocked" and "could not pass it either" in check["detail"]
