from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cip.api.deps import require_role
from cip.api.routes import auth, connections, projects, runs, uploads, users
from cip.config import get_settings
from cip.core.scoring import ScoringConfig
from cip.core.taxonomy import load_taxonomy
from cip.db import session as db
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
        await runner.resume_interrupted()
    yield


app = FastAPI(title="Client Intelligence Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth.router, users.router, uploads.router, projects.router, runs.router, connections.router):
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
