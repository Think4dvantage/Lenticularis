# Feature History & Backlog

## Current Version: v1.23.2 (shipped)

### Fix: phantom rows blanked 15 hours of tomorrow's forecast; gap-fill across model runs

Follow-up to v1.23.1. The owner pushed back on "no data": *lsmfapi reports data from now until
115 h into the future.* Checked directly against the running `lsmfapi` container — **they were
right, and the bug was ours.**

lsmfapi returns 115 hourly entries (h+0 → h+115), but **15 carry null wind values**:
`2026-08-07T01:00Z`–`15:00Z`, i.e. h+19–h+33 from the `06Z` init, with values resuming exactly at
h+34. That is the **ICON-CH1 → CH2 seam** — CH1's horizon ends at h+33 and CH2 takes over at h+34.
(Note: this is *not* the h+8–h+33 hole documented for the thermal-grid endpoint; different endpoint,
different mechanism.)

**Our bug — phantom rows.** `write_forecast` skipped null weather fields (correct) but wrote
`init_time` **unconditionally**. So an all-null frame still produced a point containing *only*
`init_time`. Proven in prod at `2026-08-07T14:00Z` for `holfuy-1808`:

| run | fields written | wind_direction |
|---|---|---|
| `2026-08-06T00` | **22** | **194.0** |
| `2026-08-06T06` | **1** (`init_time` only) | — |

That one-field row is newer, so it won every "newest wins" dedup and shadowed a complete row. The
`06Z` run wrote only **9 of 24** hours for tomorrow, and the other 15 read as `None` even though the
previous run had them.

| Change | Detail |
|---|---|
| `write_forecast` / `write_thermal_forecast` | Skip the point entirely when every weather field is null — no more phantom rows |
| `_merge_forecast_candidates` — new | Shared per-field merge: order candidates by `(preferred source, newest init_date, tiebreak)`, take each field from the first with a non-`None` value. Newer run wins; older run fills **only** its gaps |
| `_recent_forecast_init_dates` / `_forecast_init_date_filter` — new | One Flux `init_date` predicate builder for **all three** readers, so arrows and decisions can never consider different runs |
| All three readers rewired | `query_forecast_replay`, `query_forecast_snapshot_for_stations`, `query_forecast_for_stations`. Replaced the latter's bespoke four-branch dedup, which picked one winning row per `valid_time` and so blanked an hour rather than falling back |
| Response shapes preserved | Each reader strips the provenance keys it never carried (`init_date` for the horizon reader; `source`/`model`/`init_date` for replay) |
| `FORECAST_RUN_FALLBACK_DEPTH = 3` | **Measured, not guessed** — every run's hole ends at its *own* h+33, so consecutive runs have overlapping but offset holes and one step back is not enough |
| `tests/backend/test_influx_query_clients.py` | +9 tests: per-field merge precedence, gap backfill, "fills only gaps, never overwrites a newer value", all-null → hour omitted, source preference vs newer other-source, all three readers sharing one filter, bounded depth. Suite: 236 → **245** |

**Verified against prod before tagging** (read-only, new logic run inline):

| | |
|---|---|
| hours recovered (were blank, now have data) | **15 / 15** |
| readers disagreeing (arrow vs horizon vs snapshot) | **0 / 20** |
| coverage by depth | depth 1 → 0/15 · depth 2 → **6/15** · depth 3 → **15/15** |
| replay cost (1 day, all stations) | 1 run 1.46 s · 2 runs 3.05 s · 3 runs 4.40 s · 4 runs 6.33 s |

The read-side merge tolerates phantom rows already in InfluxDB, so **the fix is retroactive** — no
backfill or cleanup needed.

Resulting Amisbühl forecast for tomorrow (local): 07:00 orange, 09:00 **green**, 10:00 orange,
11:00–17:00 red (gusts 18–25 km/h exceed every arc's cap), 18:00 **green**, then red. A believable
morning window rather than a uniformly blank day.

## Previous Version: v1.23.1 (shipped)

### Fix: forecast snapshot served a stale model run, contradicting the map arrows (`database/influx.py`)

Reported live right after the v1.23.0 deploy: "Amisbühl oben" read red/orange when replaying
tomorrow, even though the station's wind arrow pointed into the green arc. Root-caused against
prod data — and **v1.23.0 was not the cause; it exposed a pre-existing bug.**

`query_forecast_snapshot_for_stations` was serving a **four-day-old model run**
(`init_date=2026-08-02T18`) while the map's arrows showed the current one:

| path | init_date used | dir @ 2026-08-07 14:00Z |
|---|---|---|
| arrow (`query_forecast_replay`) | latest (`2026-08-06T00`) | 194° |
| precompute (`query_forecast_for_stations`) | latest | 194° ✅ agrees |
| snapshot (`query_forecast_snapshot_for_stations`) | **oldest retained** (`2026-08-02T18`) | **171°** ❌ |

`init_date` is a *tag*, so every model run is its own series: `|> last()` returned one record
**per run**, not the newest run. The Python tiebreak then kept "whichever entry has the most
fields" — every run carries the same fields, so a strict `>` never replaced the first row seen,
and Flux returns tables in ascending tag order. Net effect: **the oldest retained run won.** The
code comment claimed "newest init_time written last", which is not what that query does.

Why v1.23.0 surfaced it: before then, replay marker colours came from this snapshot query (stale,
171° → green). v1.23.0 routed them through the precomputed horizon, which uses
`query_forecast_for_stations` (fresh, 194° → red). The decision changed because it started being
**correct** — the green had been computed from a 4-day-old forecast that disagreed with the arrow
the pilot was looking at.

| Change | Detail |
|---|---|
| `query_forecast_snapshot_for_stations` — init_date filter | Now restricts the pivot to the newest run per source via `_latest_forecast_init_dates()`, the **same** selection `query_forecast_replay` uses. Arrow/decision agreement is now structural, not coincidental. Same 3-day fallback when no run landed in 12 h |
| `|> last()` **removed** | It was the mechanism behind the stale pick. A docstring warning records why it must not come back |
| Explicit selection precedence | `(preferred source, newest init_date, closest reading to valid_time)` — replaces "whichever row arrived first". Naive `valid_time` is normalised to UTC |
| Two-step, like replay | Cheap `init_date` lookup on the fast (10 s) client; the pivot stays on the slow (60 s) client — the v1.22.2 timeout protection is unchanged |
| `tests/backend/test_influx_query_clients.py` | +8 tests incl. the exact prod failure (stale row first → newest must still win), row-order independence, source preference, and **snapshot and replay selecting the same `init_date`**. Suite: 228 → **236** |

**Verified against prod before tagging** (read-only, new logic run inline): arrow-vs-old
mismatched **11 of 15** flyable hours; arrow-vs-new mismatched **0 of 15**. The precompute path was
separately confirmed at **0 divergences** from the arrow, so it needed no change — the newest run
writes rows even for null frames, so it wins the dedup rather than back-filling from an older run.

⚠️ **Amisbühl stays red for tomorrow, and that is correct.** The current forecast gives 194°
(outside the 90–180° green arc) with gusts ~25 km/h (the orange arcs cap gust at 15, green at 20).
Separately, the `2026-08-06T06` run has **null wind_direction for 2026-08-07 06:00–15:00Z** — the
documented lsmfapi h+8…h+33 null hole. Those hours legitimately show no arrow and evaluate red;
that is upstream data absence, not a Lenticularis bug.

## Previous Version: v1.23.0 (shipped)

### Reactive Ruleset Evaluation (`specs/009-reactive-ruleset-evaluation`)

Ruleset decisions were recomputed on a fixed `IntervalTrigger(minutes=10)` regardless of whether
any station data had changed — Holfuy delivers every 5 min, so a decision could be 10 min stale.
Separately, forecast decisions were **never** cached: `run_forecast_evaluation_at` re-queried
InfluxDB on every call, including once per replay frame during Play. v1.22.6 made that query 135×
faster but left the "recompute on every read" architecture intact. This feature fixes both.

| Change | Detail |
|---|---|
| `rules/reactive.py` — new | `affected_ruleset_ids(db, station_ids, virtual_members)` — one `SELECT DISTINCT` reverse lookup over `rule_conditions` (`station_id` **or** `station_b_id`), never a per-station loop. Plus the FR-007 in-flight guard (`try_claim`/`release`, module-level set + lock, self-draining so no bound needed) |
| **Whole-cluster station expansion** | Expansion runs in **both** directions — reported id → canonical **and** canonical → all members. A one-way member→canonical map would have been enough for today's data, but canonicality is priority-ranked (`meteoswiss > … > jfb`), so adding a higher-priority station near an existing one *moves* the canonical id and leaves older conditions pointing at what is now a member. Those rule sets would have silently stopped being re-evaluated |
| `scheduler.evaluate_rulesets(station_ids=None, trigger=…)` | Generalised from `_run_ruleset_evaluator`. `None` = boot pass (FR-008); a set = only the rule sets depending on those stations. **Now runs via `asyncio.to_thread`** — the old version did blocking SQLite + InfluxDB work directly on the event loop, which mattered far more once it fires per collector run instead of every 10 min |
| `scheduler.evaluate_rulesets_forecast(...)` — new | Recomputes the **entire** horizon per affected rule set and stores it via `write_decisions_batch(..., measurement="rule_decisions_forecast")`. Station set defaults to the scheduler's own registry — a forecast collector has no `get_stations()`, unlike an observation collector |
| `rule_decisions_forecast` — new InfluxDB measurement | Same tags/fields as `rule_decisions`, timestamped at `valid_time`. Kept separate so a future-hour forecast decision can never collide with the observed decision later recorded for that same hour |
| `write_decisions_batch` gains `measurement=` | Defaults to `rule_decisions`, so the history-backfill caller is untouched |
| `query_forecast_decisions_for_ruleset` — new | Read side. **Passes an explicit `stop:`** — Flux defaults it to `now()` and every point here is at a *future* timestamp, so omitting it returns nothing silently. Plain `r.ruleset_id == "…"`, never `contains()` |
| `GET /api/rulesets/{id}/evaluate?forecast=true` | Reads the precomputed decision (nearest stored hour within ±30 min) and **falls back** to the live `run_forecast_evaluation_at` on a miss — a rule set created between two forecast runs must still resolve |
| Fixed 10-min job **removed** (FR-005) | Both the `add_job(IntervalTrigger(minutes=10), id="collector_ruleset_evaluator")` registration **and** the separate `call_later(… _trigger_now("collector_ruleset_evaluator"))` startup kick. The boot pass moved to `main.py`'s lifespan |
| Hooks **composed, never replaced** | `on_collector_run` / `on_forecast_run` are single callable slots, not listener lists — a second assignment silently discards the registry updater. `_compose_collector_hook` / `_compose_forecast_hook` wrap the existing callbacks |
| Health entry kept as `ruleset_evaluator` | `interval_minutes: None`, `status: "reactive"`, plus `last_affected_count`. `stats.html` renders interval generically (`… : '—'`) and nothing references the key by name, so **no frontend or i18n change** |
| `tests/backend/test_reactive_evaluation.py` — new | 31 tests. Suite: 197 → **228** |

**`no_data_stations` on a cache hit is derived, not stored** — `write_decisions_batch` persists only
`decision` + `condition_results`, and `run_forecast_evaluation` does not produce per-step no-data
lists. The router reconstructs it as "every condition referencing this station came back with
`actual_value is None`", which reproduces the live path in the case that matters (a station missing
from the forecast snapshot entirely).

