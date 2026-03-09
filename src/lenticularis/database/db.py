"""
SQLite database engine and session management.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lenticularis.database.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def init_db(db_path: str) -> None:
    """Create the SQLite file + all tables (idempotent)."""
    global _engine, _SessionLocal
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_path}"
    _engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=_engine)
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
