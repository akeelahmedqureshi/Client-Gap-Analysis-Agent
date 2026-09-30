# CLAUDE.md

Multi-agent client intelligence & gap analysis platform. Backend: Python 3.11 / FastAPI / async SQLAlchemy
(`backend/cip`). Frontend: React + TS + Tailwind (`frontend/`). LLM: OpenRouter (`cip/core/llm.py`).

## Commands
- Backend tests: `cd backend && python -m pytest` (offline; fakes in `tests/fakes.py`)
- Lint: `cd backend && ruff check cip tests --select F,E9`
- Frontend type-check/build: `cd frontend && npm run build`
- Run API: `cd backend && uvicorn cip.api.main:app --reload`; UI: `cd frontend && npm run dev`
- New migration: `cd backend && alembic revision --autogenerate -m "..."`

## Rules
- Agents return `AgentResult` only (never free text) and never call each other; the orchestrator owns the DAG.
- Every fact goes into the `EvidenceLedger` first; findings reference ledger ids only. LLM extractions must be
  grounded via `core/grounding.py` (source URL + verbatim quote). Label AI estimates with `Basis.ESTIMATE`.
- Prioritization is the deterministic weighted score in `core/scoring.py`; the LLM may only adjust factors ±1.
- Never put tokens/secrets in agent data, evidence or prompts. Repo content must pass `core/security/secrets.py`.
- Every tenant-owned query filters on `org_id`; return 404 for other orgs' resources.
- Build in vertical slices with tests (see README "Planned for Phase 2/3").