**Known limitation, carried from plan §4.2**: a full-horizon rewrite relies on last-write-wins per
`(tags, time)`. If a later model run's horizon is *shorter* than an earlier one's (lsmfapi's
h+8–h+33 null hole), the orphaned hours are never overwritten and linger stale. `weather_forecast`
has the identical gap today.

**Deploy note**: no SQLite schema change, no frontend change, no new config key. A new InfluxDB
measurement appears on first forecast run after deploy. Version bumped to 1.23.0.

## Previous Version: v1.22.6 (shipped)

### Fix: catastrophic `contains()` slowdown in forecast-snapshot queries (`database/influx.py`)

Reported live: during map replay ("go to tomorrow, hit Play"), ruleset marker colours never
kept up with the wind-arrow animation — Play advances a frame every ~600ms, but each
`loadRulesetMarkers` batch was taking **7-10 seconds**. Traced to
`query_forecast_snapshot_for_stations` (powers `run_forecast_evaluation_at`, called once per
frame per ruleset whenever replay is scrubbed into forecast time).

Measured directly against InfluxDB: the same ±30-minute, single-station query against
`weather_forecast` took **9,338 ms** with `contains(value: r.station_id, set: [...])` and
**69 ms** with an OR-chain of `r.station_id == "..."` — a **135x** difference. Root cause:
`weather_forecast`'s per-hour `init_date` tag fragments it into a huge number of series over
time (infinite retention, ~4 model runs/day since v1.15), and `contains()` cannot use the tag
index to skip non-matching series the way a direct equality filter can.
`query_forecast_for_stations` already used the OR-chain style; this method (and two others
targeting the same measurement family) just hadn't been fixed to match.

Fixed in three places — the only ones confirmed to target the affected high-cardinality
measurements (`weather_forecast` / `weather_forecast_thermal`); the other five `contains()`
call sites in `influx.py` target `weather_data`, measured fast, left untouched:
- `query_forecast_snapshot_for_stations`
- `query_thermal_forecast_snapshot_for_stations` (specs/006, not yet user-facing — fixed
  proactively before it could reproduce the same bug once Phase 2 ships)
- `query_foehn_pressure_history`'s forecast leg (`_flux_pressure` against `MEASUREMENT_FORECAST`)

`tests/backend/test_influx_query_clients.py` — 3 new tests asserting the generated Flux string
uses the OR-chain, not `contains()`, for all three.

**Follow-up raised, not built here**: forecast decisions are never precomputed — every replay
frame re-queries InfluxDB live, even though `run_forecast_evaluation` already computes a
ruleset's entire horizon in one query. Worth a proper caching/precompute pass so Play doesn't
depend on per-frame query latency at all. Tracked in backlog.

## Previous Version: v1.22.5 (shipped)

### Fix: ARM64 Docker build failure (`Dockerfile`)

`v1.22.4`'s tag push triggered a `docker-publish.yml` build that failed on the `linux/arm64`
(QEMU-emulated) leg only — `amd64` was unaffected. Root cause confirmed via `git diff v1.22.3
v1.22.4`: **zero** dependency or Dockerfile changes between the two tags, only the version
string — so this is a pure infra flake, not something introduced by the v1.22.4 code change.

`Dockerfile:1` pins `python:3.11-slim` as a floating tag, and `Dockerfile:22-24` deliberately
re-runs `poetry lock` fresh on every build (no committed lockfile). Between the v1.22.3 build
(12:22 UTC) and v1.22.4's (21:14 UTC), the base image or PyPI moved under us: the bundled `pip`
hit a known upstream bug — `user_agent()` crashes with `TypeError` when `setuptools`'s version
metadata is unreadable — triggered specifically when Poetry shells out to `pip uninstall
cryptography` to swap in the arm64 wheel.

Fix: `RUN pip install --no-cache-dir --upgrade pip setuptools` added before Poetry is installed,
so the buggy bundled pip never runs. `v1.22.4`'s tag was left as-is (its build failed before any
image was pushed to ghcr.io, so nothing needed rolling back) — this ships as `v1.22.5`.

