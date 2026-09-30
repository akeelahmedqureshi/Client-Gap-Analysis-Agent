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


def html(title: str, body: str, desc: str = "", links: list[str] = ()) -> str:
    anchors = "".join(f'<a href="{u}">{u}</a>' for u in links)
    return (f"<html><head><title>{title}</title><meta name='description' content='{desc}'></head>"
            f"<body><nav>{anchors}</nav>{body}</body></html>")


SITES: dict[str, str] = {
    # --- client ---------------------------------------------------------
    "https://abc-healthcare.com/": html(
        "ABC Healthcare", "<h1>ABC Healthcare</h1><p>Book an appointment online with our clinics. "
        "Email reminders keep patients on time. Founded in 2012 in Austin, Texas.</p>",
        "ABC Healthcare runs patient scheduling software for clinics.",
        ["/about", "/products/patient-scheduler", "/contact", "https://www.linkedin.com/company/abc-healthcare",
         "https://twitter.com/abchealth"]),
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
        "Single sign-on (SSO) with Okta. HIPAA compliant. iOS app and Android app on Google Play.</p>",
        "Clinic scheduling with an AI assistant"),
    "https://clinicflow.com/": html(
        "ClinicFlow", "<h1>ClinicFlow</h1><p>Scheduling software for clinics with online booking, SMS reminders, "
        "analytics dashboard, patient portal and a public REST API. Pricing from $49/month. HIPAA compliant.</p>",
        "Clinic workflow and scheduling"),
    "https://randomnews.com/": html("News", "<p>Celebrity gossip and sports scores.</p>"),
}


def web_transport(sites: dict[str, str] | None = None) -> httpx.MockTransport:
    sites = sites or SITES

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        key = url if url.endswith("/") and url.count("/") == 3 else url.rstrip("/")
        if key.count("/") == 2:
            key += "/"
        body = sites.get(key)
        if body is None:
            return httpx.Response(404, text="not found")
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
    "server.js": f"const express = require('express');\nconst STRIPE_KEY = '{FAKE_STRIPE_KEY}';\n",
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

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        self.queries.append(query)
        return [
            SearchResult("MediBook - clinic scheduling", "https://medibook.io/", "AI-powered clinic scheduling"),
            SearchResult("ClinicFlow | Scheduling software", "https://clinicflow.com/", "Clinic scheduling"),
            SearchResult("Top 10 scheduling tools - G2", "https://www.g2.com/categories/scheduling", "reviews"),
            SearchResult("Random News", "https://randomnews.com/", "gossip"),
        ]


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
