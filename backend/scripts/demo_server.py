"""Offline demo: the full API (and, if built, the web UI) against a simulated web.

Everything a real analysis touches — client and competitor websites, web search, GitHub, the App
Store, Google Play, OSV.dev — is served by the same fakes the test-suite uses (``tests/fakes.py``),
so you can click through the whole product with no API keys and no internet access.

    cd backend
    python scripts/demo_server.py                 # API on http://localhost:8000
    python scripts/demo_server.py --client-app    # the fake client also has an iOS app with reviews
    python scripts/demo_server.py --landscape     # six competitors: Top-10 ranking and Top-3 deep analysis

Then either ``cd frontend && npm run dev`` (http://localhost:5173) or build the UI first
(``npm run build``) and open http://localhost:8000 — the demo serves ``frontend/dist`` when present.

Upload ``examples/clients.csv``; the "ABC Patient Management" project is wired to the fake web.

Demo-only helpers (never part of the real app):

    curl -X POST localhost:8000/demo/change    # the fake world changes (competitor price cut, new blog post…)
    curl -X POST localhost:8000/demo/due       # make every monitor due now (the scheduler starts it within 5s)
    curl localhost:8000/demo/webhooks          # alert webhooks the demo captured instead of sending

This script refuses to run with CIP_ENVIRONMENT=production.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data-dir", help="where the demo database and uploads live (default: a temp dir)")
    parser.add_argument("--client-app", action="store_true", help="give the fake client an iOS app with reviews")
    parser.add_argument("--landscape", action="store_true",
                        help="a wider competitive landscape (6 competitors: Top-10 ranking + Top-3 deep analysis)")
    args = parser.parse_args()

    if os.environ.get("CIP_ENVIRONMENT", "").lower() == "production":
        sys.exit("demo_server.py is for local demos only; refusing to run with CIP_ENVIRONMENT=production")

    data = Path(args.data_dir or tempfile.mkdtemp(prefix="cip-demo-")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    # Settings are read on first use, so configure the environment before importing the app.
    os.environ.update({
        "CIP_ENVIRONMENT": "development",
        "CIP_DATABASE_URL": f"sqlite+aiosqlite:///{data / 'demo.db'}",
        "CIP_STORAGE_DIR": str(data / "storage"),
        "CIP_MONITOR_POLL_SECONDS": "5",
        "CIP_OPENROUTER_API_KEY": "",          # deterministic pipeline only
        "CIP_SEARCH_PROVIDER": "none",          # the fake search below is injected per run
        "CIP_APP_BASE_URL": f"http://localhost:{args.port}",
        "CIP_CORS_ORIGINS": json.dumps([f"http://localhost:{args.port}", "http://localhost:5173"]),
    })
    os.environ.setdefault("CIP_JWT_SECRET", "demo-only-secret-" + "x" * 32)
    sys.path.insert(0, str(BACKEND / "tests"))

    import httpx
    import uvicorn
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from sqlalchemy import update

    from cip.api.main import app
    from cip.connectors.research.web import WebFetcher
    from cip.db import session as db
    from cip.db.models import Monitor
    from cip.services import notify
    from cip.services.runner import runner
    from fakes import (
        LANDSCAPE_SITES,
        SITES,
        FakeSearch,
        FakeSourceControl,
        html,
        sites_with_client_app,
        web_transport,
    )

    sites = sites_with_client_app() if args.client_app else dict(SITES)
    if args.landscape:
        sites.update(LANDSCAPE_SITES)
    captured: list[dict] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    notify.webhook_transport = httpx.MockTransport(capture)

    def use_fakes(ctx) -> None:
        ctx.fetcher = WebFetcher(transport=web_transport(sites))
        ctx.search = FakeSearch(landscape=args.landscape)
        ctx.source_control_factory = lambda ref, token: FakeSourceControl(ref, token)

    runner.context_hook = use_fakes
    from cip.api.routes import uploads
    uploads.domain_fetcher = lambda: WebFetcher(transport=web_transport(sites))  # "Check domains" in the preview

    @app.post("/demo/change", include_in_schema=False)
    async def change() -> dict:
        sites["https://clinicflow.com/pricing"] = SITES["https://clinicflow.com/pricing"].replace("$49", "$39")
        sites["https://abc-healthcare.com/blog"] = SITES["https://abc-healthcare.com/blog"].replace(
            "<a href='/blog/page/2'>",
            "<a href='/blog/introducing-video-visits'>Introducing video visits</a><a href='/blog/page/2'>")
        sites["https://abc-healthcare.com/blog/introducing-video-visits"] = html("Video visits", "<p>Video.</p>")
        sites["https://medibook.io/"] = SITES["https://medibook.io/"].replace(
            "HIPAA compliant.", "HIPAA compliant. Online payments and invoicing.")
        return {"changed": ["ClinicFlow price $49 -> $39", "client blog post", "MediBook invoicing"]}

    @app.post("/demo/due", include_in_schema=False)
    async def due() -> dict:
        from datetime import datetime, timedelta, timezone

        async with db.sessionmaker()() as s:
            await s.execute(update(Monitor).values(next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
            await s.commit()
        return {"ok": True, "note": "the scheduler starts due monitors within a few seconds"}

    @app.get("/demo/webhooks", include_in_schema=False)
    async def webhooks() -> list[dict]:
        return captured

    dist = REPO / "frontend" / "dist"
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            return FileResponse(dist / "index.html")

    print(f"Demo data: {data}")
    print(f"API: http://localhost:{args.port}/docs" + (f"  UI: http://localhost:{args.port}" if dist.exists() else
                                                       "  UI: cd frontend && npm run dev -> http://localhost:5173"))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