## Previous Version: v1.22.4 (tag pushed, build failed — see v1.22.5)

### Fix: unmet GREEN requirement overrode a legitimately matched other group (`rules/evaluator.py`)

Reported live in prod: a launch site ("Amisbühl oben") with four wind-direction-arc groups —
one GREEN (the ideal 90-180° arc) and three ORANGE fallback arcs for other directions — read
**red** even when the wind was calm and squarely inside one of the ORANGE arcs, with that arc's
own (stricter) speed/gust thresholds satisfied. The v1.20.0 "green is a requirement" fail-safe
(`evaluator.py`) unconditionally injected a `"red"` vote whenever a green unit failed to match,
regardless of whether a *different* group in the same ruleset had already legitimately matched —
so `worst_wins` always picked red over the correctly-matched orange, no matter what.

Root-caused by reproducing the exact live conditions (direction ~9°, calm wind) against the
real stored ruleset — and confirmed **not** a replay bug: the plain live `/evaluate` endpoint
(no `at_time`) showed the identical wrong result.

Fix: the green fail-safe now only contributes red when **nothing else** in the ruleset
classified the current conditions — preserving the original spec-004 behaviour for a lone
green-only rule (still red, no other classification exists) while letting a genuinely matched
other group stand. Applied identically across the three decision blocks that duplicate this
logic (`run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`) — `_evaluate_from_station_data`
is the fourth and canonical copy; `run_forecast_evaluation_at` and `run_history_backfill` delegate
to it and needed no change.

`tests/backend/test_rules_evaluator.py` — 3 new tests on the canonical function.
`tests/backend/test_unmet_green_precedence.py` — new, 3 tests covering the other three
duplicated blocks directly, since that duplication is exactly how a fix like this could land in
one copy and not the others.

**Separately identified, not a code bug**: the paired landing ruleset ("Höhematte") had zero
saved conditions — a ruleset with no conditions always reads green by design. No failed-save
error appears anywhere in server logs; the conditions were simply never submitted. Needs
manual reconfiguration in the ruleset editor, not a fix here.

## Previous Version: v1.22.3 (shipped)

### Thermal Forecast — Phase 1: Ingestion (`specs/006-thermal-forecast`)

Backend-only, no user-facing surface yet. Ingests lsmfapi's `/api/forecast/thermal-grid`
(solar, sunshine, cloud cover, freezing level, CAPE/CIN, LCL, LFC, TKE, ~4×/day, 120h horizon)
and derives seven pilot-facing metrics, ready for Phase 2 (rules + station-detail panel) and
Phase 3 (map layer) — neither built yet.

| Change | Detail |
|---|---|
| `services/thermal.py` — new | 7 pure functions: `thermal_ceiling_m`, `cloud_base_agl_m`, `thermal_strength` (0-5), `overdevelopment_risk` (0-3), `blue_thermal`, `turbulence_index` (0-3), `ceiling_spread_m`. `cin=None` means no inhibition layer, substituted as `0.0` — never a cap |
| `collectors/forecast_thermal_swissmeteo.py` — new | One HTTP request (28.6 MB / 4.1s) covers every station and the whole grid. Nearest-grid-point mapping via `haversine_m()`. Per-field, per-frame null handling — never assumes a full frame or a full horizon is populated |
| `weather_forecast_thermal` — new InfluxDB measurement | Own measurement, not merged into `weather_forecast` — the two forecasts have different `init_time`s and would break `query_forecast_snapshot_for_stations`'s dedup |
| `forecast_thermal` scheduler job | Hourly, no Open-Meteo fallback (none exists for thermal). Re-collection guard skips the write when `(init_time, model, usable_frame_count)` is unchanged — the model refreshes ~4×/day but the job runs hourly |
| **Phase 1 exit gate is coverage, not row count** | lsmfapi is known to null frames h+8…h+33 on some runs — squarely on today's and tomorrow's flyable hours. The collector logs `coverage today=%d/12 d1=%d/12 (local 08-19, non-null solar)` every run; Phase 2/3 do not proceed until both days show real coverage |
| `tests/backend/test_thermal_derived.py`, `test_thermal_collector.py` | 63 + 9 tests. Suite: 96 → 188 |

**Not shipped yet, tracked in the same spec**: rules-engine integration (`FieldName`/`FIELD_MAP`,
6 merge sites), station-detail thermal panel, `/thermal-forecast` map page. See
`specs/006-thermal-forecast/tasks.md` Phases 2-4.

## Previous Version: v1.22.2 (shipped)

### Fix: forecast snapshot query timing out under concurrent load (`query_forecast_snapshot_for_stations`)

Observed in prod (`sdh` host): two genuine ~10s InfluxDB read-timeouts on
`query_forecast_snapshot_for_stations` within a 30-minute window, each causing that forecast
evaluation to spuriously report **red** via the v1.20.0 no-data fail-safe rule — not because
conditions were actually unmet, but because the station's forecast data didn't come back before
the default 10s client gave up. Correlates with specs/archive/008's viewport-preload requests
hitting InfluxDB concurrently with replay/forecast scrubbing, and specs/archive/007's
`run_forecast_evaluation_at` calling this query more often than before.

Fix: switched to `_slow_query_api` (60s timeout), the same client `query_forecast_replay` and
`query_forecast_accuracy_ranking` already use for exactly this reason.
`tests/backend/test_influx_query_clients.py` — new regression test asserting the slow client is
used, not the fast one.

## Previous Version: v1.22.1 (shipped)

### Fix: cross-ruleset condition-group id collision on save (`PUT /api/rulesets/{id}/conditions`)

`ruleset-editor.html`'s `makeCondGroup()` mints group ids from a per-session counter
(`'g' + (++groupSeq)`) that resets to `0` every time the editor opens fresh. Two
different rule sets therefore routinely send the same client-minted ids (`g1`, `g2`, ...).
`ConditionGroup.id` is a global primary key across all rule sets, so saving a *second*
rule set whose groups reused an id already on disk for a different rule set hit an
uncaught `IntegrityError: UNIQUE constraint failed: condition_groups.id` — surfaced to
the pilot as a bare 500 with no explanation.

Fix: `replace_conditions` now mints fresh server-side UUIDs for group ids and remaps
`RuleCondition.group_id` through that mapping, the same pattern `/clone` already used.
Client-supplied group ids are now purely a same-request correlation token, never a
literal primary key.

## Previous Version: v1.22.0 (shipped)

Specced and planned in `specs/archive/008-progressive-map-loading/`.

### Viewport-First Progressive Loading & Geolocation Centering (`specs/archive/008`)

The map always fetched and rendered every station regardless of what was on screen, and always opened
centered on Interlaken. This feature makes rendering viewport-aware and lets the map open centered on
the pilot instead.

| Change | Detail |
|---|---|
| **No API/cache change** | `GET /api/stations` and `GET /api/stations/replay` already return every station in one atomic payload, and the day-offset progressive-load sequence (`[1, 0, 2, -1, 3, -2, 4, -3, 5]`) already existed in both the client prefetch loop and `warm_replay_cache`. A bounding-box query parameter was considered and rejected — it would fragment the shared, cross-pilot `_replay_cache` into one entry per viewport |
| **Viewport-first rendering** | `_renderStationsViewportFirst()` (`map.js`) places on-screen stations immediately, defers the rest via chunked `requestIdleCallback`. Applied to `loadStations()`, `applyReplaySnapshot()`, and the 60s live refresh. Ruleset markers (specs/archive/007) untouched |
| **Pan/zoom re-prioritization** | `moveend` listener promotes newly-visible stations out of the still-pending deferred queue, without duplicating already-placed markers |
| **Geolocation centering** | First visit (or any prior grant) attempts `navigator.geolocation` non-blocking, after the map is already painted at Interlaken/zoom-11; recenters via `map.setView()` if resolved. `localStorage['lenti_geo_pref']` remembers only an explicit denial — a timeout/unavailable-position leaves a prior grant to retry next visit. New "center on me" control (mirrors `_PersonalToggle`) retries regardless of stored preference |
| i18n ×4 | `map.geolocate_button` |

