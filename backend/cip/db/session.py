from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import JSON, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from cip.config import get_settings
from cip.db.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    url = database_url or get_settings().database_url
    kwargs = {"connect_args": {"timeout": 30}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    _engine = create_async_engine(url, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    return _sessionmaker or configure()


async def dispose() -> None:
    """Close pooled connections (app shutdown / test teardown)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def create_all() -> None:
    """Development convenience; production uses Alembic migrations."""
    sessionmaker()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --------------------------------------------------------------------------- schema drift
#
# ``create_all`` creates missing tables but never adds columns to tables that already exist, so a database
# created by an older version (e.g. a development SQLite file) can miss columns the code now uses. These
# helpers find such columns, add them (additive ALTER TABLE only, with the model's default for existing rows)
# and record the schema as the newest Alembic revision so later ``alembic upgrade head`` runs work.

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def missing_columns(conn) -> list[tuple[str, str]]:
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    out = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        out += [(table.name, c.name) for c in table.columns if c.name not in have]
    return out


def _default_sql(col, dialect) -> str | None:
    if col.server_default is not None and hasattr(col.server_default, "arg"):
        arg = col.server_default.arg
        if isinstance(arg, str):
            return arg if arg.lstrip("-").isdigit() else "'" + arg.replace("'", "''") + "'"
        return str(arg.compile(dialect=dialect))
    value = None
    if col.default is not None:
        value = col.default.arg(None) if col.default.is_callable else col.default.arg
    elif not col.nullable:
        value = {} if isinstance(col.type, JSON) else None
    if value is None:
        return None
    if isinstance(col.type, JSON) or isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'"
    if isinstance(value, bool):
        return ("true" if value else "false") if dialect.name == "postgresql" else str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None  # e.g. timestamps: left NULL for existing rows


def add_missing_columns(conn) -> list[str]:
    added = []
    for table, name in missing_columns(conn):
        col = Base.metadata.tables[table].columns[name]
        ddl = f'ALTER TABLE "{table}" ADD COLUMN "{name}" {col.type.compile(dialect=conn.dialect)}'
        default = _default_sql(col, conn.dialect)
        if default is not None:
            ddl += f" DEFAULT {default}"
            if not col.nullable:
                ddl += " NOT NULL"
        conn.execute(text(ddl))
        added.append(f"{table}.{name}")
    return added


def stamp_head(conn) -> str | None:
    """Record the newest migration as applied (only call once the schema matches the models)."""
    if not MIGRATIONS_DIR.exists():
        return None
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
    conn.execute(text("DELETE FROM alembic_version"))
    conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})
    return head


def sync_schema(conn) -> dict:
    """Create missing tables, add missing columns, stamp the Alembic head. Additive only; never drops data."""
    Base.metadata.create_all(conn)
    added = add_missing_columns(conn)
    head = stamp_head(conn) if added or not inspect(conn).has_table("alembic_version") else None
    return {"added_columns": added, "stamped": head}


async def table_names() -> list[str]:
    sessionmaker()
    assert _engine is not None
    async with _engine.connect() as conn:
        return await conn.run_sync(lambda c: inspect(c).get_table_names())


async def alembic_revision() -> str | None:
    if "alembic_version" not in await table_names():
        return None
    async with _engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()


async def check_schema(repair: bool) -> dict:
    """At start-up: repair drift (development) or report it (production)."""
    sessionmaker()
    assert _engine is not None
    async with _engine.begin() as conn:
        if repair:
            return await conn.run_sync(sync_schema)
        return {"missing_columns": [f"{t}.{c}" for t, c in await conn.run_sync(missing_columns)]}


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session
