# T11 — Enable SQLite WAL mode + busy_timeout

**Severity:** Medium · **Phase:** 2 · **Model tier:** Trivial

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md` (it explicitly documents WAL as required).
- LF line endings only. Exactly this task.

## Problem
`src/lenticularis/database/db.py` `init_db()` creates the engine with no journal-mode or busy-timeout
configuration:
```python
_engine = create_engine(db_url, connect_args={"check_same_thread": False})
```
Under FastAPI's threadpool (many concurrent sync handlers) plus the scheduler/evaluator committing
writes, default rollback-journal mode risks `database is locked` errors. The backend conventions doc
prescribes WAL — the code never enables it.

## Fix
In `db.py`, set a busy timeout via `connect_args` and enable WAL + a sane `synchronous` level via a
connection event listener:
```python
from sqlalchemy import create_engine, event, text

# inside init_db(), replace the create_engine call:
_engine = create_engine(
    db_url,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)

@event.listens_for(_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
```
Register the listener before `Base.metadata.create_all(...)` runs so the first connection already
applies the pragmas. Log WAL status at INFO in `init_db` (the operability doc asks for "WAL mode
status" at startup).

## Acceptance criteria
- After startup, `PRAGMA journal_mode;` on the DB file returns `wal` (a `-wal` sidecar file appears
  next to the `.db`).
- Startup log includes a line reporting WAL enabled.
- Concurrent reads during an evaluator write no longer raise `database is locked` under normal load.
