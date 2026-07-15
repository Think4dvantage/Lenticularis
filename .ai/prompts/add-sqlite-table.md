# Prompt: Add a New SQLite Table or Column

Use this prompt when you need to persist new structured data.

> **No Alembic. No `.sql` migration files. No `_migrations` table.** New tables are created by
> `Base.metadata.create_all()` at startup; new columns on existing tables are added with raw
> `ALTER TABLE` guarded by `PRAGMA table_info()` in `_run_column_migrations()`. See
> `.ai/instructions/02-backend-conventions.md` and `04-constraints.md`.

---

## Adding a new table

1. **Define the ORM model** in `src/lenticularis/database/models.py` (`Base` is the
   `DeclarativeBase` defined there):
   ```python
   class Widget(Base):
       __tablename__ = "widgets"
       id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
       name = Column(String, nullable=False)
       created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
   ```

2. **That's the whole migration.** `init_db()` calls `Base.metadata.create_all()` on every
   startup; it is idempotent and creates only missing tables. Do **not** write any SQL, add a
   migration file, or touch `_run_column_migrations()` for a brand-new table.

3. **Add the Pydantic schemas** (Create / Update / Out) in `src/lenticularis/models/`.

4. **Add CRUD endpoints** in the appropriate router (or create one — see `add-api-router.md`).

5. **Document it** in `.ai/context/architecture.md` under "SQLite Tables".

## Adding a column to an existing table

1. **Add the column to the ORM model** in `models.py` (so fresh databases get it via
   `create_all()`).

2. **Add an idempotent guard** in `_run_column_migrations()` in
   `src/lenticularis/database/db.py` — this backfills the column on databases that already
   exist. Read the current columns with `PRAGMA table_info(...)`, then `ALTER TABLE` only if
   the column is missing:
   ```python
   cols = {row[1] for row in conn.execute(text("PRAGMA table_info(widgets)")).fetchall()}
   if "new_col" not in cols:
       conn.execute(text("ALTER TABLE widgets ADD COLUMN new_col TEXT"))
       conn.commit()
       logger.info("Migration: added widgets.new_col column")
   ```
   (For a foreign key: `ADD COLUMN owner_id TEXT REFERENCES users(id)`.)

3. **Update the Pydantic schemas** and any router that returns the entity.

4. **Document the new column** in `.ai/context/architecture.md` under "SQLite Tables".

## Both cases

- **Never** use Alembic, create `.sql` files, or maintain a `_migrations` table.
- Run `poetry run pytest -q` — the harness builds the schema from `Base.metadata` in-memory,
  so a model that does not import cleanly will fail fast.
- Run `.ai/prompts/sync.md` to flush human-readable docs.
