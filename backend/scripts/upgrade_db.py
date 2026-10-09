"""Bring an existing database up to the current schema, then record it as the newest Alembic revision.

For databases that were created by ``create_all`` (development start-up) or by an older version of the app,
where ``alembic upgrade head`` would fail on tables that already exist. Additive only: it creates missing
tables and adds missing columns (existing rows get the column's default); nothing is dropped or rewritten.
Back up the database first.

    cd backend
    python scripts/upgrade_db.py            # uses CIP_DATABASE_URL (default: sqlite ./cip.db)
    python scripts/upgrade_db.py --check    # only list what is missing
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cip.config import get_settings  # noqa: E402
from cip.db import session as db  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report missing columns without changing anything")
    args = parser.parse_args()
    print(f"Database: {get_settings().database_url.split('@')[-1]}")
    if args.check:
        missing = (await db.check_schema(repair=False))["missing_columns"]
        print("Missing columns: " + (", ".join(missing) if missing else "none"))
        await db.dispose()
        return 1 if missing else 0
    result = await db.check_schema(repair=True)
    await db.dispose()
    print("Added columns: " + (", ".join(result["added_columns"]) or "none"))
    print(f"Recorded migration: {result['stamped'] or 'unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