**Deploy note**: `static/map.js` + i18n changed → `pyproject.toml` bumped to 1.22.0 (asset cache key).
No SQLite/InfluxDB schema change, no new query parameters, no data sent to the backend.

## Previous Version: v1.21.0 (shipped)

Specced and planned in `specs/archive/007-replay-aware-ruleset-decisions/`.

### Replay-Aware Ruleset Decisions (`specs/archive/007`)

The map's time-navigation bar already re-draws wind arrows for any scrubbed day/hour or ▶ Play
animation, but launch/landing/opportunity markers always showed **today's live** decision regardless
of what moment was on screen — scrubbing to yesterday left the wind arrow and the decision dot
disagreeing. This feature makes ruleset markers follow the same replay moment.

| Change | Detail |
|---|---|
| **New evaluator function** | `run_forecast_evaluation_at(ruleset, influx, valid_time)` — single-`valid_time` forecast lookup, the forecast counterpart to `run_evaluation_at`. Built on the already-existing `InfluxClient.query_forecast_snapshot_for_stations` (previously only used by `GET /api/foehn/forecast`), and calls `_evaluate_from_station_data` directly instead of duplicating the decision block a fifth time |
| **`GET /api/rulesets/{id}/evaluate` gains `forecast: bool = False`** | Only meaningful with `at_time` set. `false` (default, unchanged) = observed data via `run_evaluation_at`. `true` = forecast data for that `valid_time` via the new function. The caller states the mode explicitly — the map already knows which mode applies to the frame it's displaying (`ReplayEngine.isForecastFrame`) |
| **Landing-halo fix** | Linked landing rulesets (the launch-site halo colour) are now evaluated in the **same** `at_time`/`forecast` mode as the primary rule set, via a shared `_evaluate_at()` router helper. Previously always live regardless of the primary rule set's mode — invisible until this feature made the map actually call `evaluate?at_time=` |
| **Frontend: coalescing refresh queue** | `loadRulesetMarkers(atTime, isForecast)` hooked into `_mapReplay`'s `onFrame` callback. A single-slot coalescing queue (not a fixed debounce) keeps at most one batch of `/evaluate` calls in flight regardless of Play's 600 ms/frame cadence; a generation counter discards a superseded batch's results instead of rendering a stale timestamp's decision |
| **Existing 60 s live poll gated** | `if (window._lentiIsTimeNavLive())` — previously unconditional, which would have silently flipped markers back to live every minute while scrubbed away from Now, the moment markers became replay-aware |
| `tests/backend/test_forecast_evaluation_at.py`, `test_evaluate_at_time.py` | New evaluator function parity with `_evaluate_from_station_data`; no-data fail-safe; API-level forecast-param routing; landing-halo regression test |

**Deploy note**: `static/index.html` changed → `pyproject.toml` bumped to 1.21.0 (asset cache key).
No SQLite/InfluxDB schema change, no response-shape change.

## Previous Version: v1.20.1 (shipped)

### Fix: duplicate condition-group id on re-edit (`ruleset-editor.html`)

`makeCondGroup()` mints new group ids as `'g' + (++groupSeq)`. `groupSeq` was only ever
reset to `0` in `applyPreset()` — `loadEdit()` (opening an existing rule set) restored the
stored `condition_groups` without touching it. So editing a rule whose groups were already
`g1`/`g2`/`g3` and clicking "Add Condition Group" minted `g1` again, and the save request
was rejected with `duplicate group id(s): g1`.

Fix: `loadEdit()` now scans the restored `condition_groups` for ids matching `gN` and
advances `groupSeq` past the highest `N` found, so ids minted afterward can't collide.

## Previous Version: v1.20.0 (shipped)

Specced and planned in `specs/archive/004-green-requirement-semantics/`.

### Green Conditions Are Requirements (`specs/archive/004`)

Fixes a decision flaw: a launch/landing site whose only rule was a positive GREEN confirmation
(e.g. "wind direction in the usable arc → green") read **green 100% of the time**, including when the
direction was dead wrong. A GREEN condition only fires when it matches, so when it did *not* match,
nothing triggered and the site fell back to the "benefit of the doubt" green default — the opposite of
the pilot's intent.

| Change | Detail |
|---|---|
| **GREEN is now a requirement** | For launch/landing, a GREEN unit (standalone GREEN condition, or an AND group whose effective `_worst` colour is green) that does **not** trigger contributes `red`. One `elif` per decision block; the existing `worst_wins` / `majority_vote` / green-default logic then produces the right answer unchanged |
| **No-data fails safe** | "Not triggered" folds no-data into threshold-failure (`_eval_condition` returns `(False, …)` for both), so an unconfirmable GREEN requirement → **red** with no special branch. `no_data_stations` still lists the station so the pilot sees *why* (D3) |
| **Exception semantics preserved** | RED/ORANGE contribute only when matched; a rule set built only from exceptions still defaults to green on a calm day. The benefit-of-the-doubt default survives for exception-only sets, and only there |
| **Opportunity untouched** | Gated on `site_type != "opportunity"` — opportunity already forces red when not all units trigger; the rule would double-count and could flip its `< total_units` guard |
| **Mixed groups stay exception-style** (D2) | Only a unit whose effective colour is green is a requirement, mirroring how `worst_wins` collapses a group to one colour |
| Applied to all four decision blocks | `_evaluate_from_station_data`, `run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`. The four-way duplication is flagged as a follow-up cleanup, not refactored here |
| `help.html` + i18n ×4 | Green = requirement rewritten (colour meanings, result-colour guidance, worst-wins section); `combination_hint` reworded in en/de/fr/it |
| `tests/backend/test_rules_evaluator.py` | +8 cases: unmet green → red, no-data green → red, landing variant, all-green group unmet → red, mixed group stays green, exception-only calm → green, opportunity guard |

**Deploy note** — behavioural change, not a drop-in restart of logic:
- **Decisions change for any launch/landing rule set that contains a GREEN condition.** They change in
  the pilot's intended direction (a required green now gates the site), but a site that used to read
  green whenever its green condition was unmet will now read red. Exception-only rule sets are
  unaffected.
- Public map: no-data sets are already dropped before evaluation, so no false red reaches anonymous
  visitors.
- `help.html` changed → `pyproject.toml` bumped to 1.20.0 (asset cache key).

## Previous Version: v1.19.0 (shipped)

Two features, specced and planned in `specs/archive/002-public-rulesets/` and
`specs/archive/003-condition-group-names/`.

**Deploy notes** (this release is not a drop-in restart):
- The `condition_groups` backfill runs at container startup against real data.
  It is designed not to touch a single `rule_conditions` row — take a DB copy anyway.
- The anonymous map is **empty until rule sets are curated**. `is_showcase` defaults to
  FALSE by design; nothing becomes public as a side-effect of deploying. Curate with
  `PUT /api/rulesets/{id}/set_showcase?is_showcase=true` (admin; 409 if the owner has
  not published it). There is no admin UI for this yet.

### Public Rule Sets on the Map (`specs/archive/002-public-rulesets`)

Visitors who are not signed in now see curated example rule sets with live traffic lights, and a
prompt to sign up. Signed-in pilots additionally see other people's published rule sets, except at
sites they have already configured.

