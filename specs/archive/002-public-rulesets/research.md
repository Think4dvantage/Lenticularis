# Research: Public Rule Sets on the Map

**Feature**: [spec.md](./spec.md)
**Phase**: 0 — Research
**Date**: 2026-07-16

Unknowns identified in the spec, resolved here before design.

---

## R1 — What marking backs "curated"? (spec Assumptions, FR-011)

- **Decision**: Add a **new, distinct marking** for showcase curation. Do not reuse `is_preset`.
- **Rationale**: `is_preset` already means "offer this as a starting template in the new-rule-set
  form" (`rulesets.py:108-118`, toggled by admin at `rulesets.py:532`). Showcasing on the public map
  is a different editorial judgement: a good template is not necessarily a good showcase (a generic
  "Alpine launch template" teaches well but demonstrates nothing live), and a compelling showcase is
  not necessarily a good template (a heavily site-specific föhn rule set). Overloading one flag would
  make the two decisions inseparable — an admin could not have one without the other, and neither
  audience could be tuned independently. The cost of a second flag is one guarded `ALTER TABLE`,
  which is cheap and reversible; the cost of overloading is a coupling that cannot be undone later
  without a data migration to untangle intent.
- **Alternatives considered**:
  - *Reuse `is_preset`* — zero schema change, but permanently welds template-ness to showcase-ness.
  - *Reuse `is_public`* — rejected: that flag carries the **owner's** decision, and FR-011 requires
    curation to be a separate *admin* judgement. One flag cannot hold two people's opinions.
  - *A separate curation table* — over-built for a boolean on an existing entity; no attributes to
    hold beyond the flag itself.

> **Updated by D4 (2026-07-16).** `is_showcase` remains a **separate column** from `is_public` — the
> reasoning above is unchanged, because the two flags record two different people's decisions and
> must stay distinct. What D4 adds is that they are **combined at read time**: anonymous visibility
> requires `is_showcase AND is_public`. Curation is still an independent admin judgement; it simply
> may only be *exercised* over rule sets the owner has already published. Separate storage, joint
> effect. See spec D4 and the truth table in `data-model.md`.

## R2 — How is the public map served without an N+1 fan-out? (NFR-002)

- **Decision**: One batched, cached read path. Gather every station ID across **all** qualifying rule
  sets, make a **single** `query_latest_for_stations()` call, then evaluate each rule set in memory
  against that shared snapshot via `_evaluate_from_station_data()`. Cache the finished payload.
- **Rationale**: `run_evaluation()` already batches, but only *within* one rule set — it collects that
  rule set's station IDs and queries for them (`evaluator.py:310-317`). Called in a loop over N rule
  sets it produces N InfluxDB queries. `04-constraints.md` (T09) forbids exactly this shape, and
  NFR-002 requires cost not to scale with the number of public rule sets. `_evaluate_from_station_data(ruleset, station_data)`
  is pure and does no I/O, so it is the correct seam: one query, N in-memory evaluations.
- **Alternatives considered**:
  - *Loop `run_evaluation()` per rule set* — the obvious approach, and the one the current
    authenticated frontend effectively does. Violates NFR-002 and T09. Rejected.
  - *Reuse the `rule_decisions` history already written to InfluxDB* — decisions are already
    persisted, so the map could read them back instead of recomputing. Rejected: those are written by
    the evaluation of *owners'* rule sets on their own cadence, so freshness would be neither
    controlled nor guaranteed (NFR-004), and it would couple the public map to the scheduler's timing.

## R3 — Cache shape and lifetime

- **Decision**: A module-level cache holding the finished public payload, with a TTL, a size bound,
  and a lock — following the established pattern.
- **Rationale**: `04-constraints.md` (T10) is explicit: *"Every module-level cache dict must have a
  maximum size and a `threading.Lock` guard"*, with a documented eviction pattern. The replay cache
  in `api/routers/stations.py` (`_replay_cache`, 5 min TTL) is the in-repo precedent to copy. The
  public payload has very low cardinality — effectively one entry for the anonymous view — so bounds
  are trivially satisfied, but the guard is still required by the constraint and by the fact that
  APScheduler jobs and request handlers touch module state from different threads.
