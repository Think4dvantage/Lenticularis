# Data Model: Named Condition Groups

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md)
**Phase**: 1 — Design

---

## Summary

One new table. One existing column gains a meaning it did not have. **No column is altered, and no
evaluation logic changes** (research R2).

This is the table `database/models.py:253` has been predicting:

```python
# Grouping (NULL = top-level flat list; future: group_id references a condition_groups table)
```

---

## New entity: `condition_groups`

| Column | Type | Null | Default | Purpose |
|---|---|---|---|---|
| `id` | String | PK | `uuid4()` | Group identity. Accepts the client-minted id, as conditions' `group_id` already does |
| `ruleset_id` | String | NOT NULL, indexed | — | FK → `rulesets.id`, `ondelete="CASCADE"` |
| `name` | String | **nullable** | `NULL` | The pilot's label. NULL = unnamed (FR-004, FR-005) |
| `sort_order` | Integer | NOT NULL | `0` | Display order, mirroring `rule_conditions.sort_order` |

### Semantics

- **`name` is nullable, not `""`.** NULL means "never named" and is what every backfilled legacy
  group gets (R4, FR-005). An empty string would be indistinguishable from a name the pilot cleared.
- **No uniqueness constraint on `name`.** It is a memo, not a key — two groups may both be "Wind"
  (spec Assumptions).
- **A group with zero conditions is valid.** That is the whole point of D1, and it is inert at
  evaluation by construction (FR-011, R2).

### Relationship

```python
# On RuleSet — mirrors the existing conditions relationship exactly (models.py:154-157)
condition_groups = relationship(
    "ConditionGroup", back_populates="ruleset", cascade="all, delete-orphan",
    order_by="ConditionGroup.sort_order",
)
```

`cascade="all, delete-orphan"` is **load-bearing, not decorative**. SQLite foreign keys are not
enforced here — `db.py:97-99` sets no `PRAGMA foreign_keys=ON`, so `ondelete="CASCADE"` is
documentation only and the ORM does the actual work (R6). Without this cascade, deleting a rule set
leaves its groups behind forever.

---

## Changed meaning: `rule_conditions.group_id`

**The column itself does not change.** It stays `Column(String, nullable=True)`.

| Before | After |
|---|---|
| Opaque client-minted marker with no parent | Reference to `condition_groups.id` |
| Dropped to NULL when a group held < 2 rows | Preserved at any group size (D1) |
| NULL = standalone condition | Unchanged — NULL still means standalone |

**No `ALTER TABLE` on this column.** Adding a real `ForeignKey(...)` to it would require a SQLite
table rebuild (create/copy/drop/rename) — disproportionate risk for a constraint the database would
not enforce anyway (R6). Integrity is maintained by validation on the write path (R3), which is where
every other FK in this schema is effectively enforced too.

---

## Migration

Two steps, both idempotent, per `02-backend-conventions.md` — **no Alembic, no `.sql` files**.

### 1. Table creation — automatic

`Base.metadata.create_all()` in `init_db()` creates `condition_groups` on first boot after deploy.
It is idempotent and only creates missing tables. Nothing to write.

### 2. Backfill — guarded, in `_run_column_migrations()`

Every distinct non-NULL `group_id` currently in `rule_conditions` becomes a real, unnamed group
(R4). Guard on emptiness so it is safe to re-run:

```python
existing = conn.execute(text("SELECT COUNT(*) FROM condition_groups")).scalar()
if existing == 0:
    rows = conn.execute(text(
        "SELECT DISTINCT ruleset_id, group_id FROM rule_conditions "
        "WHERE group_id IS NOT NULL"
    )).fetchall()
    for ruleset_id, group_id in rows:
        conn.execute(
            text("INSERT INTO condition_groups (id, ruleset_id, name, sort_order) "
                 "VALUES (:id, :rs, NULL, 0)"),
            {"id": group_id, "rs": ruleset_id},
        )
    conn.commit()
    logger.info("Migration: backfilled %d condition_groups from existing group_ids", len(rows))
```

**Why this preserves decisions exactly (NFR-001):** conditions keep the `group_id` values they
already have. The evaluator buckets by that value (`evaluator.py:201-205`) and is not modified, so
the buckets after migration are byte-for-byte the buckets before it. Decisions cannot move.

**Why the id is reused, not regenerated:** inserting the *existing* `group_id` as the new row's
primary key means no condition row has to be touched. Regenerating ids would require rewriting every
`rule_conditions.group_id` — more work, more risk, no benefit.

---

## Transport models

### `ConditionGroupIn` (request)

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Client-minted, as `group_id` already is today (`ruleset-editor.html:758`) |
| `name` | `Optional[str]` | `None` = unnamed. Max length per spec Assumptions |
| `sort_order` | `int` | Defaults to 0 |

### `ConditionGroupOut` (response)

Same fields. Returned as part of `RuleSetDetail` so the editor can rebuild groups from stored data
rather than inferring them from `group_id` collisions (`ruleset-editor.html:1125-1129`).

### `ConditionsReplaceRequest` — extended

```python
class ConditionsReplaceRequest(BaseModel):
    conditions: list[RuleConditionCreate]
    groups: list[ConditionGroupIn] = Field(default_factory=list)
```

**Validation is fail-closed (R3)**: every non-NULL `condition.group_id` must appear in `groups`, or
reject with `VALIDATION_FAILED`. Without this, a caller omitting `groups` silently destroys every
group name the pilot typed. A 422 is enormously preferable to silent data loss.

### `ConditionResult` — one field added

| Field | Type | Notes |
|---|---|---|
| `group_name` | `Optional[str]` | Populated from a `{group_id: name}` lookup. **Cannot affect any decision** (R2) — it is presentation only |

This is what FR-007 requires and what the separate tooltip change will consume. `group_id` and
`group_all_matched` are already present (`models/rules.py:165-166`).

> **Never exposed to anonymous visitors** (FR-009, and `002` D2). `002`'s public payload is a
> separate narrow model that carries no condition data at all, so this is satisfied by construction.

---

## What is NOT changing

- **`rules/evaluator.py` decision logic** — untouched. Only `ConditionResult` gains `group_name` (R2).
- No change to `rule_conditions` columns, `rulesets`, or any other table.
- No new InfluxDB measurement, tag, or field.
- Grouping remains **AND-only and one level deep**. No nesting, no OR (spec Out of Scope).
