# Implementation Plan: Public Rule Sets on the Map

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md) · **Contracts**: [contracts/public-map.yaml](./contracts/public-map.yaml)
**Phase**: 2 — Plan · **Date**: 2026-07-16
**Next step**: `tasks.md`

---

## Technical Context

| Concern | Choice |
|---|---|
| Backend | FastAPI + Pydantic v2, existing patterns only — no new dependencies |
| Persistence | One boolean column on `rulesets`, guarded `ALTER TABLE` in `_run_column_migrations()`. No Alembic |
| Evaluation | Reuse `_evaluate_from_station_data()` (pure, no I/O) over a single batched `query_latest_for_stations()` snapshot |
| Caching | Module-level dict, TTL 60 s, `threading.Lock`, size-bounded — per T10 |
| Distance | Reuse `haversine_m()` from `services/dedup.py:44` |
| Frontend | Vanilla JS in `static/index.html`. No build step, no new libraries |
| Tests | pytest + `FakeInflux`, per `06-testing-conventions.md` |

**Architecture approach**: a thin read path with one new module holding the batch-evaluate-and-cache
logic, consumed by two routers (anonymous and signed-in). The write side is a single admin toggle
mirroring the existing `set_preset`. No existing route, model, or evaluation path changes.

**Key dependencies**: none new. Every primitive already exists.

---

## Constitution Check

Per `.ai/instructions/00-ai-usage.md`. Each principle assessed, not assumed.

| # | Principle | Status | Notes |
|---|---|---|---|
| 1 | Read before acting | ✅ Pass | Spec, research and design grounded in read code; every claim cites a file:line |
| 2 | Plan before building | ✅ Pass | This document. No code written before it |
| 3 | Minimal scope | ⚠️ **Justified exception** | The two-audience model (curated for anonymous, published for signed-in) is larger than the literal request. It is **not** AI-initiated scope creep — it is decision **D3**, made explicitly by the user during clarification, with the alternative offered and declined. Recorded so it is not later mistaken for drift |
| 4 | Tool-agnostic instructions | ✅ Pass | No AI instruction files created outside `.ai/` |
| 5 | Keep docs in sync | ✅ Pass | Phase 5 updates `architecture.md`, `features.md`, `README.md` — a required phase, not optional cleanup |
| 6 | No secrets committed | ✅ Pass | No secrets, no config values involved |
| 7 | Prod is off-limits | ✅ Pass | No deployment action. Migration runs at container startup as every other column migration does |

