# Prompt: Add a New SQLite Table or Column

## New Table

Add a new SQLAlchemy ORM model in `src/lenticularis/database/models.py`:

```python
class {ModelName}(Base):
    __tablename__ = "{table_name}"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=datetime.utcnow)
    # ... other columns
```

New tables are created automatically by `Base.metadata.create_all()` in `db.py:init_db()`. No migration block needed.

## New Column on Existing Table

Add the column to the ORM model in `models.py`, then add an **idempotent** migration block to `_run_column_migrations()` in `src/lenticularis/database/db.py`:

```python
# {table_name} migrations
cols = {c["name"] for c in conn.execute(text("PRAGMA table_info({table_name})")).fetchall()}
if "{new_column}" not in cols:
    conn.execute(text("ALTER TABLE {table_name} ADD COLUMN {new_column} {TYPE} {DEFAULT}"))
    conn.commit()
```

Always check `PRAGMA table_info` first to make migrations idempotent. Never use Alembic.

## Pydantic Schemas

Add corresponding request/response schemas to `src/lenticularis/models/`. Follow Pydantic v2 conventions.

Refer to `.ai/context/architecture.md` for the full SQLite schema reference.
