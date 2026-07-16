# Data Model: Public Rule Sets on the Map

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md)
**Phase**: 1 — Design

---

## Summary

One new column. No new tables, no relationships, no changes to existing columns.

The feature is overwhelmingly a **read-path** change; the only persisted state it adds is the
admin's curation judgement.

---

## Changed entity: `rulesets`

| Column | Type | Null | Default | Purpose |
|---|---|---|---|---|
| `is_showcase` | BOOLEAN | NOT NULL | `FALSE` | Admin marks this rule set as an example fit for anonymous visitors (FR-008, FR-011) |

### Why a new column rather than reusing an existing flag

`rulesets` already carries two adjacent booleans. Neither can serve:

| Flag | Means | Why it cannot back curation |
|---|---|---|
| `is_public` | **Owner** chose to publish | Curation is a separate **admin** judgement (FR-011); one flag cannot record two people's decisions. Under D4 the two are *combined* at read time, but they remain distinct facts |
| `is_preset` | **Admin** offers it as a starting template | Different editorial judgement — a good template is not a good showcase (see research R1) |

`is_showcase` is deliberately independent of both. The three flags answer three different questions
and are freely combinable.

### Field semantics

- **Default `FALSE`** — nothing becomes publicly visible to anonymous visitors as a side-effect of
  this migration. The anonymous map starts empty and fills only by deliberate admin action. This is
  the safe default: the alternative (defaulting existing public rule sets to showcase) would publish
  people's rule sets to the open internet without anyone deciding to.
- **Gated by `is_public` (D4)** — `is_showcase` is the admin's editorial opinion; `is_public` is the
  owner's consent. **Both are required** for anonymous visibility, and consent is checked live:

  ```
  anonymous visibility  ⟺  is_showcase AND is_public
  ```

  This is a **conjunction at read time, not a copy at write time**. The two flags stay independent
  columns holding two independent facts; neither overwrites the other.
- **NOT NULL** — matches `is_public` / `is_preset`, which are both `nullable=False` with a default.

### Why the gate is a read-time conjunction, not a write-time cascade

An obvious alternative is to clear `is_showcase` whenever an owner sets `is_public = false`. Rejected:
that destroys the admin's editorial decision as a side-effect of someone else's unrelated action. If
the owner re-publishes, an admin would have to notice and re-curate — work that was already done and
was never withdrawn.

Holding both facts separately and ANDing them at read time means un-publishing **hides** rather than
**forgets** (FR-013), and re-publishing restores the previous state with no admin involvement. It
also means there is exactly one place where visibility is decided, rather than an invariant
maintained across every write path that touches `is_public`.

### The truth table is the whole feature

| `is_public` | `is_showcase` | Anonymous map | Signed-in map (others') |
|---|---|---|---|
| false | false | ✗ | ✗ |
| false | true | ✗ — owner has not consented (D4) | ✗ |
| true | false | ✗ — not curated | ✓ (subject to 500 m suppression) |
| true | true | ✓ | ✓ (subject to 500 m suppression) |

Note row 2 is **reachable**: an admin curates a published rule set, the owner later un-publishes it.
The curation flag legitimately outlives the consent. This is FR-013 working as specified, not a
broken state to repair.

### Migration

Per `02-backend-conventions.md` and `04-constraints.md` — **no Alembic, no `.sql` files**. A guarded
`ALTER TABLE` in `_run_column_migrations()` in `database/db.py`, following the exact shape already
used for `is_preset` (`db.py:29-32`):

```python
cols = {row[1] for row in conn.execute(text("PRAGMA table_info(rulesets)")).fetchall()}
if "is_showcase" not in cols:
    conn.execute(text("ALTER TABLE rulesets ADD COLUMN is_showcase BOOLEAN NOT NULL DEFAULT FALSE"))
    conn.commit()
    logger.info("Migration: added rulesets.is_showcase column")
```

Idempotent, safe to re-run, and consistent with every other column migration in the file.

---

## New transport models (not persisted)

Per research R5, the public path must **not** reuse `RuleSetOut` — it carries `owner_display_name`
and would leak owner identity to anonymous visitors by default.

### `PublicRuleSetMarker`

The complete set of what an anonymous visitor may learn about a rule set (D2, FR-007):

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Needed for stable client-side keying. Not sensitive — every other rule set route requires auth and ownership |
| `name` | `str` | Site name |
| `lat` | `float` | Non-null by construction; unpositioned rule sets are omitted |
| `lon` | `float` | " |
| `site_type` | `SiteType` | Drives which marker icon is used |
| `decision` | `ResultColour` | Current traffic light |

**Deliberately absent** — and each absence is a requirement, not an oversight:

| Excluded | Why |
|---|---|
| `owner_display_name`, `owner_id` | NFR-001, D2 — owner identity is never exposed |
| `conditions`, thresholds, `group_id` | D2, FR-007 — rules stay pilot-owned; also `003` FR-009 |
| `description`, `notify_on`, `org_id`, timestamps | Not needed to demonstrate value; minimal exposure |
| `no_data_stations` | Rule sets resting on no data are **omitted entirely** (FR-010, R4), so the field can never be non-empty here |

### `PublicMapResponse`

| Field | Type | Notes |
|---|---|---|
| `data` | `list[PublicRuleSetMarker]` | Matches the `data`-key shape already used by composite endpoints in `stations.py` |
| `generated_at` | `str` | ISO timestamp of the underlying evaluation, so the client can show freshness (NFR-004) |

> Collection shape follows `07-api-conventions.md`: there is no single house style, and this is a
> composite/computed payload, so it takes the `data` + metadata shape rather than a bare array.

---

## What is NOT changing

- No change to `rule_conditions`, `condition_groups` (which does not exist — see `003`), or any
  evaluation logic.
- No change to how decisions are computed. The public path calls the same evaluator seam
  (`_evaluate_from_station_data`) with the same inputs.
- No change to `RuleSetOut`, `RuleSetDetail`, or any existing route's payload.
- No new InfluxDB measurement, tag, or field.
