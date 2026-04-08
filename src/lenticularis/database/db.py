"""
SQLite database engine and session management.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from lenticularis.database.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def _run_column_migrations(engine) -> None:
    """Add columns introduced after the initial schema — safe to re-run (idempotent)."""
    with engine.connect() as conn:
        # ── rulesets ──
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(rulesets)")).fetchall()}
        if "site_type" not in cols:
            conn.execute(text("ALTER TABLE rulesets ADD COLUMN site_type TEXT NOT NULL DEFAULT 'launch'"))
            conn.commit()
            logger.info("Migration: added rulesets.site_type column")
        if "is_preset" not in cols:
            conn.execute(text("ALTER TABLE rulesets ADD COLUMN is_preset BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.commit()
            logger.info("Migration: added rulesets.is_preset column")
        if "org_id" not in cols:
            conn.execute(text("ALTER TABLE rulesets ADD COLUMN org_id TEXT REFERENCES organizations(id)"))
            conn.commit()
            logger.info("Migration: added rulesets.org_id column")

        # ── users ──
        ucols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "org_id" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN org_id TEXT REFERENCES organizations(id)"))
            conn.commit()
            logger.info("Migration: added users.org_id column")


def _seed_dedup_overrides(engine) -> None:
    """Pre-seed known station dedup pairs.  Idempotent — checks before inserting."""
    _SEEDS = [
        # (station_id_a, station_id_b, note)
        ("holfuy-1850", "windline-6116", "Lehn: same physical site, different networks"),
    ]
    with engine.connect() as conn:
        for sid_a, sid_b, note in _SEEDS:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM station_dedup_overrides "
                    "WHERE (station_id_a = :a AND station_id_b = :b) "
                    "   OR (station_id_a = :b AND station_id_b = :a)"
                ),
                {"a": sid_a, "b": sid_b},
            ).fetchone()
            if not exists:
                import uuid as _uuid
                conn.execute(
                    text(
                        "INSERT INTO station_dedup_overrides (id, station_id_a, station_id_b, note, created_at) "
                        "VALUES (:id, :a, :b, :note, datetime('now'))"
                    ),
                    {"id": str(_uuid.uuid4()), "a": sid_a, "b": sid_b, "note": note},
                )
                conn.commit()
                logger.info("Seeded dedup override: %s <-> %s", sid_a, sid_b)


def init_db(db_path: str) -> None:
    """Create the SQLite file + all tables (idempotent)."""
    global _engine, _SessionLocal
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_path}"
    _engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=_engine)
    _run_column_migrations(_engine)
    _seed_dedup_overrides(_engine)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    logger.info("SQLite database ready: %s", db_path)


def get_db():
    """FastAPI dependency — yields a SQLAlchemy Session."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() during startup")
    db: Session = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """Return the SQLAlchemy session factory (for use outside FastAPI dependency injection)."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() during startup")
    return _SessionLocal
