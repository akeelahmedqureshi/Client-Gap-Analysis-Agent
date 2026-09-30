import base64
import json

import httpx
import pytest

from cip.connectors.research.web import UnsafeURL, WebFetcher, assert_public_url, parse_html
from cip.connectors.source_control import GitHubProvider, GitLabProvider, RepositoryAccessDenied, parse_repo_url

from fakes import web_transport


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/abc/project", ("github", "abc/project")),
    ("github.com/abc/project.git", ("github", "abc/project")),
    ("git@github.com:abc/project.git", ("github", "abc/project")),
    ("https://www.github.com/abc/project/tree/main/src", ("github", "abc/project")),
    ("https://gitlab.com/group/sub/proj/-/blob/main/x.py", ("gitlab", "group/sub/proj")),
    ("https://gitlab.example.com/team/app", ("gitlab", "team/app")),
])
def test_parse_repo_url(url, expected):
    ref = parse_repo_url(url)
    assert (ref.provider, ref.full_name) == expected


@pytest.mark.parametrize("url", ["https://github.com/abc", "https://abc.github.io/x", "https://example.com/a/b"])
def test_parse_repo_url_rejects(url):
    assert parse_repo_url(url) is None


async def test_github_provider_reads_tree_and_files():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer tok"
        p = req.url.path
        if p == "/repos/abc/project":
            return httpx.Response(200, json={"default_branch": "main", "private": True, "description": "d"})
        if p == "/repos/abc/project/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "a.py", "type": "blob", "size": 3},
                                                      {"path": "src", "type": "tree"}]})
        if p == "/repos/abc/project/contents/a.py":
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"x=1").decode()})
        return httpx.Response(404)

    gh = GitHubProvider(token="tok", transport=httpx.MockTransport(handler))
    ref = parse_repo_url("https://github.com/abc/project")
    meta = await gh.get_repository(ref)
    assert meta.private and meta.default_branch == "main"
    assert [t.path for t in await gh.get_tree(ref, "main")] == ["a.py"]
    assert await gh.get_file(ref, "a.py", "main") == "x=1"
    assert await gh.get_file(ref, "missing.py", "main") is None
    assert gh.file_url(ref, "a.py", "main", "3-5") == "https://github.com/abc/project/blob/main/a.py#L3-L5"


async def test_github_private_without_token_is_access_denied():
    gh = GitHubProvider(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(RepositoryAccessDenied):
        await gh.get_repository(parse_repo_url("https://github.com/abc/private"))


async def test_gitlab_tree_pagination():
    pages = {"1": ([{"path": "a.py", "type": "blob"}], "2"), "2": ([{"path": "b.py", "type": "blob"}], "")}

    def handler(req: httpx.Request) -> httpx.Response:
        assert "group%2Fproj" in str(req.url)
        items, nxt = pages[req.url.params["page"]]
        return httpx.Response(200, content=json.dumps(items), headers={"x-next-page": nxt})

    gl = GitLabProvider(token="t", base_url="https://gitlab.com", transport=httpx.MockTransport(handler))
    tree = await gl.get_tree(parse_repo_url("https://gitlab.com/group/proj"), "main")
    assert [t.path for t in tree] == ["a.py", "b.py"]


async def test_crawler_follows_priority_links_and_extracts_contacts():
    pages = await WebFetcher(transport=web_transport()).crawl("https://abc-healthcare.com", max_pages=10)
    urls = [p.url.rstrip("/") for p in pages]
    assert urls[0] == "https://abc-healthcare.com"
    assert "https://abc-healthcare.com/products/patient-scheduler" in urls
    contact = next(p for p in pages if p.url.endswith("/contact"))
    assert "support@abc-healthcare.com" in contact.emails


def test_parse_html_strips_scripts():
    page = parse_html("https://x.com/", 200, "<html><body><script>var s='hidden'</script><p>Visible</p></body></html>")
    assert "Visible" in page.text and "hidden" not in page.text


async def test_ssrf_guard_blocks_private_addresses():
    for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/",
                "file:///etc/passwd"):
        with pytest.raises(UnsafeURL):
            await assert_public_url(url)
