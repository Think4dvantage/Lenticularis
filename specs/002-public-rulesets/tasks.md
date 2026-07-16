# Tasks: Public Rule Sets on the Map

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md) · **Contracts**: [contracts/public-map.yaml](./contracts/public-map.yaml)
**Next step**: `analyze.md` → `implement.md`

## Status — shipped in v1.19.0, 2 tasks deferred

**22 of 24 done.** Implemented, tested (17 tests), committed, tagged `v1.19.0`, pushed.

**Resume here** — the two deferred tasks are both P2/P3 polish, neither blocks anything:

- **T015** — anonymous view does not display data freshness. `generated_at` is in the payload and
  logged to the console; it is simply not rendered.
- **T020** — the owner cannot see, in the editor, that their rule set is a curated public example.
  Backend is complete (`is_showcase` is on `RuleSetOut`); this is UI only.

**Also not built, and not a task here**: there is no admin UI for curation. The toggle is API-only
(`PUT /api/rulesets/{id}/set_showcase?is_showcase=true`). The anonymous map stays empty until
somebody calls it — that is by design, not a defect.

**One decision was reinterpreted during implementation** (see 003's status note for the detail): it
does not affect this feature.

---

## Summary

- **Total tasks**: 24
- **Parallel opportunities**: 9 marked `[P]`
- **MVP scope**: Phase 2 + Phase 3 (US1 only) — a visitor sees curated examples. US2–US4 are additive.
- **Test tasks included.** `.ai/prompts/tasks.md` says to add them only on request, but
  `06-testing-conventions.md` states *"Backend logic must be test-gated"*, and this feature's top
  risks (a private rule set reaching the public internet; a no-data green misleading visitors; owner
  identity leaking) are silent failures that only a test catches. They are in scope deliberately.

## Dependencies

```
Phase 1 — Setup            (none; existing project, no new deps)
        │
Phase 2 — Foundation       T001–T008  ← blocks every story
        │                  schema → curation route → transport models → batch+cache service
        │
        ├── Phase 3 — US1  T009–T015  anonymous map           ← MVP ends here
        │
        ├── Phase 4 — US2  T016–T019  proximity suppression   (needs T005–T007)
        │
        ├── Phase 5 — US3  T020       owner sees exposure     (needs T001)
        │
        └── Phase 6 — US4  T021       conversion path         (needs T013)
                    │
Final  — Polish            T022–T024  docs + version bump     ← required before any deploy
```

US2, US3 and US4 are independent of each other and may be done in any order once Phase 2 lands.

---

## Phase 1 — Setup

No tasks. Existing project, no new dependencies, no new configuration. Every primitive this feature
needs already exists (`haversine_m`, `_evaluate_from_station_data`, `query_latest_for_stations`, the
`_replay_cache` pattern).

---

## Phase 2 — Foundation

**Goal**: curation can be recorded, and a correct public payload can be built. No user-visible change.
**Blocks**: all four stories.

- [x] T001 [P] Add `is_showcase` column (`Boolean, nullable=False, default=False`) to `RuleSet` in `src/lenticularis/database/models.py`
- [x] T002 [P] Add guarded `ALTER TABLE rulesets ADD COLUMN is_showcase BOOLEAN NOT NULL DEFAULT FALSE` to `_run_column_migrations()` in `src/lenticularis/database/db.py`, mirroring the `is_preset` guard at `db.py:29-32`
- [x] T003 Add `PUT /api/rulesets/{id}/set_showcase` (admin-only) to `src/lenticularis/api/routers/rulesets.py`, mirroring `set_preset` at `rulesets.py:532`. **Enforce D4**: `is_showcase=true` on a rule set with `is_public=false` → **409 CONFLICT** via `AppException`; `is_showcase=false` always permitted
- [x] T004 [P] Tests for T003 in `tests/backend/test_public_rulesets.py`: non-admin → 403; curating an unpublished rule set → 409; curating a published one → 200; **un-curating an unpublished rule set → 200** (withdrawing curation is never blocked); migration is idempotent
- [x] T005 [P] Add `PublicRuleSetMarker` (id, name, lat, lon, site_type, decision — **and nothing else**) and `PublicMapResponse` (data, generated_at) to `src/lenticularis/models/rules.py`. Do **not** reuse or subclass `RuleSetOut` — it carries `owner_display_name` and would leak owner identity by default (research R5)
- [x] T006 Create `src/lenticularis/services/public_map.py` with `build_public_map(db, influx, viewer=None)`: select qualifying rule sets → collect **every** station id across **all** of them into one set → **one** `query_latest_for_stations()` call → `_evaluate_from_station_data()` per rule set against that shared snapshot. Anonymous selection filters on **`is_showcase AND is_public`** (D4). Never call `run_evaluation()` in a loop — that is the N+1 this feature exists to avoid (research R2, constraint T09)
- [x] T007 Add omission rules to `build_public_map` in `src/lenticularis/services/public_map.py`: drop any rule set with non-empty `no_data_stations` (**FR-010** — the evaluator returns green for no-data by design, `evaluator.py:306-308`, which would show visitors a confident green for a site with no data); drop unpositioned rule sets; drop non-green `opportunity` sites (FR-009)
- [x] T008 Add the cache to `src/lenticularis/services/public_map.py`: module-level dict, 60 s TTL, `threading.Lock`, bounded size (constraint T10), **plus a poisoning guard** — never cache a payload built from a failed or empty Influx read (see `architecture.md` "Cache poisoning guard"). Log failures with `logger.exception` and re-raise; never silently serve an empty map (constraint T18)

---

## Phase 3 — US1: A visitor understands the product without signing up [US1]

**Goal**: an unauthenticated visitor sees curated examples with live colours and a sign-up prompt.
**Independent test criteria**: open the map in a private window, signed out. Curated+published rule
sets appear with correct colours; clicking one explains it is an example and invites sign-up; no
private rule set appears; no owner name appears anywhere in the response.

- [x] T009 [US1] Create `src/lenticularis/api/routers/public.py` with `GET /api/public/rulesets/map` (prefix `/api/public`, no auth), calling `build_public_map` wrapped in `asyncio.to_thread()` (constraints T07/T08 — Influx calls are synchronous and would block the event loop)
- [x] T010 [US1] Register the public router in `src/lenticularis/api/main.py`
- [x] T011 [P] [US1] Tests in `tests/backend/test_public_rulesets.py`: unauthenticated → 200; **a curated-but-unpublished rule set never appears** (the `is_public=false, is_showcase=true` row of the truth table); a published-but-uncurated one never appears anonymously; **a rule set with no data is omitted, not green**; response contains no `owner_display_name`/`owner_id`; empty list is valid; **Influx call count does not scale with rule set count**
- [x] T012 [US1] Lift rule set loading out of `if (isLoggedIn())` in `static/index.html:573-815` and branch on auth state: anonymous → `/api/public/rulesets/map`, signed-in → existing path. Add `[Lenti:index]` logging to every new branch (mandatory per `03-frontend-conventions.md`)
- [x] T013 [US1] Render the anonymous popup in `static/index.html`: site name + colour + "this is an example — sign up to build your own". Use `textContent` only — rule set names are untrusted (constraint T03). Never `innerHTML`
- [x] T014 [P] [US1] Add example/CTA i18n keys to **all four** of `static/i18n/{en,de,fr,it}.json` simultaneously (constraint: never hardcode a user-visible string)
- [ ] T015 [P] [US1] Show data freshness from `generated_at` in the anonymous view (NFR-004) in `static/index.html`

---

## Phase 4 — US2: A pilot is not shown duplicates of their own sites [US2]

**Goal**: signed-in pilots see other people's published rule sets, except where they already have one.
**Independent test criteria**: two accounts; a rule set 400 m from one of your own is hidden, one at
600 m is shown, and your own are always shown.

- [x] T016 [US2] Add `GET /api/rulesets/public-map` to `src/lenticularis/api/routers/rulesets.py`: other owners' `is_public` rule sets, excluding any within **500 m** of one of the viewer's own. Reuse **`haversine_m()` from `services/dedup.py:44`** — it is already public and already returns metres; do not reimplement it (`02-backend-conventions.md` forbids redefining shared helpers)
- [x] T017 [US2] Ensure the per-viewer result is **not** served from the anonymous shared cache in `src/lenticularis/services/public_map.py` (research R6) — suppression is viewer-specific, and sharing the entry would serve one viewer's result to another
- [x] T018 [P] [US2] Tests in `tests/backend/test_public_rulesets.py`: 499 m suppressed / 501 m shown (boundary); own rule sets never suppressed; an unpositioned rule set suppresses nothing; **two viewers with different rule sets get different results** (cache isolation)
- [x] T019 [US2] Render other owners' rule sets in a visually distinct style from own in `static/index.html` (FR-006)

---

## Phase 5 — US3: An owner controls exposure [US3]

**Goal**: an owner can see and change whether their rule set is published, and whether it is curated.
**Independent test criteria**: publish and un-publish a rule set; confirm it enters and leaves the
anonymous map on the next refresh without any admin action.

- [ ] T020 [US3] Surface curation state to the owner in `static/ruleset-editor.html`: show that a rule set is a curated public example, and that **un-publishing hides it from the anonymous map while retaining curation** (FR-013). Add i18n keys to all four locales

---

## Phase 6 — US4: A visitor converts [US4]

**Goal**: a visitor who sees an example has a direct route to creating their own.

- [x] T021 [US4] Link the anonymous popup CTA to registration in `static/index.html`, landing the visitor somewhere that lets them create a rule set (spec P3 acceptance criteria)

---

## Final Phase — Polish

**Required before any deploy** — T024 in particular is not optional.

- [x] T022 [P] Update `.ai/context/architecture.md`: new `is_showcase` column in the SQLite table list, the two new routes in API Contracts, the public map cache alongside the replay cache section
- [x] T023 [P] Add the milestone to `.ai/context/features.md`, and update `README.md` if its route list is affected (Constitution #5 — human-readable docs are derived from `.ai/`)
- [x] T024 **Bump the version in `pyproject.toml`.** Phases 3–6 change `static/`, and the version *is* the cache key — `pages.py` cache-busts with `?v=<app-version>` and `main.py` serves versioned assets `immutable, max-age=1y`. Shipping changed assets without a bump pins stale files in browsers **for a year**

---

## Notes

**Per-task spec files.** `001-review-remediation` gave each task its own standalone `T##-*.md`,
paste-ready for a cheaper model. Not reproduced here: this feature's detail already lives in
`plan.md`, `research.md`, `data-model.md` and `contracts/`, and each task above cites the exact file
and the constraint it serves. Ask if you want them split out for a task-at-a-time workflow.

**The three tasks that carry the real risk**, if attention is scarce:

- **T003 + T006** — the D4 gate. Get either wrong and a private rule set reaches the open internet.
- **T007** — the no-data omission. Get it wrong and the shop window lies to visitors, confidently.
- **T005** — the narrow payload. Get it wrong and every visitor learns who owns each rule set.