| Change | Detail |
|---|---|
| `rulesets.is_showcase` — new column | Admin curation, independent of the owner's `is_public` and of `is_preset`: three flags, three different people's decisions. Guarded `ALTER TABLE`, default FALSE — nothing becomes public as a side-effect of the migration |
| **Owner consent gates curation** | Anonymous visibility is the read-time conjunction `is_showcase AND is_public`. `set_showcase(true)` on an unpublished rule set → **409**. Un-publishing hides but does **not** clear curation, so re-publishing restores it with no admin action. `is_public=false, is_showcase=true` is a legitimate state |
| `services/public_map.py` — new | One `query_latest_for_stations()` for **every** station across **all** rule sets, then `_evaluate_from_station_data()` in memory. `run_evaluation()` batches only within one rule set, so looping it would have been an unauthenticated N+1 (constraint T09) |
| **No-data rule sets omitted** | The evaluator returns green when nothing triggers, including on no data. Defensible for a pilot who sees `no_data_stations`; a lie to a visitor. Omitted entirely rather than shown as a confident green |
| `PublicRuleSetMarker` | id, name, lat, lon, site_type, decision — and nothing else. Deliberately not derived from `RuleSetOut`, which carries `owner_display_name` and would have leaked owner identity by default |
| 500 m proximity suppression | Signed-in only, reusing `haversine_m()` from `services/dedup.py`. Per-viewer, so never served from the anonymous cache |
| `api/routers/public.py` — new | `/api/public` — the only unauthenticated surface. 60 s shared cache with a poisoning guard (an empty build is never cached, so a transient Influx failure cannot blank the map for the TTL) |
| `static/index.html` | The ruleset layer was entirely inside `if (isLoggedIn())`; now branches by auth state |
| `tests/backend/test_public_rulesets.py` | 17 tests — the D4 gate, no-data omission, owner-field leakage, one-Influx-call batching, cache isolation between viewers, 500 m boundary |

### Named Condition Groups (`specs/archive/003-condition-group-names`)

Condition groups can be named, so a pilot returning to a rule set remembers which risk each group
guards against.

| Change | Detail |
|---|---|
| `condition_groups` — new table | `id`, `ruleset_id`, `name` (nullable = never named), `sort_order`. The table `models.py:253` had predicted since the beginning |
| Backfill migration | One unnamed group per distinct existing `group_id`, **reusing the id as the row's primary key** — so no `rule_conditions` row is touched and the evaluator's buckets are byte-for-byte identical. Decisions provably cannot move |
| **Evaluator decision logic untouched** | Groups are still bucketed from conditions, never iterated from rows. That keeps an empty group inert by construction, and makes "decisions unchanged" true by construction rather than something to prove. Iterating rows would reach `_worst([])` → `ValueError` and kill the rule set's evaluation |
| `group_name` on `ConditionResult` | Output enrichment only, via `_group_names()`. Lets a decision be explained as "Föhn risk" instead of a list of numbers — this is what the tooltip rework will consume |
| Fail-closed save | `ConditionsReplaceRequest.groups` is validated: any condition pointing at an absent group → 422. A permissive default would have let a caller omitting `groups` silently delete every name the pilot typed |
| Atomic replace | Groups and conditions are replaced together, with a `flush()` between deletes and inserts — the editor re-sends the same group ids, and SQLAlchemy emits INSERTs before DELETEs within a flush |
| **Clone remap** | `clone_ruleset` copied `group_id` verbatim (`rulesets.py:597`). Harmless while `group_id` was an opaque marker; once groups are rows owned by a `ruleset_id`, the clone pointed at the **source's** groups — renaming the source would rename the copy. Now remapped through `{old: new}` |
| `static/ruleset-editor.html` | Name input per group; groups rebuilt from stored rows rather than inferred from `group_id` collisions; a *named* box is a real group at any size; empty groups render as empty containers; deleting a group with conditions asks first |
| `tests/backend/test_condition_groups.py` | 20 tests — backfill idempotency, decisions identical across the migration, empty-group inertness, one-condition equivalence, fail-closed validation, clone independence |

### Fixed along the way

| Fix | Detail |
|---|---|
| **422 handler crashed on any `ValueError` validator** | `main.py`'s `RequestValidationError` handler passed `exc.errors()` straight to `JSONResponse`. Pydantic v2 puts the **exception object** in `ctx.error`, which is not JSON serialisable — so the handler 500'd while reporting a 422. Pre-existing and latent: `WebcamBase._http_only` raises `ValueError` the same way and no test had ever posted a bad URL. Fixed with `jsonable_encoder` |
| `FakeInflux.query_decision_history` | Missing stub, exactly the failure `06-testing-conventions.md` warns about |

## Previous Version: v1.18.2 (shipped)

Patch: the Stations overview now lists **Jungfraubahn** as a filterable network, and the
`jfb` badge is styled (dark-red) on every page that renders network badges (`stations`,
`index` map popups, `station-detail`, `forecast-accuracy`, `forecast-analysis`) instead of
falling through to grey `unknown`. Frontend-only; no backend change.

## Previous Version: v1.18.1 (shipped)

Everything unreleased since v1.17: the security & performance remediation batch, the
Forecast Accuracy Analysis page, the Jungfraubahn collector, the test-harness repair, and
self-hosted frontend libraries + static-asset caching.

### Static Asset Caching + Self-Hosted Libraries (v1.18.1)

| Change | Detail |
|---|---|
| `static/vendor/` — new | Leaflet 1.9.4 (`leaflet.js`, `leaflet.css`) and Chart.js v4 (`chart.umd.min.js`, `chartjs-adapter-date-fns.bundle.min.js`) vendored locally. **All CDN references removed** — no page loads from `unpkg.com` or `cdn.jsdelivr.net` any more. |
| `.gitattributes` | `static/vendor/** -text` — vendored assets must stay byte-exact; never line-ending-normalised. |
| `api/main.py` — CSP tightened | `script-src` / `style-src` reduced to `'self' 'unsafe-inline'`; the `unpkg.com` and `cdn.jsdelivr.net` allowances are gone now that nothing is loaded cross-origin. |
| `api/main.py` — `Cache-Control` on `/static/` | Versioned URLs (`?v=<app-version>`) → `public, max-age=31536000, immutable`. Unversioned hits (locale JSON, ES-module imports) → `public, max-age=600`. |
| `api/routers/pages.py` — cache-busting | Every local `href="/static/…"` / `src="/static/…"` in a page is rewritten at serve time to append `?v=<app-version>`. A version bump busts every asset atomically on deploy. |
| `api/routers/pages.py` — ETag | HTML is served `no-cache` with an ETag (`<version>-<mtime>`) and revalidates to `304`. HTML is re-read per request, so dev volume-mount edits stay live. |
| `tests/backend/test_static_caching.py` | 6 tests: `no-cache` + ETag on HTML, 304 revalidation, assets versioned, no CDN refs remain, immutable on versioned assets, short cache on unversioned, vendored libs actually served. |

Note: Leaflet's default `marker-icon.png` / `layers.png` are **not** vendored, and do not
need to be — every marker in the app uses `L.divIcon` or `L.circleMarker`, and no
`L.control.layers` is used, so those files are never requested.

### Jungfraubahn (JFB) Observation Collector

13 stations in the Jungfrau region from the Jungfraubahn middleware API. No auth, one
request per cycle, 10 min interval. Zero schema change — every kept field maps onto the
existing `WeatherMeasurement`.