- **TTL**: 60 s. The map already refreshes on a 60 s interval (`index.html:813`), and observations
  arrive on a 5–30 min collector cadence, so a shorter TTL buys nothing and a much longer one risks
  a visibly stale traffic light (NFR-004). 60 s makes the anonymous load cost independent of the
  number of visitors.
- **Alternatives considered**:
  - *No cache* — every anonymous page load triggers an InfluxDB query. This is the abuse surface
    NFR-002 exists to close. Rejected.
  - *Warm on startup like the replay cache* — deferred, not rejected. Worth doing only if first-hit
    latency proves visible; the payload is far cheaper to build than the replay one.

## R4 — "Cannot be evaluated" is not the same as "request failed" (FR-010)

- **Decision**: A rule set qualifies for display only when it evaluates against **real data**. Omit
  any rule set whose evaluation rests on no data, using the `no_data_stations` signal.
- **Rationale**: This is the sharpest hazard in the feature. `run_evaluation()` documents that the
  decision *"is always `green` when no conditions trigger (no data / all conditions false) — this is
  intentional: unknown = benefit of the doubt"* (`evaluator.py:306-308`), and expects consumers to
  surface `no_data_stations`. For a signed-in pilot that trade-off is defensible: they can see the
  no-data list and apply judgement. For an anonymous visitor it is not — the shop window would show
  a confident green "flyable" marker for a site the system knows nothing about. That is worse than
  showing nothing, and it is the exact opposite of the trust the feature is meant to build.
  The current frontend guards only with `if (!dec) return;` (`index.html:779`), which catches a
  *failed request* — not a successful evaluation that happens to be green out of ignorance.
- **Consequence**: FR-010 must be implemented against `no_data_stations`, not against request
  failure. A rule set with **any** no-data station backing a condition is omitted from the public map.
- **Alternatives considered**:
  - *Show it greyed / "unknown"* — contradicts FR-010, which explicitly chose omission, and adds a
    fourth visual state to a traffic light whose entire value is having three.
  - *Show it green anyway* — rejected; this is the bug being avoided.

## R5 — Payload must be narrower than the existing one (D2, NFR-001)

- **Decision**: The public path serves its own minimal payload: site name, coordinates, site type,
  and decision. It must not reuse `RuleSetOut`.
- **Rationale**: `RuleSetOut` carries `owner_display_name` (`models/rules.py:131`) and the full rule
  set metadata. Reusing it would leak owner identity to anonymous visitors **by default**, violating
  D2 and NFR-001 — and it would do so silently, because nothing would look wrong. A separate narrow
  model makes the exposure decision explicit and reviewable, and makes accidental widening
  impossible without someone editing the public model on purpose.
- **Alternatives considered**:
  - *Reuse `RuleSetOut` and strip fields at the router* — one forgotten field is a privacy leak, and
    the default is wrong. Rejected in favour of a model that cannot leak by omission.

## R6 — Where does the 500 m proximity test run? (D1, FR-005)

- **Decision**: Reuse `haversine_m()` from `services/dedup.py:44`. Apply the suppression to the
  **signed-in** path only, and compute it against the viewer's own rule sets.
- **Rationale**: `haversine_m(lat1, lon1, lat2, lon2) -> float` is already public and already returns
  metres, so the D1 test is a direct `< 500` comparison with no unit conversion. `02-backend-conventions.md`
  forbids redefining shared helpers. The comparison is O(viewer's rule sets × candidate rule sets),
  which is negligible at any plausible scale but is **per-viewer** and therefore cannot live in the
  shared anonymous cache — it must be applied after the cached common payload is fetched.
- **Consequence**: two distinct paths, not one parameterised path. The anonymous payload is shared
  and cacheable; the signed-in payload is per-viewer and is **not** the same cache entry. Conflating
  them risks serving one viewer's suppression result to another — a correctness bug, and a privacy
  bug if the sets differ.
