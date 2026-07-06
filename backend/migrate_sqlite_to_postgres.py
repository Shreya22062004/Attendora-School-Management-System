"""One-time migration of the existing SQLite data to central PostgreSQL.
Run from backend: python migrate_sqlite_to_postgres.py
DATABASE_URL must point to PostgreSQL.
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, MetaData, select
from app.database import Base
import app.models  # register all models

postgres_url = os.getenv("DATABASE_URL")
if not postgres_url or not postgres_url.startswith("postgresql"):
    raise SystemExit("Set DATABASE_URL to your PostgreSQL URL before running migration.")
sqlite_file = Path(__file__).resolve().parent / "school_attendance.db"
if not sqlite_file.exists():
    raise SystemExit(f"SQLite source not found: {sqlite_file}")

src = create_engine(f"sqlite:///{sqlite_file.as_posix()}")
dst = create_engine(postgres_url, pool_pre_ping=True)
Base.metadata.create_all(dst)
source_meta = MetaData(); source_meta.reflect(bind=src)
target_meta = MetaData(); target_meta.reflect(bind=dst)
order = ["users", "school_settings", "students", "attendance_sessions", "attendance", "attendance_history", "school_calendar"]
with src.connect() as sconn, dst.begin() as dconn:
    for name in order:
        if name not in source_meta.tables or name not in target_meta.tables:
            continue
        source_table = source_meta.tables[name]; target_table = target_meta.tables[name]
        rows = [dict(r._mapping) for r in sconn.execute(select(source_table)).all()]
        if rows:
            existing = dconn.execute(select(target_table.c.id).limit(1)).first() if "id" in target_table.c else None
            if existing:
                print(f"SKIP {name}: target already contains data")
                continue
            valid = set(target_table.c.keys())
            cleaned = [{k:v for k,v in row.items() if k in valid} for row in rows]
            dconn.execute(target_table.insert(), cleaned)
        print(f"{name}: {len(rows)} rows migrated")
print("Migration complete. Verify counts before production use.")