| Change | Detail |
|---|---|
| `collectors/jfb.py` — new | `JfbCollector(BaseCollector)`, `NETWORK = "jfb"`. Single JSON call returns all stations. Reuses `to_float` / `normalize_wind_dir` from `collectors/utils.py`. |
| Field mapping | `FF`→`wind_speed`, `G10`→`wind_gust` (both **knots → km/h, ×1.852**), `DIR`→`wind_direction`, `TL`→`temperature`, `RH`→`humidity`, `QFE`→`pressure_qfe`. |
| Dropped params | `TD` / `DIFFTD` (derivable from `TL`+`RH`); `G1h` (1-hour max gust — different semantics from `wind_gust`, which is the 10-min peak everywhere else in the stack). |
| `pressure_qff` left `None` | QFF is **not** derivable from QFE + elevation (that is QNH). Synthesising it would inject several hPa of error at these altitudes — larger than the föhn gradients it would be compared against. Same choice as `fga.py`. |
| New wind stations | `jfb-lauberhorn` (2315 m), `jfb-wengen-dorf` (1278 m), `jfb-wengen-lauberhorn-ziel` (1285 m) — the Wengen/Lauterbrunnen bowl, previously covered only by Jungfraujoch 8 km away. |
| New atmosphere stations | Eiger (3955 m), Mittellegihütte, Hollandiahütte SAC, Kleine Scheidegg, Grütschalp, Grindelwald-Moos, Jungfrau-Ostgrat, Jungfraujoch, Lauterbrunnen-Gässli, Lauterbrunnen-Heliport — an 799 m → 3955 m elevation ladder within ~10 km. |
| Excluded stations | `Interlaken` and `Jungfraujoch-Sphinx` skipped on ingest — exact duplicates of `meteoswiss-INT` / `meteoswiss-JUN` with fewer fields and coordinates rounded to 2–3 decimals (so 50 m proximity dedup would not catch them). |
| `scheduler.py` / `services/dedup.py` | Registered in `_COLLECTOR_REGISTRY`; `"jfb"` appended to `NETWORK_PRIORITY` (lowest — MeteoSwiss wins any future proximity clash). |
| `tests/backend/test_jfb_collector.py` | 15 tests: knots conversion, direction normalisation, dropped params, exclusions, timestamp reconstruction + midnight rollover, staleness skip, `currentDateTime` always sent. |

**API quirks (must not regress):**
- **`currentDateTime` is mandatory.** Called bare, the endpoint returns observations
  ~8 h stale with no error. The collector always sends `?currentDateTime=<now>`.
- `timeUTC` is time-only (`"11:30"`, no date) — reconstructed against the request date,
  with midnight rollover. Readings older than 2 h are skipped.
- **Known upstream data bug:** `Hollandiahütte SAC` declares elevation 3248 m but reports
  ~928 hPa / 23 °C (a ~750 m reading). Their metadata or sensor mapping is wrong. Do not
  build on that station's pressure or temperature.

---

### Backend Test Harness — two broken fixtures fixed

| Change | Detail |
|---|---|
| `tests/backend/conftest.py` — `poolclass=StaticPool` | In-memory SQLite defaulted to `SingletonThreadPool` = one connection (and one empty DB) per thread. `create_all()` only populated the main thread's. Sync deps (`get_current_user`, `require_pilot`, `require_admin`) run in FastAPI's worker threadpool → `no such table: users`. `async def` handlers were unaffected, which made it look arbitrary. |
| `tests/backend/conftest.py` — `app.state` set directly | httpx's `ASGITransport` never emits ASGI lifespan events, so `lifespan_context` never ran and `app.state.influx` was never set → `503 InfluxDB not available`. State is now assigned directly; the no-op lifespan is kept so the real scheduler/InfluxDB startup still cannot fire. |
| `tests/backend/conftest.py` — `FakeInflux.query_latest_all_stations` | Missing stub, previously masked by the 503. |
| `.ai/instructions/06-testing-conventions.md` | Corrected — it documented both broken patterns as the correct approach. |

Backend suite: **59 passed, 0 failed** (was 57 passed, 2 failed).

---

### Forecast Accuracy Analysis Page (NOT YET VERIFIED — awaiting first successful data load)

| Change | Detail |
|---|---|
| `static/forecast-analysis.html` + `forecast-analysis.js` | New `/forecast-analysis` page. Lead-time toggle D+1/D+2/D+3. Per-field ranked tables of worst-forecast stations (MAE + bias + correction hint). Colour-coded MAE cells. Station names link to `/forecast-accuracy?station=X`. |
| `database/influx.py` — `query_forecast_accuracy_ranking` | New method. Queries 90d actuals (aggregateWindow 1h) + forecasts; Python-side join keyed on hourly timestamp; circular error for wind_direction; MAE + bias per station×field×bucket. |
| `database/influx.py` — `_ranking_client` | Third InfluxDB client with `ranking_query_timeout` (default 300 s). Used only for ranking queries — avoids 60 s `_slow_query_api` timeout that was silently killing the actuals query. |
| `config.py` — `ranking_query_timeout` | `InfluxDBConfig` gains `ranking_query_timeout: int = 300000`. |
| `api/routers/stations.py` — `GET /api/stations/forecast-accuracy-ranking` | 30-min server-side cache. `force_refresh=true` param bypasses cache. `warm_accuracy_ranking_cache()` function. |
| `api/main.py` | Startup warm-up task + page route `/forecast-analysis`. |
| `scheduler.py` | 24h `IntervalTrigger` job re-warms ranking cache. |
| All 13 HTML files | Forecast Analysis nav link added. |
| `i18n/en+de+fr+it.json` | `forecast_analysis.*` keys added. |

See `.ai/context/forecast-analysis-wip.md` for full debug history and next steps.

---

### Security & Performance Remediation Batch

23-task security and performance pack (`specs/archive/001-review-remediation/`). All tasks complete.

| Phase | Tasks | Summary |
|---|---|---|
| Security | T01–T05 | Flux injection hardening; JWT fail-closed; webcam URL validation + XSS escaping; security-header + CORS middleware; OAuth tokens out of URL + `email_verified` check |
| Performance | T06–T11 | GZip middleware; async event-loop unblocking in Influx handlers; scheduler writes offloaded via `asyncio.to_thread`; rule evaluator batches Influx fetch; bounded in-memory caches with lock; SQLite WAL + `busy_timeout=30000` |
| Architecture | T12–T19 | Typed error envelope + global exception handlers (labelled "RFC 7807" at the time — a misnomer; see `07-api-conventions.md`); pages router extracted from `main.py`; scheduler post-run hooks (replaces monkey-patching); Alembic dependency dropped; version single-sourced; SQLAlchemy 2.0 `select()` style; swallowed-exception fixes; InfluxDB duplicate field key + `_source` dedup no-op fixed |
| Quality | T20–T23 | Pytest harness + GitHub Actions CI; collector `_to_float`/wind-dir/concurrency dedup (`collectors/utils.py`, `base._collect_concurrent`); frontend nav/bootstrap dedup (`static/bootstrap.js`, `static/shared.css`); remaining hardcoded strings → i18n keys |

Key new files: `api/errors.py`, `api/routers/pages.py`, `collectors/utils.py`, `static/bootstrap.js`, `tests/backend/conftest.py + 4 test files`, `.github/workflows/test.yml`.

---

## Previous Version: v1.17 (shipped)

### Replay fix, collector scheduling overhaul, stats table improvements

