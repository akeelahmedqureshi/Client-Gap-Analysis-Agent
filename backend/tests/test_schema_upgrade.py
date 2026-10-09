"""A database created by an older version (and then touched by create_all) is upgraded in place."""

import asyncio
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from cip.db import session as db

BACKEND = Path(__file__).resolve().parents[1]


def test_old_database_is_upgraded_without_losing_data(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("CIP_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    from cip.config import get_settings
    get_settings.cache_clear()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "0006")  # the schema of an older release
    con = sqlite3.connect(path)
    con.execute("insert into organizations (id, name, created_at) values ('org_1', 'Acme', '2026-10-01')")
    con.commit()
    con.close()

    async def run() -> tuple[dict, dict, dict]:
        db.configure()
        await db.create_all()  # what an older development start-up did: new tables, but no new columns
        before = await db.check_schema(repair=False)
        fixed = await db.check_schema(repair=True)
        after = await db.check_schema(repair=False)
        await db.dispose()
        return before, fixed, after

    try:
        before, fixed, after = asyncio.run(run())
    finally:
        get_settings.cache_clear()
    assert {"analysis_runs.parent_run_id", "organizations.settings"} <= set(before["missing_columns"])
    assert set(fixed["added_columns"]) == set(before["missing_columns"]) and fixed["stamped"]
    assert after["missing_columns"] == []
    con = sqlite3.connect(path)
    assert con.execute("select name, settings from organizations").fetchall() == [("Acme", "{}")]
    assert con.execute("select version_num from alembic_version").fetchone()[0] == fixed["stamped"]
