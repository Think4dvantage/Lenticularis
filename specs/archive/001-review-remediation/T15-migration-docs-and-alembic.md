# T15 — Reconcile migration docs + drop unused Alembic dependency

**Severity:** Medium · **Phase:** 3 · **Model tier:** Trivial

## Ground Rules
- LF line endings only. Exactly this task — docs + dependency only, no code-behavior change.

## Problem
The instructions contradict themselves and the code:
- `.ai/instructions/02-backend-conventions.md` documents a `.sql`-files + `_migrations`-table system
  that **does not exist** in the repo.
- `.ai/instructions/04-constraints.md` says "No Alembic — migrations are raw `ALTER TABLE` in
  `_run_column_migrations()`" — which **is** what `src/lenticularis/database/db.py` actually does.
- `pyproject.toml` declares `alembic` as a dependency, but it is never imported or used anywhere
  (contradicts the "No Alembic" rule and adds dead weight).

## Fix
1. **Drop the dependency.** In `pyproject.toml`, remove the line:
   ```
   "alembic (>=1.17.2,<2.0.0)",
   ```
   from `dependencies`. (Do not remove anything else.) Note in your summary that the human should
   run `poetry lock` / rebuild so the lockfile/image drop it.
2. **Fix the migration docs.** Edit `.ai/instructions/02-backend-conventions.md` so the
   "New SQLite Table & Migrations" section describes the **actual** mechanism:
   - Schema is created by `Base.metadata.create_all()` on first boot.
   - New columns are added idempotently in `_run_column_migrations()` in
     `src/lenticularis/database/db.py` using `PRAGMA table_info(<table>)` guards + raw
     `ALTER TABLE ... ADD COLUMN ...` followed by `conn.commit()`.
   - There is **no** `_migrations` table and **no** `.sql` migration files. Remove the `.sql`/
     `_migrations`/`run_migrations()` example blocks that describe a non-existent system.
   - State explicitly: "No Alembic" (matching `04-constraints.md`).
   Keep the WAL guidance (it becomes true once T11 lands; if T11 is not yet merged, phrase it as the
   required target state).

## Acceptance criteria
- `alembic` no longer appears in `pyproject.toml`.
- `.ai/instructions/02-backend-conventions.md` describes the real `_run_column_migrations()` +
  `create_all()` approach and no longer references a `_migrations` table or `.sql` files.
- `02-` and `04-` instruction files no longer contradict each other on migrations.
- `grep -ri alembic src/` returns nothing (it never did — confirm).