| Change | Detail |
|---|---|
| `static/index.html` — `tnPlayHours()` | Always returns all 13 hours `[7…19]` for every day offset. Previously returned only `[8,11,14,17]` for offset ≥ 2 (CH2 3-hourly mode). Removed now that lsmfapi delivers hourly 120h. |
| `static/index.html` — `tnStartPlay()` | Frame speed fixed at 600 ms for all days (was 1000 ms for 4-hour mode). |
| `collectors/forecast_swissmeteo.py` — parallel collect | Added `collect_all_iter` override using `asyncio.gather` — all stations fetched in parallel. lsmfapi is co-located, no rate limits. Replaces serial `BaseForecastCollector.collect_all_iter`. |
| `collectors/forecast_base.py` — source override | `__init__` reads optional `source` key from config dict and overrides class-level `SOURCE` tag. Useful if two instances of the same collector class need distinct source tags. |
| `scheduler.py` + `config.py` — `cron_hours` | Added optional `cron_hours: list[int]` to `ForecastCollectorConfig`. When set, uses `CronTrigger(hour=...)` instead of `IntervalTrigger`. Kept as a supported feature but not used in production (hourly interval preferred). |
| Collector scheduling — hourly | Both swissmeteo station collector and wind forecast grid collector changed from cron `04/10/16/22Z` to `IntervalTrigger(minutes=60)`. lsmfapi updates ~4×/day; no-op runs are harmless. |
| `config.yml` / `config.yml.example` | `open-meteo` and `open-meteo-short` set `enabled: false`. `swissmeteo` uses `interval_minutes: 60`, `cron_hours` removed. |
| `database/influx.py` — `write_forecast_grid` chunked | Grid write now batches at 5000 pts per InfluxDB call (was one call for all points). Fixes read-timeout crash when writing 1.17M points (~234 chunks × 5k pts). |
| `static/stats.html` — collector table | Added "Records" column (`last_measurement_count` — points written in last run, e.g. `243 pts`). Fixed "Schedule" column: shows `04/10/16/22Z` for cron collectors, `N min` for interval. i18n keys `col_interval` renamed to "Schedule"/"Zeitplan"/etc.; `col_records` added to all 4 locales. |

---

## Previous Version: v1.16 (shipped)

### SwissMeteo lsmfapi Integration — Full Stack Fix

| Change | Detail |
|---|---|
| `collectors/forecast_swissmeteo.py` — parser fix | lsmfapi response schema corrected: `generated_at` → `init_time`, `hours[]` → `forecast[]`, EnsembleValue dicts → flat float fields with `_min`/`_max` suffixes. `_probable()`/`_ens()` helpers removed. Helpers `_f()` / `_i()` read flat fields directly. `collect_altitude_for_station()` method **removed** — lsmfapi has no per-station altitude endpoint. |
| `collectors/forecast_grid_swissmeteo.py` — new | `ForecastGridSwissMeteoCollector`: fetches lsmfapi `/api/forecast/grid?level_m=X` for all 8 altitude levels **in parallel** (no rate limiting — same Docker network). 1272 ICON-CH1 grid points; `grid_id = f"{lat:.4f}_{lon:.4f}"` (4 decimal places for non-round native coordinates). Falls back to Open-Meteo grid only when lsmfapi returns 0 wind points. |
| `scheduler.py` — grid collector | `_run_grid_forecast_collector` tries `ForecastGridSwissMeteoCollector` first; fallback to `ForecastGridCollector` (Open-Meteo). Logs source used (`source: swissmeteo` or `source: open-meteo`). |
| `scheduler.py` — altitude collector removed | `_run_swissmeteo_altitude_collector` and the companion APScheduler job registration removed. lsmfapi has no per-station altitude endpoint; altitude wind data comes from the grid map. |
| `scheduler.py` — status fix | `_run_forecast_collector`: when `total_points == 0` and `errors > 0`, status is `error` (not `ok_no_data`). `ok_no_data` reserved for no eligible stations. |
| `models/weather.py` | `StationWindProfilePoint` class **removed** — measurement no longer written or queried. |
| `database/influx.py` — dead code removed | `MEASUREMENT_WIND_PROFILE` constant and `write_station_wind_profile()` method removed. |
| `database/influx.py` — dual InfluxDB clients | `InfluxClient` now has `_query_api` (10s timeout, standard queries) and `_slow_query_api` (60s timeout, `query_forecast_replay` only). Configured by new `InfluxDBConfig.slow_query_timeout` (default 60000 ms). |
| `database/influx.py` — `query_forecast_replay` two-step | Replaced slow full-scan + Flux `group→sort→limit` with two-step approach: (1) `_latest_forecast_init_dates()` — fast scan of `range(-12h)`, single field, returns `{source: latest_init_date}` in milliseconds; (2) main pivot filtered to exact `init_date` per source — processes only 1–2 model runs instead of 72+. Cuts query time from 62 s timeout to ~1–2 s. |
| `database/influx.py` — `query_forecast_replay` field filter | Excludes `_min`/`_max` and `init_time` fields — map replay only needs central values. Cuts data volume ~3× vs full SwissMeteo schema. |
| `database/influx.py` — `query_forecast_replay` pivot fix | Added `init_date` to pivot `rowKey` (was missing, causing Flux to silently drop rows when multiple model runs had the same station/valid_time). |
| `database/influx.py` — `query_forecast_snapshot_for_stations` | Added `source` and `init_date` to pivot `rowKey` (same structural fix). |
| `api/routers/wind_forecast.py` — dynamic grid | Router no longer imports or uses `GRID_POINTS` from `forecast_grid.py`. Grid is built dynamically from whatever `grid_id` tags are in InfluxDB — works for both Open-Meteo (171 pts, 2 dp) and lsmfapi (1272 pts, 4 dp) without code changes. |
| `config.py` | `InfluxDBConfig` gains `slow_query_timeout: int = 60000`. |

---

## Previous Version: v1.15.1 (shipped)

### Incremental improvements

| Change | Detail |
|---|---|
| Wind forecast grid — humidity | `GridForecastPoint` gains `humidity: Optional[float]`. Collector fetches `relative_humidity_<N>hPa` from Open-Meteo alongside wind vars. InfluxDB `wind_forecast_grid` stores `humidity` field. API frames include `rh` parallel array. Frontend: cloud icon on arrows when `rh ≥ 90`; arrows clickable with popup showing lat/lon, ws, wd, rh. |
| Station-detail tooltip — zone-aware filter | Replaced `mode: 'index'` with `mode: 'x'` in `CHART_DEFAULTS`. `makeForecastFilter` hides obs items in forecast zone and vice versa. |
| Station-detail tooltip — ensemble min/max | `(min)`/`(max)` band anchors filtered from tooltip; values appended inline on probable line. Lookup by timestamp match, not `dataIndex`. |

---

## Previous Version: v1.15 (shipped)

### SwissMeteo Forecast Integration

| Change | Detail |
|---|---|
| `collectors/forecast_swissmeteo.py` | `ForecastSwissMeteoCollector(BaseForecastCollector)`: SOURCE="swissmeteo", MODEL="icon-ch". Calls lsmfapi per station. Full ensemble `_min`/`_max` fields on `ForecastPoint`. |
| `models/weather.py` — ensemble fields | `ForecastPoint` extended with optional `_min`/`_max` for all weather fields. |
| InfluxDB `weather_forecast` — `init_date` | Tag format `YYYY-MM-DDTHH` — one series per model-run hour. |
| Source priority + 24h fallback | `query_forecast_for_stations()`: prefer `swissmeteo` when ≤24h old; else latest-init-date wins. |
| Forecast source badge | `GET /api/stations/{id}/forecast` gains `forecast_source` + `forecast_model`. Station-detail shows pill badge. |
| Ensemble band charts | 3-dataset Chart.js pattern: invisible min anchor → semi-transparent max fill → solid probable line. |

---

## Previous Version: v1.14.1 (shipped)

### Hotfix

| Change | Detail |
|---|---|
| QNH → QFF migration | `pressure_qnh` removed entire stack. All pressure as `pressure_qff`. Affects models, collectors, influx, evaluator, frontend, i18n. |

---