**No blocking violations.** The single exception (#3) is a recorded user decision, which the
constitution permits when justified explicitly.

### Additional constraint compliance (`04-constraints.md`)

| Constraint | How this plan complies |
|---|---|
| T09 — batch before looping | The entire point of R2: one `query_latest_for_stations()` for **all** rule sets, N in-memory evaluations. A per-rule-set `run_evaluation()` loop is explicitly rejected |
| T10 — bounded, locked caches | Cache carries TTL + `threading.Lock` + max size, copying `_replay_cache` |
| T07/T08 — never block the event loop | InfluxDB calls are synchronous; the async route wraps the builder in `asyncio.to_thread()` |
| T01 — Flux injection | No user-supplied ID reaches a Flux string on this path; station IDs come from the DB, not the request |
| T03 — XSS | Rule set names are untrusted; the popup uses `textContent`, never `innerHTML` |
| T12 — error envelope | Errors via the standard envelope; no bare `JSONResponse` |
| T18 — no swallowed exceptions | The cache builder logs with `logger.exception` and re-raises; it must not silently serve an empty map |
| Static assets → version bump | Phase 4 touches `static/`, so `pyproject.toml` **must** be bumped — the version is the cache key |

---

## Data Model Summary

One column: `rulesets.is_showcase` (BOOLEAN NOT NULL DEFAULT FALSE) — the admin's curation judgement,
stored separately from the owner's `is_public` and from `is_preset`, because all three record
different decisions by different people. Per **D4**, anonymous visibility is the read-time
conjunction `is_showcase AND is_public`: stored separately, applied jointly. Full rationale, truth
table and migration snippet in [data-model.md](./data-model.md).

Two transport models, neither persisted: `PublicRuleSetMarker` (id, name, lat, lon, site_type,
decision — and *nothing else*) and `PublicMapResponse` (data + generated_at). These must not reuse
`RuleSetOut`, which carries `owner_display_name` and would leak owner identity by default (R5).

---

## File Structure

**Create**

| File | Purpose |
|---|---|
| `src/lenticularis/services/public_map.py` | Batch evaluate + cache. The whole feature's logic lives here, testable without HTTP |
| `src/lenticularis/api/routers/public.py` | `/api/public` router — the only unauthenticated surface |
| `tests/backend/test_public_rulesets.py` | Omission rules, privacy, proximity, cache |

**Modify**

| File | Change |
|---|---|
| `src/lenticularis/database/models.py` | `is_showcase` column on `RuleSet` |
| `src/lenticularis/database/db.py` | Guarded `ALTER TABLE` in `_run_column_migrations()` |
| `src/lenticularis/models/rules.py` | `PublicRuleSetMarker`, `PublicMapResponse` |
| `src/lenticularis/api/routers/rulesets.py` | `GET /public-map` (signed-in) + `PUT /{id}/set_showcase` (admin) |
| `src/lenticularis/api/main.py` | Register the public router |
| `static/index.html` | Unwrap the `isLoggedIn()` gate; anonymous markers + CTA popup |
| `static/i18n/{en,de,fr,it}.json` | CTA + "example rule set" keys — all four, simultaneously |
| `pyproject.toml` | **Version bump** — mandatory, static assets changed |
| `.ai/context/architecture.md`, `.ai/context/features.md`, `README.md` | Doc sync (Constitution #5) |

---

## Implementation Phases

Ordered so each phase is independently verifiable and nothing user-visible ships until the data
behind it is correct.

### Phase 1 — Curation marking (no user-visible change)

1. `is_showcase` on the `RuleSet` model.
2. Guarded `ALTER TABLE` migration, mirroring `is_preset` (`db.py:29-32`).
3. `PUT /api/rulesets/{id}/set_showcase`, admin-only, mirroring `set_preset` (`rulesets.py:532`),
   **with the D4 write guard**: `is_showcase=true` on an unpublished rule set → **409 CONFLICT**;
   `is_showcase=false` always allowed.
4. Tests: migration idempotency; non-admin gets 403; toggle round-trips; **409 when curating an
   unpublished rule set**; **un-curating an unpublished rule set still succeeds**.

*Verifiable*: an admin can mark a published rule set as a showcase, and cannot mark a private one.

### Phase 2 — Batched public read path

1. `services/public_map.py` — `build_public_map(db, influx, audience)`:
   - select qualifying rule sets (positioned; **`is_showcase AND is_public`** for anonymous, per D4 —
     the read-time conjunction is what makes owner consent live)
   - collect **every** station ID across **all** of them into one set
   - **one** `query_latest_for_stations()` call
   - `_evaluate_from_station_data()` per rule set against that shared snapshot
   - drop any rule set with non-empty `no_data_stations` (**FR-010 / R4**)
   - drop non-green opportunity sites (FR-009)
   - return `PublicMapResponse`
2. Cache: TTL 60 s, `threading.Lock`, bounded, **plus a poisoning guard** — never cache a result
   built from a failed or empty InfluxDB read (the replay cache learned this the hard way; see
   `architecture.md` "Cache poisoning guard").
3. `GET /api/public/rulesets/map` in the new router, `asyncio.to_thread()` around the builder.
4. Tests: **no-data rule set is omitted, not green**; payload contains no owner fields; empty list is
   a valid response; one Influx call regardless of rule set count; **a curated-but-unpublished rule
   set never appears anonymously** (the `is_public=false, is_showcase=true` row from the truth table).

*Verifiable*: `curl` the route unauthenticated and get correct markers.

### Phase 3 — Signed-in path with proximity suppression

1. `GET /api/rulesets/public-map` — other owners' `is_public` rule sets, minus any within **500 m**
   of one of the viewer's own, via `haversine_m()`.
2. Per-viewer, so **not** the anonymous cache entry (R6).
3. Tests: 499 m suppressed / 501 m shown (boundary); own rule sets never suppressed; unpositioned
   rule sets suppress nothing.

*Verifiable*: two accounts, one nearby rule set, correct suppression.

### Phase 4 — Frontend

1. Lift ruleset loading out of `if (isLoggedIn())` (`index.html:573-815`); branch by auth state to
   the correct route.
2. Anonymous: render markers; popup shows name + colour + "this is an example — sign up to build
   your own", with a link to register. `textContent` only (T03).
3. Signed-in: render others' rule sets in a visually distinct style from own (FR-006).
4. i18n keys in all four locales.
5. Console logging with the `[Lenti:index]` prefix on every new path — mandatory per
   `03-frontend-conventions.md`, not optional.
6. **Bump `pyproject.toml`.**

*Verifiable*: load the map signed out in a private window.

### Phase 5 — Docs sync

`architecture.md` (new routes, new column, cache), `features.md` (milestone), `README.md` if the
route list is affected. Constitution #5 — required.

---

## Dependencies

- **External**: none. No new libraries, no new services.
- **Internal**: `haversine_m()` (`services/dedup.py:44`), `_evaluate_from_station_data()`
  (`rules/evaluator.py:186`), `query_latest_for_stations()`, the `_replay_cache` pattern
  (`api/routers/stations.py`).
- **Operational**: at least one rule set must be marked `is_showcase` or the anonymous map is empty
  (spec Dependencies). Ship Phase 1 first so curation can happen before the map goes live.
- **Not dependent on `003`.** They share a display surface but neither blocks the other.

---

## Risk & Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **"Unknown = benefit of the doubt" green** shows a confident green marker for a site with no data — the shop window lies to visitors | **High** — directly defeats the feature's purpose | FR-010 / R4: omit any rule set with non-empty `no_data_stations`. Explicit test. Note the current frontend guard (`if (!dec) return;`) catches only *failed requests* and would **not** catch this |
| 2 | **Owner identity leak** via reusing `RuleSetOut` (carries `owner_display_name`) | **High** — privacy breach, silent | Separate `PublicRuleSetMarker`; `additionalProperties: false` in the contract; test asserting no owner field appears in the anonymous payload |
| 3 | **Unauthenticated N+1** into InfluxDB — an abuse surface anyone can spray | **High** | R2 batch + 60 s cache. Test that rule set count does not change the Influx call count |
| 4 | **Per-viewer results served from the shared cache** — one viewer's suppression leaks to another | **High** — correctness *and* privacy | R6: two separate paths. The anonymous payload is identical for all callers; the signed-in one is never cached under a shared key |
| 5 | Blocking the event loop with synchronous Influx calls | Medium | `asyncio.to_thread()` (T07/T08) |
| 6 | Cache poisoned with an empty map after a transient Influx failure — map stays empty for the TTL | Medium | Poisoning guard: never cache an empty/failed build. Log and re-raise (T18) |
| 7 | Static assets change without a version bump — stale JS pinned in browsers for a year | Medium | Phase 4 bumps `pyproject.toml`. Called out in the phase, not left to memory |
| 8 | XSS via a rule set name rendered into the popup | Medium | `textContent` only (T03). Names are untrusted |
| 9 | 500 m proves wrong in practice | Low | D1 accepted a fixed value knowingly; it is one constant to change |
| 10 | **A private rule set reaches the anonymous map** — the owner never consented to publication | **High** — the product's differentiator is that rules are pilot-owned | D4: write guard (409) **and** read-time `is_showcase AND is_public`. Two independent barriers; the read filter is authoritative, since it is the only place visibility is decided |
| 11 | A future write path sets `is_public=false` and forgets to consider showcase state | Low | Not applicable by construction — visibility is a read-time conjunction, so there is no invariant for a write path to violate. This is the main reason the write-time cascade was rejected |

### Resolved — curation requires owner consent (D4, 2026-07-16)

**An admin may only curate a rule set whose owner has published it.** Decided by the user; recorded
as spec D4 / FR-012 / FR-013.

Anonymous visibility is the **conjunction of both flags, evaluated at read time**:

```
anonymous visibility  ⟺  is_showcase AND is_public
```

Implemented in two places, and both are required:

1. **Write guard** — `set_showcase(true)` on an unpublished rule set is refused with **409 CONFLICT**
   (state conflict, not a malformed payload). Silently accepting it would leave an admin believing
   they had curated something invisible. `set_showcase(false)` is always permitted — withdrawing
   curation must never be blocked.
2. **Read filter** — the anonymous query filters on **both** flags. This is what makes consent live:
   an owner un-publishing hides the rule set immediately, without any admin involvement.

**Deliberately not implemented: clearing `is_showcase` when an owner un-publishes.** That would
destroy the admin's editorial decision as a side-effect of an unrelated action by someone else, and
force re-curation if the owner ever re-published. Holding both facts independently and ANDing them at
read time means un-publishing *hides* rather than *forgets* (FR-013). It also keeps visibility decided
in exactly one place, instead of an invariant that every future write path touching `is_public` must
remember to maintain.

The state `is_public=false, is_showcase=true` is therefore **legitimate and reachable**, not a broken
row to repair. See the truth table in [data-model.md](./data-model.md).
