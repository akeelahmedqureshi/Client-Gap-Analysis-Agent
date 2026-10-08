from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cip.api.deps import require_role
from cip.api.routes import auth, connections, knowledge, monitoring, projects, runs, sales, uploads, users
from cip.config import get_settings
from cip.core.scoring import ScoringConfig
from cip.core.taxonomy import load_taxonomy
from cip.db import session as db
from cip.services.ratelimit import limiter
from cip.services.runner import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.environment == "production" and settings.jwt_secret == "change-me-in-production":
        raise RuntimeError("CIP_JWT_SECRET must be set in production")
    db.configure()
    if settings.environment != "production":
        await db.create_all()
    if settings.resume_runs_on_startup:
        try:
            await runner.resume_interrupted()
        except Exception:  # noqa: BLE001 - e.g. Redis briefly unavailable; never block API start-up
            logging.getLogger(__name__).exception("Could not resume interrupted runs at start-up")
    scheduler = None
    if settings.monitor_scheduler_enabled:
        from cip.services.monitoring import scheduler_loop

        scheduler = asyncio.create_task(scheduler_loop(), name="monitor-scheduler")
    yield
    if scheduler is not None:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
    await limiter.aclose()
    await db.dispose()


app = FastAPI(title="Client Intelligence Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth.router, users.router, uploads.router, projects.router, runs.router, connections.router,
          monitoring.router, knowledge.router, sales.router):
    app.include_router(r)


@app.get("/api/health")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "llm": {"provider": "openrouter", "model": s.openrouter_model, "enabled": s.llm_enabled},
            "search_provider": s.search_provider}


@app.get("/api/meta/taxonomy", dependencies=[Depends(require_role("viewer"))])
async def taxonomy() -> list[dict]:
    return [{"id": f.id, "name": f.name, "category": f.category_name, "ai": f.ai, "defaults": f.defaults}
            for f in load_taxonomy().features]


@app.get("/api/meta/scoring", dependencies=[Depends(require_role("viewer"))])
async def scoring() -> dict:
    return ScoringConfig().model_dump()