## Previous Version: v1.14.0 (shipped)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | MeteoSwiss collector + InfluxDB write pipeline + station API + station-detail chart page |
| v0.2 | Leaflet.js map with station markers and latest-measurement popups |
| v0.3 | JWT register/login, `get_current_user`/`require_admin`, Google OAuth, SQLite via SQLAlchemy |
| v0.4 | Pilot-owned launch site CRUD; site markers on map |
| v0.5 | `collectors/slf.py` (30 min) and `collectors/metar.py` (15 min); full scheduler |
| v0.6 | Ruleset editor — condition builder, AND/OR nesting, direction compass, pressure-delta two-station mode |
| v0.7 | `rules/evaluator.py` live evaluator + `run_forecast_evaluation`; traffic light badges |
| v0.8 | `collectors/forecast_meteoswiss.py` ICON-CH1/CH2 GRIB2; map time-navigation; forecast colour-strip |
| v0.9 | `stats.html` flyability statistics |
| v0.10 | `collectors/wunderground.py`; virtual föhn stations; `foehn.html` |
| v1.0 | Multilanguage EN/DE/FR/IT + mobile-responsive UI |
| v1.1 | Admin panel; collector control; customer role |
| v1.2 | Webcam links; preset launch sites; decision history + forecast API |
| v1.3 | Forecast accuracy dashboard; `init_date` tag; layered forecast schedule; `collect_all_iter` rate-limit spreading |
| v1.4 | Opportunity site type; AI rule suggestions via Ollama |
| v1.5 | Multi-tenant org system; subdomain routing; org-dashboard |
| v1.6 | Help/FAQ page; AI input normaliser; fuzzy station matching; geographic station lookup |
| v1.7 | Holfuy collector; forecast replay prefetch cache (client-side TTL 10 min) |
| v1.8 | Replay performance: server-side in-memory TTL cache (5 min); startup warm-up; `aggregateWindow(30m)` |
| v1.9 | Virtual station deduplication (union-find, 50 m GPS, manual overrides, `display_registry`) |
| v1.10 | Replay cache correctness: `_patch_scheduler_forecast` post-collection invalidation + rewarm; cache-poisoning guard |
| v1.11 | Google OAuth login; opportunity ruleset fix |
| v1.12 | Rules engine improvements; UTC→local time; backtester; 30-day backfill; email notifications |
| v1.13 | Föhn Tracker rework; delta/trend conditions; per-user config; `foehn_active` field in ruleset editor |
| v1.14.0 | Ruleset Gallery + FGA/Meteo Oberwallis collector (9 stations, DMS coordinates, XML) |
| v1.14 | Wind Forecast Grid Map (`wind-forecast.html`); `ForecastGridCollector`; `GridForecastPoint`; `GET /api/wind-forecast/grid`; commercial Open-Meteo API key support |

---

## Backlog (unordered)

### Thermal Forecast (lsmfapi thermal-grid endpoint) — **planned, ready to implement**, `specs/006-thermal-forecast`

Plan **and** tasks written: `specs/006-thermal-forecast/plan.md` + `tasks.md`, targeting
**v1.22.2 → v1.23.0**. Plan revised 2026-08-02 (it was authored against v1.20.0, before 007/008
shipped). All design decisions are closed — see plan §11. **Read the spec folder, not this entry**,
for the current design; the notes below are kept only for original rationale.

Key fields: `solar` (W/m²), `lcl` (cloud base m ASL), `lfc`, `freezing_level`, `cape`, `cin`,
`cloud_cover`, `cloud_mid`, `tke`, `sunshine`, all with ensemble `_min`/`_max`. 120-hour horizon,
~4×/day refresh, `stride_km=10`.

Three things the 2026-08-02 review established that are easy to lose:

1. **The thermal grid and `wind_forecast_grid` are the same 1272 cells** — verified in the lsmfapi
   source: shared default bbox, same stride, character-identical point-generation code. They join on
   `(grid_id, valid_time)`, so wind × thermal composition is free in both the rules engine and the
   map (plan §3.4, §6.9).
2. **Payload is 28.6 MB / 4.1 s at `stride_km=10`**, not the ~0.5 MB this entry used to claim.
   One request covers every station and the whole map — never fan out per-station.
3. **Upstream currently nulls h+8…h+33** — from a 00Z init that is today 08:00 through tomorrow
   09:00, i.e. the entire flyable window. Phase 1's exit gate is a *coverage* check, not a row
   count (plan §10.1); if the hole is still open, Phases 2–3 are on hold.

**Observed counterpart (from the JFB collector):** the JFB stations form an 799 m → 3955 m
elevation ladder within ~10 km (Lauterbrunnen-Heliport 799, Lauterbrunnen-Gässli 856, Grindelwald-Moos
1267, Grütschalp 1469, Kleine Scheidegg 2071, Mittellegihütte 3340, Eiger 3955 — all reporting
temperature + humidity). That gives a **measured vertical temperature profile**, i.e. a real lapse
rate, and temperature−dewpoint spread converts directly to observed cloud base (~125 m per K) — the
observed counterpart to forecast `lcl`, and a viable way to score thermal forecast accuracy through
the existing `/forecast-analysis` machinery. Recorded as a follow-up spec in plan §12, not scheduled.

Caveat: **exclude `jfb-hollandiahutte-sac`** — it declares 3248 m but reports ~928 hPa / 23 °C
(a ~750 m reading). Upstream metadata/sensor bug, confirmed in the raw payload.

### Shelved

- **InfluxDB 2.7 → 3 migration** (`specs/005-influxdb3-migration`) — spec + plan complete, not
  proceeding to `tasks.md`. Blocked on a licensing/monetisation decision (D1 in the spec): InfluxDB 3
  Core (free) can't serve Lenti's 90-day/365-day query patterns, and Enterprise requires resolving
  whether `lenti.cloud` is commercial use first. Reactivate when that question closes. **D2
  (unresolved, added 2026-08-02)**: the user recalls a prior decision to target Postgres/TimescaleDB
  instead of InfluxDB 3, for the same licensing/long-term-support reasons — not found recorded
  anywhere. Re-decide the engine (D2) before reactivating on the old InfluxDB-3-only framing.

### Platform Features

- **Org statistics page** — `/org/{slug}/stats`
- **Customer role scoped access** — read-only rulesets assigned by admin
- **Trusted users + field condition reports** — `is_trusted`, `weather_reports` table, map pins
- **AI weather analysis** — Ollama/Claude compares reports vs station data; `ai_insights` table
- **Push notifications (FCM)** — `fcm_tokens` table; `services/push_fcm.py`
- ~~**Email alerts**~~ — shipped in v1.12
- **Flutter mobile app** — separate repo `lenticularis-app`
- **OGN live glider overlay** — toggleable Leaflet layer, WebSocket proxy
- **OGN launch statistics** — detect takeoffs from OGN tracks
- **xcontest correlation** — correlate flight dates with ruleset decision history
- **Club area overlay** — toggleable GeoJSON polygon layer
- ~~**Duplicate station handling**~~ — shipped in v1.9
- **Wind rose chart** — replace direction scatter on station-detail
- **Performance pass** — InfluxDB query profiling; downsampling for data >90 days
- **Auto-clone preset on nearby site creation**
- **lsmfapi grid — add `ws_min`/`ws_max`, `wd_min`/`wd_max`, `vw`/`vw_min`/`vw_max`** — extend `/api/forecast/grid` response with ensemble spread + vertical wind so the wind forecast map can show ensemble bands and vertical wind component

### Tech debt (discovered v1.20.0)

- **Deduplicate the evaluator decision block** — the condition/group bucketing + combination logic is
  copied verbatim across `_evaluate_from_station_data`, `run_evaluation`, `run_evaluation_at`, and
  `run_forecast_evaluation` in `rules/evaluator.py`. Any decision-logic change (e.g. the v1.20.0
  green-requirement rule) must be made in all four. Route the live/snapshot/forecast paths through the
  shared core so it lives once. See `specs/archive/004-green-requirement-semantics/plan.md` follow-up.
- **Fix `pyproject.toml` `requires-python`** — it is `"^3.11"`, a Poetry caret that is invalid in a
  PEP 621 `[project]` table. `ruff check` cannot parse the file (CI hides this with
  `continue-on-error: true`); run `ruff check --isolated` to lint until fixed. Change to
  `">=3.11,<4.0"`; verify the Docker build still resolves before relying on it.
